"""The owner's USDC in Arc Earn vaults (ERC-4626), and the ShopEarn contract on Arc that lets the
agent park idle USDC into approved vaults and unpark it back to the owner's wallet when an invoice
needs it.

All authoritative state (the owner's shares, their current USDC redemption value via
convertToAssets, maxWithdraw, share allowances, and ShopEarn limits) is read directly from the
contracts on Arc. Every park or unpark is simulated first on the Arc node as the agent and then
sent with an idempotency key through the agent's Circle wallet.
"""

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from services.circle import CircleClient, CircleError
from services.payments import ArcRpc, ChainError, Payer
from services.payments.encoding import USDC_DECIMALS, usdc_units

# ShopEarnFactory deployed on Arc Testnet (contracts/src/ShopEarnFactory.sol).
EARN_FACTORY = "0xEDF589982F10CCC3c8489726FaB39124b1F683Aa"
ARC_USDC = "0x3600000000000000000000000000000000000000"

# Minimum USDC kept liquid in the owner's wallet on Arc so the owner always has native USDC to pay
# gas when signing dashboard actions in MetaMask.
MIN_WALLET_CUSHION = Decimal("1")

FOLLOW_SECONDS = 60
POLL_SECONDS = 2


@dataclass(frozen=True)
class VaultSpec:
    """An ERC-4626 USDC vault verified on Arc Testnet."""

    key: str  # short name used by the agent, e.g. "morpho" or "earnkit"
    name: str
    address: str
    share_decimals: int


VAULTS: tuple[VaultSpec, ...] = (
    VaultSpec(
        key="morpho",
        name="Morpho USDC Vault",
        address=to_checksum_address("0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc"),
        share_decimals=6,
    ),
    VaultSpec(
        key="earnkit",
        name="EarnKit USDC Vault",
        address=to_checksum_address("0xaabbef1d3971c710276ed41ec791bbe14cdb8e88"),
        share_decimals=18,
    ),
)


def _usdc(units: int) -> Decimal:
    return Decimal(units).scaleb(-USDC_DECIMALS)


@dataclass(frozen=True)
class EarnPosition:
    """The owner's position in one ERC-4626 vault on Arc."""

    key: str
    name: str
    vault: str
    shares: int
    balance_usdc: Decimal  # convertToAssets(shares) in USDC
    max_withdraw_usdc: Decimal  # maxWithdraw(owner) in USDC
    approved_in_earn: bool  # ShopEarn.approvedVault(vault)
    shares_approved: bool  # vault.allowance(owner, shopEarn) >= shares and > 0


@dataclass(frozen=True)
class ShopEarnState:
    """The owner's ShopEarn contract state and positions across all known Arc Earn vaults."""

    in_wallet: Decimal
    earn: str | None  # the owner's ShopEarn contract on Arc; None until created
    usdc_approved: Decimal = Decimal(0)  # USDC allowance from owner to ShopEarn
    max_per_park: Decimal = Decimal(0)
    weekly_cap: Decimal = Decimal(0)
    left_this_week: Decimal = Decimal(0)
    paused: bool = False
    agent_is_set: bool = False
    vaults: tuple[EarnPosition, ...] = ()
    liquidity_strategy: str = "preserve_yield"

    @property
    def total_in_earn(self) -> Decimal:
        return sum((v.balance_usdc for v in self.vaults), Decimal(0))

    @property
    def can_park(self) -> Decimal:
        """How much USDC the agent may park right now, leaving MIN_WALLET_CUSHION in the owner's
        wallet so the owner is never left without gas on Arc."""
        if not self.earn or self.paused or not self.agent_is_set:
            return Decimal(0)
        if not any(v.approved_in_earn for v in self.vaults):
            return Decimal(0)
        spendable = max(Decimal(0), self.in_wallet - MIN_WALLET_CUSHION)
        return min(spendable, self.usdc_approved, self.max_per_park, self.left_this_week)

    def can_unpark(self, key_or_address: str) -> Decimal:
        """How much USDC the agent can unpark from a specific vault right now."""
        if not self.earn or self.paused or not self.agent_is_set:
            return Decimal(0)
        pos = self.find_vault(key_or_address)
        if pos is None or not pos.shares_approved or pos.shares == 0:
            return Decimal(0)
        return pos.balance_usdc

    def find_vault(self, key_or_address: str) -> EarnPosition | None:
        target = key_or_address.strip().lower()
        for v in self.vaults:
            if v.key.lower() == target or v.vault.lower() == target or v.name.lower() == target:
                return v
        return None


def resolve_vault(key_or_address: str, vaults: Sequence[VaultSpec] = VAULTS) -> VaultSpec:
    target = key_or_address.strip().lower()
    for spec in vaults:
        if (spec.key.lower() == target or spec.address.lower() == target
                or spec.name.lower() == target):
            return spec
    known = ", ".join(f"{v.key} ({v.name})" for v in vaults)
    raise ChainError(f"unknown Earn vault {key_or_address!r}; available vaults: {known}")


class OwnerEarn:
    """Reads the owner's ShopEarn and ERC-4626 vaults on Arc, and parks or unparks the owner's
    USDC through ShopEarn with the agent's Circle wallet."""

    def __init__(
        self,
        payer: Payer,
        *,
        factory: str = EARN_FACTORY,
        usdc: str = ARC_USDC,
        vaults: Sequence[VaultSpec] = VAULTS,
        strategy: Callable[[], str] = lambda: "preserve_yield",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.payer = payer
        self._factory = to_checksum_address(factory) if factory else ""
        self._usdc = to_checksum_address(usdc)
        self._vaults = tuple(vaults)
        self._strategy = strategy
        self._sleep = sleep

    @property
    def chain(self) -> ArcRpc:
        return self.payer.chain

    @property
    def circle(self) -> CircleClient:
        return self.payer.circle

    def _view(self, to: str, sig: str, types: Sequence[str] = (),
              args: Sequence[Any] = ()) -> bytes:
        return self.chain.call(to, keccak(text=sig)[:4] + encode(list(types), list(args)))

    def read(self) -> ShopEarnState:
        owner = self.chain.owner_of(self.payer.shop)
        agent = self.payer.agent_address()
        return self.read_for(owner, agent)

    def read_for(self, owner: str, agent: str) -> ShopEarnState:
        owner = to_checksum_address(owner)
        agent = to_checksum_address(agent)
        (in_wallet,) = decode(["uint256"], self._view(self._usdc, "balanceOf(address)",
                                                      ["address"], [owner]))
        earns: list[str] = []
        if self._factory:
            (earns,) = decode(["address[]"], self._view(self._factory, "earnsOf(address)",
                                                        ["address"], [owner]))
        earn = to_checksum_address(earns[-1]) if earns else None
        positions = tuple(self._read_vault(spec, owner, earn) for spec in self._vaults)
        if earn is None:
            return ShopEarnState(
                in_wallet=_usdc(in_wallet),
                earn=None,
                vaults=positions,
                liquidity_strategy=self._strategy(),
            )
        (usdc_allowed,) = decode(["uint256"], self._view(
            self._usdc, "allowance(address,address)", ["address", "address"], [owner, earn]))
        (per_park,) = decode(["uint256"], self._view(earn, "maxPerPark()"))
        (weekly,) = decode(["uint256"], self._view(earn, "weeklyCap()"))
        (left,) = decode(["uint256"], self._view(earn, "remainingThisWeek()"))
        (paused,) = decode(["bool"], self._view(earn, "paused()"))
        (its_agent,) = decode(["address"], self._view(earn, "agent()"))
        return ShopEarnState(
            in_wallet=_usdc(in_wallet),
            earn=earn,
            usdc_approved=_usdc(usdc_allowed),
            max_per_park=_usdc(per_park),
            weekly_cap=_usdc(weekly),
            left_this_week=_usdc(left),
            paused=paused,
            agent_is_set=to_checksum_address(its_agent) == agent,
            vaults=positions,
            liquidity_strategy=self._strategy(),
        )

    def _read_vault(self, spec: VaultSpec, owner: str, earn: str | None) -> EarnPosition:
        vault = to_checksum_address(spec.address)
        (shares,) = decode(["uint256"], self._view(vault, "balanceOf(address)",
                                                   ["address"], [owner]))
        if shares > 0:
            (assets,) = decode(["uint256"], self._view(vault, "convertToAssets(uint256)",
                                                       ["uint256"], [shares]))
        else:
            assets = 0
        try:
            (max_withdraw,) = decode(["uint256"], self._view(vault, "maxWithdraw(address)",
                                                             ["address"], [owner]))
        except ChainError:
            max_withdraw = assets
        if earn is None:
            return EarnPosition(
                key=spec.key, name=spec.name, vault=vault, shares=shares,
                balance_usdc=_usdc(assets), max_withdraw_usdc=_usdc(max_withdraw),
                approved_in_earn=False, shares_approved=False,
            )
        (approved_vault,) = decode(["bool"], self._view(earn, "approvedVault(address)",
                                                        ["address"], [vault]))
        (share_allowance,) = decode(["uint256"], self._view(
            vault, "allowance(address,address)", ["address", "address"], [owner, earn]))
        shares_ok = share_allowance >= shares and share_allowance > 0
        return EarnPosition(
            key=spec.key, name=spec.name, vault=vault, shares=shares,
            balance_usdc=_usdc(assets), max_withdraw_usdc=_usdc(max_withdraw),
            approved_in_earn=approved_vault, shares_approved=shares_ok,
        )

    def park(self, vault_name: str, amount: Decimal) -> dict[str, Any]:
        """Park `amount` of the owner's USDC on Arc into `vault_name` through ShopEarn."""
        spec = resolve_vault(vault_name, self._vaults)
        state = self.read()
        if state.earn is None:
            raise ChainError("the owner has not set up Earn yet (dashboard: Settings > Earn)")
        if state.in_wallet - amount < MIN_WALLET_CUSHION:
            raise ChainError(f"keep at least {MIN_WALLET_CUSHION} USDC in the owner's wallet on "
                             f"Arc for network fees (wallet has {state.in_wallet} USDC)")
        vault = to_checksum_address(spec.address)
        units = usdc_units(amount)
        call = (keccak(text="park(address,uint256)")[:4]
                + encode(["address", "uint256"], [vault, units]))
        return self._execute("park", state.earn, spec, amount, call)

    def unpark(self, vault_name: str, amount: Decimal | None = None) -> dict[str, Any]:
        """Withdraw `amount` of USDC (or all shares when `amount` is None or >= the vault balance)
        from `vault_name` back to the owner's wallet on Arc through ShopEarn."""
        spec = resolve_vault(vault_name, self._vaults)
        state = self.read()
        if state.earn is None:
            raise ChainError("the owner has not set up Earn yet (dashboard: Settings > Earn)")
        pos = state.find_vault(spec.key)
        if pos is None or pos.shares == 0:
            raise ChainError(f"the owner has no USDC parked in {spec.name}")
        vault = to_checksum_address(spec.address)
        if amount is None or amount >= pos.balance_usdc or amount >= pos.max_withdraw_usdc:
            call = keccak(text="unparkAll(address)")[:4] + encode(["address"], [vault])
            reported_amount = pos.balance_usdc
        else:
            units = usdc_units(amount)
            call = (keccak(text="unpark(address,uint256)")[:4]
                    + encode(["address", "uint256"], [vault, units]))
            reported_amount = amount
        return self._execute("unpark", state.earn, spec, reported_amount, call)

    def _execute(self, kind: str, earn: str, spec: VaultSpec, amount: Decimal,
                 call: bytes) -> dict[str, Any]:
        agent = self.payer.agent_address()
        self.chain.call(earn, call, sender=agent)  # simulate first: reverts say why
        sent = self.circle.execute_contract(
            self.payer.wallet_id, earn, call, idempotency_key=str(uuid.uuid4())
        )
        waited = 0
        while not sent.finished and waited < FOLLOW_SECONDS:
            self._sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            sent = self.circle.get_transaction(sent.id)
        if sent.finished and sent.state != "COMPLETE":
            raise CircleError(f"Earn {kind} on {spec.name} {sent.state.lower()}: "
                              f"{sent.error_reason or 'no reason given'}")
        return {
            "kind": f"earn_{kind}",
            "vault": spec.key,
            "vault_name": spec.name,
            "vault_address": to_checksum_address(spec.address),
            "amount": format(amount.normalize(), "f"),
            "state": sent.state.lower(),
            "tx": sent.tx_hash or "",
            "circle_tx": sent.id,
        }

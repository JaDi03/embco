"""Unit tests for the Arc Earn service (`services/earn/client.py`): reading ShopEarn and ERC-4626
vault positions (both 6-decimal and 18-decimal shares), enforcing the owner's gas cushion, and
simulating + sending park, unpark and unparkAll calls through Circle."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from services.circle import CircleError, CircleTransaction
from services.earn import (
    EARN_FACTORY,
    MIN_WALLET_CUSHION,
    VAULTS,
    EarnPosition,
    OwnerEarn,
    ShopEarnState,
)
from services.earn.client import ARC_USDC, resolve_vault
from services.payments.chain import ChainError, Reverted, revert_reason

OWNER = "0x2471658CD7A7B10d4C92113B0777dA59317e839a"
AGENT = "0xeAc91C9491F0D391c80f17C4267A3329Ad7Ba862"
SHOP = "0x" + "5c" * 20
EARN = to_checksum_address("0x" + "e9" * 20)
MORPHO = to_checksum_address(VAULTS[0].address)
EARNKIT = to_checksum_address(VAULTS[1].address)
MAX_UINT = (1 << 256) - 1


def _sel(sig: str) -> bytes:
    return keccak(text=sig)[:4]


class FakeArcChain:
    """Answers eth_call reads and simulations for ShopEarn, ShopEarnFactory, USDC and the two
    ERC-4626 vaults on Arc."""

    def __init__(
        self,
        *,
        in_wallet: int = 800_000_000,
        earn: str | None = EARN,
        usdc_allowance: int = MAX_UINT,
        max_per_park: int = 300_000_000,
        weekly_cap: int = 1_000_000_000,
        remaining: int = 750_000_000,
        paused: bool = False,
        earn_agent: str = AGENT,
        morpho_shares: int = 200_000_000,
        morpho_assets: int = 204_500_000,
        morpho_approved: bool = True,
        morpho_share_allowance: int = MAX_UINT,
        earnkit_shares: int = 100 * 10**18,
        earnkit_assets: int = 101_250_000,
        earnkit_approved: bool = False,
        earnkit_share_allowance: int = 0,
        revert_on_sim: str | None = None,
    ) -> None:
        self.in_wallet = in_wallet
        self.earn = earn
        self.usdc_allowance = usdc_allowance
        self.max_per_park = max_per_park
        self.weekly_cap = weekly_cap
        self.remaining = remaining
        self.paused = paused
        self.earn_agent = earn_agent
        self.morpho_shares = morpho_shares
        self.morpho_assets = morpho_assets
        self.morpho_approved = morpho_approved
        self.morpho_share_allowance = morpho_share_allowance
        self.earnkit_shares = earnkit_shares
        self.earnkit_assets = earnkit_assets
        self.earnkit_approved = earnkit_approved
        self.earnkit_share_allowance = earnkit_share_allowance
        self.revert_on_sim = revert_on_sim
        self.simulated: list[tuple[str, bytes, str]] = []

    def owner_of(self, shop: str) -> str:
        assert shop == SHOP
        return OWNER

    def call(self, to: str, data: bytes, *, sender: str | None = None) -> bytes:
        to = to_checksum_address(to)
        sel = data[:4]
        if sender is not None:
            self.simulated.append((to, data, sender))
            if self.revert_on_sim:
                raise Reverted(self.revert_on_sim)
            return encode(["uint256"], [1])

        if to == to_checksum_address(ARC_USDC):
            if sel == _sel("balanceOf(address)"):
                return encode(["uint256"], [self.in_wallet])
            if sel == _sel("allowance(address,address)"):
                return encode(["uint256"], [self.usdc_allowance])

        if to == to_checksum_address(EARN_FACTORY) and sel == _sel("earnsOf(address)"):
            return encode(["address[]"], [[self.earn] if self.earn else []])

        if self.earn and to == to_checksum_address(self.earn):
            if sel == _sel("maxPerPark()"):
                return encode(["uint256"], [self.max_per_park])
            if sel == _sel("weeklyCap()"):
                return encode(["uint256"], [self.weekly_cap])
            if sel == _sel("remainingThisWeek()"):
                return encode(["uint256"], [self.remaining])
            if sel == _sel("paused()"):
                return encode(["bool"], [self.paused])
            if sel == _sel("agent()"):
                return encode(["address"], [self.earn_agent])
            if sel == _sel("approvedVault(address)"):
                vault = "0x" + data[-20:].hex()
                ok = (to_checksum_address(vault) == MORPHO and self.morpho_approved) or (
                    to_checksum_address(vault) == EARNKIT and self.earnkit_approved
                )
                return encode(["bool"], [ok])

        if to in (MORPHO, EARNKIT):
            is_morpho = to == MORPHO
            shares = self.morpho_shares if is_morpho else self.earnkit_shares
            assets = self.morpho_assets if is_morpho else self.earnkit_assets
            allowance = self.morpho_share_allowance if is_morpho else self.earnkit_share_allowance
            if sel == _sel("balanceOf(address)"):
                return encode(["uint256"], [shares])
            if sel == _sel("convertToAssets(uint256)"):
                return encode(["uint256"], [assets])
            if sel == _sel("maxWithdraw(address)"):
                return encode(["uint256"], [assets])
            if sel == _sel("allowance(address,address)"):
                return encode(["uint256"], [allowance])

        raise AssertionError(f"unexpected eth_call to {to} with selector 0x{sel.hex()}")


class FakeCircle:
    def __init__(self, state: str = "COMPLETE", tx_hash: str = "0x" + "ab" * 32) -> None:
        self.state = state
        self.tx_hash = tx_hash
        self.calls: list[tuple[str, str, bytes, str]] = []

    def execute_contract(self, wallet_id: str, contract: str, data: bytes, *,
                         idempotency_key: str, ref_id: str = "") -> CircleTransaction:
        self.calls.append((wallet_id, contract, data, idempotency_key))
        return CircleTransaction(id="circle-tx-1", state=self.state, tx_hash=self.tx_hash)

    def get_transaction(self, tx_id: str) -> CircleTransaction:
        return CircleTransaction(id=tx_id, state=self.state, tx_hash=self.tx_hash)


def make_earn(chain: FakeArcChain | None = None, circle: FakeCircle | None = None) -> OwnerEarn:
    chain = chain or FakeArcChain()
    circle = circle or FakeCircle()
    payer = SimpleNamespace(shop=SHOP, wallet_id="w-agent", chain=chain, circle=circle,
                            agent_address=lambda: AGENT)
    return OwnerEarn(payer, sleep=lambda _: None)


def test_reads_shop_earn_and_both_6_and_18_decimal_vault_positions():
    state = make_earn().read()
    assert state.earn == EARN
    assert state.in_wallet == Decimal("800")
    assert state.max_per_park == Decimal("300")
    assert state.weekly_cap == Decimal("1000")
    assert state.left_this_week == Decimal("750")
    assert state.agent_is_set is True
    assert state.paused is False
    assert state.total_in_earn == Decimal("305.75")
    # Can park is min(800 - 1 cushion, usdc_approved, 300 max_per_park, 750 left_this_week) = 300
    assert state.can_park == Decimal("300")

    morpho, earnkit = state.vaults
    assert morpho.key == "morpho" and morpho.balance_usdc == Decimal("204.5")
    assert morpho.approved_in_earn is True and morpho.shares_approved is True
    assert state.can_unpark("morpho") == Decimal("204.5")

    assert earnkit.key == "earnkit" and earnkit.balance_usdc == Decimal("101.25")
    assert earnkit.approved_in_earn is False and earnkit.shares_approved is False
    assert state.can_unpark("earnkit") == Decimal("0")


def test_when_owner_has_no_shop_earn_contract_yet_positions_are_still_read():
    state = make_earn(FakeArcChain(earn=None)).read()
    assert state.earn is None
    assert state.can_park == Decimal("0")
    assert state.can_unpark("morpho") == Decimal("0")
    assert state.total_in_earn == Decimal("305.75")


def test_can_park_always_leaves_gas_cushion_in_owner_wallet():
    state = ShopEarnState(
        in_wallet=Decimal("50.5"),
        earn=EARN,
        usdc_approved=Decimal("1000"),
        max_per_park=Decimal("300"),
        weekly_cap=Decimal("1000"),
        left_this_week=Decimal("750"),
        paused=False,
        agent_is_set=True,
        vaults=(EarnPosition("morpho", "Morpho USDC Vault", MORPHO, 0, Decimal(0), Decimal(0),
                             True, True),),
    )
    assert state.can_park == Decimal("50.5") - MIN_WALLET_CUSHION


def test_park_simulates_and_executes_with_idempotency_key():
    chain, circle = FakeArcChain(), FakeCircle()
    earn = make_earn(chain, circle)
    done = earn.park("morpho", Decimal("150.25"))

    assert done == {
        "kind": "earn_park",
        "vault": "morpho",
        "vault_name": "Morpho USDC Vault",
        "vault_address": MORPHO,
        "amount": "150.25",
        "state": "complete",
        "tx": "0x" + "ab" * 32,
        "circle_tx": "circle-tx-1",
    }
    expected_call = _sel("park(address,uint256)") + encode(["address", "uint256"],
                                                           [MORPHO, 150_250_000])
    assert chain.simulated == [(EARN, expected_call, AGENT)]
    wallet_id, contract, data, idem = circle.calls[0]
    assert (wallet_id, contract, data) == ("w-agent", EARN, expected_call)
    assert len(idem) == 36


def test_park_refuses_to_drain_the_owner_gas_cushion():
    chain = FakeArcChain(in_wallet=100_500_000)  # 100.50 USDC in wallet
    earn = make_earn(chain)
    with pytest.raises(ChainError, match="keep at least 1 USDC"):
        earn.park("morpho", Decimal("100"))


def test_unpark_partial_amount_calls_unpark_with_6_decimal_units():
    chain, circle = FakeArcChain(), FakeCircle()
    earn = make_earn(chain, circle)
    done = earn.unpark("morpho", Decimal("80"))

    expected_call = _sel("unpark(address,uint256)") + encode(["address", "uint256"],
                                                             [MORPHO, 80_000_000])
    assert chain.simulated == [(EARN, expected_call, AGENT)]
    assert done["kind"] == "earn_unpark" and done["amount"] == "80"


def test_unpark_full_balance_or_none_uses_unparkAll_to_avoid_rounding_dust():
    chain, circle = FakeArcChain(), FakeCircle()
    earn = make_earn(chain, circle)
    done = earn.unpark("morpho", Decimal("204.5"))

    expected_call = _sel("unparkAll(address)") + encode(["address"], [MORPHO])
    assert chain.simulated == [(EARN, expected_call, AGENT)]
    assert done["kind"] == "earn_unpark" and done["amount"] == "204.5"


def test_simulation_revert_surfaces_shop_earn_error_names():
    for sig, name in [
        ("VaultNotApproved(address)", "VaultNotApproved"),
        ("OverParkLimit(uint256,uint256)", "OverParkLimit"),
        ("ZeroShares()", "ZeroShares"),
        ("ZeroAssets()", "ZeroAssets"),
    ]:
        assert revert_reason(_sel(sig) + b"\x00" * 64) == name

    chain = FakeArcChain(revert_on_sim="VaultNotApproved")
    with pytest.raises(Reverted, match="VaultNotApproved"):
        make_earn(chain).park("morpho", Decimal("10"))


def test_failed_circle_transaction_raises_circle_error():
    circle = FakeCircle(state="FAILED")
    with pytest.raises(CircleError, match="Earn park on Morpho USDC Vault failed"):
        make_earn(circle=circle).park("morpho", Decimal("10"))


def test_resolve_vault_accepts_key_name_or_address_and_rejects_unknown():
    assert resolve_vault("morpho").address == MORPHO
    assert resolve_vault("EarnKit USDC Vault").address == EARNKIT
    assert resolve_vault(MORPHO.lower()).key == "morpho"
    with pytest.raises(ChainError, match="unknown Earn vault"):
        resolve_vault("unknown_vault")


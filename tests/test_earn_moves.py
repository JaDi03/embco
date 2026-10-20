from datetime import UTC, datetime

from eth_abi import encode

from services.earn.client import MOVES_FROM, PARKED, UNPARKED
from services.shops.earn_moves import read_moves, update_moves
from tests.test_earn import AGENT, EARN, MORPHO, OWNER, FakeArcChain, make_earn

# lopezgrocery's real park: 6.69 USDC into Morpho at block 68147778, 20 Oct 07:49:52 UTC
PARK_BLOCK = 68147778
PARK_TX = "0xd18576d1859c451780ee19741eade4700966ecfa870b30f0db91a4c9c79236aa"


def _topic(address: str) -> str:
    return "0x" + "00" * 12 + address[2:].lower()


def _log(topic: str, block: int, assets: int, shares: int, by: str, tx: str) -> dict:
    return {"topics": [topic, _topic(MORPHO), _topic(by)], "blockNumber": hex(block),
            "transactionHash": tx, "data": "0x" + encode(["uint256", "uint256"],
                                                          [assets, shares]).hex()}


class ChainWithLogs(FakeArcChain):
    def __init__(self, logs: list[dict], latest: int) -> None:
        super().__init__()
        self.events, self.latest, self.ranges = logs, latest, []

    def latest_block(self) -> int:
        return self.latest

    def logs(self, address, topics, start, end):
        assert address.lower() == EARN.lower() and end - start < 100_000  # the node range limit
        self.ranges.append((start, end))
        return [e for e in self.events if start <= int(e["blockNumber"], 16) <= end]

    def block_time(self, block: int) -> datetime:
        return datetime(2026, 10, 20, 7, 49, 52, tzinfo=UTC) if block == PARK_BLOCK else (
            datetime(2026, 10, 21, tzinfo=UTC))


def test_moves_are_read_in_node_sized_ranges_and_kept_once(tmp_path):
    events = [_log(PARKED, PARK_BLOCK, 6_690_000, 6_678_699, AGENT, PARK_TX),
              _log(UNPARKED, PARK_BLOCK + 50, 2_000_000, 1_996_000, OWNER, "0xbb")]
    chain = ChainWithLogs(events, latest=PARK_BLOCK + 100)
    update_moves(tmp_path, make_earn(chain))
    assert chain.ranges[0][0] == MOVES_FROM
    moves = read_moves(tmp_path)
    assert [(m["kind"], m["usdc"], m["by"], m["vault"]) for m in moves] == [
        ("in", "6.690000", "agent", "Morpho USDC Vault"),
        ("out", "2.000000", "owner", "Morpho USDC Vault")]
    assert moves[0]["at"] == "2026-10-20T07:49:52+00:00" and moves[0]["tx"] == PARK_TX

    chain.latest += 10
    update_moves(tmp_path, make_earn(chain))  # only the new blocks
    assert chain.ranges[-1] == (PARK_BLOCK + 101, PARK_BLOCK + 110)
    assert len(read_moves(tmp_path)) == 2


def test_a_long_gap_is_caught_up_over_several_checks(tmp_path):
    chain = ChainWithLogs([], latest=MOVES_FROM + 30 * 99_999)
    update_moves(tmp_path, make_earn(chain))
    assert len(chain.ranges) == 20
    update_moves(tmp_path, make_earn(chain))
    assert chain.ranges[-1][1] == chain.latest


def test_a_failed_read_keeps_what_was_saved(tmp_path):
    chain = ChainWithLogs([_log(PARKED, PARK_BLOCK, 6_690_000, 1, AGENT, PARK_TX)],
                          latest=PARK_BLOCK + 1)
    update_moves(tmp_path, make_earn(chain))

    def down(*_):
        from services.payments.chain import ChainError
        raise ChainError("Arc node request failed: ConnectError")
    chain.latest_block = down
    update_moves(tmp_path, make_earn(chain))
    assert len(read_moves(tmp_path)) == 1


def test_no_shop_earn_means_no_moves(tmp_path):
    chain = ChainWithLogs([], latest=MOVES_FROM + 10)
    chain.earn = None
    update_moves(tmp_path, make_earn(chain))
    assert read_moves(tmp_path) == [] and chain.ranges == []

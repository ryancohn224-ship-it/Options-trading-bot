from datetime import date, datetime, timedelta

from trading_agent.snapshot import Snapshot, SnapshotStore, snapshot_from_chain
from tests.conftest import DAY


def _snap(chain, day=DAY, fidelity="live", offset=0):
    return Snapshot(
        session_date=day, as_of=chain.as_of + timedelta(minutes=offset),
        underlying=chain.underlying, expiry=chain.expiry, spot=chain.spot,
        quotes=chain.quotes, fidelity=fidelity, prev_close=599.0, realized_vol=0.09,
    )


def test_a_snapshot_round_trips_through_disk(tmp_path, chain):
    store = SnapshotStore(tmp_path)
    original = _snap(chain)
    store.write(original)
    [restored] = store.load(DAY)

    assert restored.spot == original.spot
    assert restored.fidelity == "live"
    assert restored.realized_vol == 0.09
    assert len(restored.quotes) == len(original.quotes)


def test_quotes_survive_the_positional_encoding(tmp_path, chain):
    store = SnapshotStore(tmp_path)
    store.write(_snap(chain))
    before = {q.symbol: q for q in chain.quotes}
    after = {q.symbol: q for q in store.load(DAY)[0].quotes}

    assert before.keys() == after.keys()
    for symbol, q in before.items():
        r = after[symbol]
        assert (r.strike, r.right, r.bid, r.ask, r.bid_size, r.ask_size) == (
            q.strike, q.right, q.bid, q.ask, q.bid_size, q.ask_size
        )


def test_appending_through_the_day_keeps_every_pass(tmp_path, chain):
    """Each management pass appends a gzip member; none of them overwrite the others."""
    store = SnapshotStore(tmp_path)
    for offset in (0, 15, 30, 45):
        store.write(_snap(chain, offset=offset))
    loaded = store.load(DAY)

    assert len(loaded) == 4
    assert [s.as_of for s in loaded] == sorted(s.as_of for s in loaded)


def test_days_are_discovered_and_sorted(tmp_path, chain):
    store = SnapshotStore(tmp_path)
    for day in (date(2026, 3, 5), date(2026, 1, 9), date(2026, 2, 2)):
        store.write(_snap(chain, day=day))
    assert store.days() == [date(2026, 1, 9), date(2026, 2, 2), date(2026, 3, 5)]


def test_a_missing_day_is_empty_rather_than_an_error(tmp_path):
    store = SnapshotStore(tmp_path)
    assert store.load(date(2026, 1, 1)) == []
    assert store.days() == []
    assert store.fidelity_of(date(2026, 1, 1)) is None


def test_fidelity_is_recorded_per_day(tmp_path, chain):
    store = SnapshotStore(tmp_path)
    store.write(_snap(chain, day=date(2026, 1, 5), fidelity="synthetic"))
    store.write(_snap(chain, day=date(2026, 1, 6), fidelity="live"))
    assert store.fidelity_of(date(2026, 1, 5)) == "synthetic"
    assert store.fidelity_of(date(2026, 1, 6)) == "live"


def test_snapshot_from_chain_rebuilds_an_equivalent_chain(chain):
    snap = snapshot_from_chain(chain, DAY, "live", 599.0, 0.09)
    rebuilt = snap.chain
    assert rebuilt.spot == chain.spot
    assert rebuilt.at_strike(chain.quotes[0].right, chain.quotes[0].strike) is not None


def test_storage_stays_small(tmp_path, chain):
    """Years of this get committed to git, so the per-day cost has to stay trivial."""
    store = SnapshotStore(tmp_path)
    for offset in range(0, 25 * 15, 15):
        store.write(_snap(chain, offset=offset))
    assert store.path_for(DAY).stat().st_size < 200_000

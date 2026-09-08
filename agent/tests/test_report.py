from datetime import date

import pytest

from trading_agent.report import _rank, evaluate_all, render
from trading_agent.research import ResearchLog
from trading_agent.simulate import simulate
from trading_agent.snapshot import SnapshotStore
from trading_agent.variants import DEFAULT_VARIANTS, Variant


@pytest.fixture
def stored(tmp_path, config):
    store = SnapshotStore(tmp_path / "snap")
    days = simulate(store, config, days=30, seed=5)
    return store, days


def test_evaluate_all_scores_every_variant(stored, config):
    store, _ = stored
    runs = evaluate_all(store, config, list(DEFAULT_VARIANTS))
    assert len(runs) == len(DEFAULT_VARIANTS)
    assert all(r.research.days + r.holdout.days == len(store.days()) for r in runs)


def test_research_and_holdout_do_not_overlap(stored, config):
    store, _ = stored
    run = evaluate_all(store, config, [DEFAULT_VARIANTS[0]])[0]
    assert run.research.days > run.holdout.days > 0


def test_a_variant_that_never_trades_does_not_rank_first(stored, config):
    """A gate strict enough to stand down every day scores a flawless +0.000R."""
    store, _ = stored
    never = Variant("never", {"gate": {"min_credit_ratio": 0.99}})
    runs = evaluate_all(store, config, [never, DEFAULT_VARIANTS[0]])
    assert _rank(runs)[-1].variant.name == "never"


def test_the_report_renders_and_names_its_fidelity(stored, config, tmp_path):
    store, days = stored
    text = render(store, config, list(DEFAULT_VARIANTS), ResearchLog(tmp_path / "r"),
                  tmp_path / "journal", days[-1])
    assert "# Trading day report" in text
    assert "synthetic" in text
    assert "cannot" in text.lower() or "not evidence" in text.lower()


def test_the_report_refuses_to_conclude_from_synthetic_days(stored, config, tmp_path):
    store, days = stored
    text = render(store, config, list(DEFAULT_VARIANTS), ResearchLog(tmp_path / "r"),
                  tmp_path / "journal", days[-1])
    assert "Nothing here can change the live configuration" in text


def test_the_report_survives_an_empty_store(tmp_path, config):
    store = SnapshotStore(tmp_path / "empty")
    text = render(store, config, list(DEFAULT_VARIANTS), ResearchLog(tmp_path / "r"),
                  tmp_path / "journal", date(2026, 9, 8))
    assert "No data yet" in text


def test_open_proposals_appear_in_the_report(stored, config, tmp_path):
    store, days = stored
    log = ResearchLog(tmp_path / "r")
    log.propose("wider-wings", "spread cost is per leg", "beats champion by 0.02R")
    text = render(store, config, list(DEFAULT_VARIANTS), log, tmp_path / "journal", days[-1])
    assert "wider-wings" in text and "spread cost is per leg" in text


def test_holdout_openings_are_surfaced(stored, config, tmp_path):
    store, days = stored
    log = ResearchLog(tmp_path / "r")
    for _ in range(3):
        log.record_holdout_look("v", "check")
    text = render(store, config, list(DEFAULT_VARIANTS), log, tmp_path / "journal", days[-1])
    assert "Holdout openings so far: **3**" in text

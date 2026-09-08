from datetime import date, timedelta

import pytest

from trading_agent.replay import ReplayResult
from trading_agent.research import (
    MIN_TRADES_FOR_A_VERDICT, PromotionRules, ResearchLog, bootstrap_ci,
    best_of_k_pvalue, check_promotion, is_holdout, max_drawdown, split,
    summarize, trades_needed, wilson_interval,
)

DAYS = [date(2026, 1, 1) + timedelta(d) for d in range(400)]


# -- the split ---------------------------------------------------------------------


def test_holdout_assignment_is_stable_across_calls():
    assert [is_holdout(d) for d in DAYS[:50]] == [is_holdout(d) for d in DAYS[:50]]


def test_holdout_is_about_the_requested_share():
    _, holdout = split(DAYS)
    assert 0.18 < len(holdout) / len(DAYS) < 0.32


def test_the_split_is_exhaustive_and_disjoint():
    research, holdout = split(DAYS)
    assert len(research) + len(holdout) == len(DAYS)
    assert not set(research) & set(holdout)


def test_holdout_membership_does_not_depend_on_what_else_is_present():
    """A day's role was fixed before the data existed; adding days cannot move it."""
    _, few = split(DAYS[:20])
    _, many = split(DAYS)
    assert set(few) == {d for d in many if d in set(DAYS[:20])}


# -- statistics --------------------------------------------------------------------


def test_wilson_is_wide_at_small_n_and_never_certain():
    lo, hi = wilson_interval(3, 3)
    assert lo < 1.0 and hi <= 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_bootstrap_interval_brackets_the_mean():
    values = [13.0] * 70 + [-50.0] * 30
    lo, hi = bootstrap_ci(values)
    assert lo < sum(values) / len(values) < hi


def test_bootstrap_needs_more_than_one_point():
    lo, hi = bootstrap_ci([1.0])
    assert lo != lo and hi != hi  # NaN


def test_max_drawdown_measures_peak_to_trough():
    assert max_drawdown([10, 10, -30, 5]) == pytest.approx(30)
    assert max_drawdown([10, 20]) == 0.0


def test_trades_needed_grows_as_the_edge_shrinks():
    noisy_small_edge = [1.0] * 55 + [-1.0] * 45
    noisy_big_edge = [1.0] * 80 + [-1.0] * 20
    assert trades_needed(noisy_small_edge) > trades_needed(noisy_big_edge)


def test_best_of_k_flags_a_leaderboard_winner_as_luck():
    """Ten variants of pure noise routinely produce a good-looking winner."""
    values = [13.0] * 70 + [-30.0] * 30
    lucky = best_of_k_pvalue(2.0, values, k=10)
    assert lucky > 0.20


# -- summaries ---------------------------------------------------------------------


def _result(day, pnl, risk=100.0, entered=True, fidelity="live"):
    return ReplayResult(
        day=day, variant="v", fidelity=fidelity, entered=entered, reason="traded",
        pnl=pnl, risk=risk,
    )


def test_summary_counts_only_days_that_traded():
    results = [_result(DAYS[0], 10), _result(DAYS[1], 0, entered=False), _result(DAYS[2], -5)]
    s = summarize(results, "v", days=3)
    assert s.trades == 2 and s.days == 3
    assert s.trade_rate == pytest.approx(2 / 3)


def test_summary_separates_promotable_from_synthetic_trades():
    results = [_result(DAYS[0], 10), _result(DAYS[1], 10, fidelity="synthetic")]
    s = summarize(results, "v", days=2)
    assert s.trades == 2 and s.promotable_trades == 1


def test_verdict_refuses_to_conclude_from_a_small_sample():
    s = summarize([_result(DAYS[i], 50.0) for i in range(5)], "v", days=5)
    assert s.trades < MIN_TRADES_FOR_A_VERDICT
    assert s.verdict == "insufficient"


def test_verdict_reports_indistinguishable_when_the_interval_spans_zero():
    results = [_result(DAYS[i], 20.0 if i % 2 else -20.0) for i in range(60)]
    assert summarize(results, "v", days=60).verdict == "indistinguishable from zero"


def test_verdict_reports_negative_for_a_consistent_loser():
    results = [_result(DAYS[i], -20.0 if i % 5 else 10.0) for i in range(60)]
    assert summarize(results, "v", days=60).verdict == "negative"


# -- promotion ---------------------------------------------------------------------


def _summary(mean_r, promotable, n=60):
    results = [_result(DAYS[i], mean_r * 100.0) for i in range(n)]
    s = summarize(results, "v", days=n)
    return type(s)(**{**vars(s), "promotable_trades": promotable})


def test_promotion_needs_enough_live_trades():
    verdict = check_promotion("c", _summary(0.10, 12), _summary(0.0, 60),
                              _summary(0.10, 20), holdout_looks=0)
    assert not verdict.approved
    assert any("research trades" in r for r in verdict.reasons)


def test_promotion_needs_an_edge_over_the_champion():
    verdict = check_promotion("c", _summary(0.05, 60), _summary(0.05, 60),
                              _summary(0.05, 20), holdout_looks=0)
    assert not verdict.approved
    assert any("edge over champion" in r for r in verdict.reasons)


def test_promotion_needs_the_holdout_to_agree():
    """A research result that dies out of sample is the definition of overfitting."""
    verdict = check_promotion("c", _summary(0.20, 60), _summary(0.0, 60),
                              _summary(-0.10, 20), holdout_looks=0)
    assert not verdict.approved
    assert any("holdout expectancy" in r for r in verdict.reasons)


def test_promotion_is_blocked_once_the_holdout_is_burned():
    verdict = check_promotion("c", _summary(0.20, 60), _summary(0.0, 60),
                              _summary(0.20, 20), holdout_looks=10)
    assert not verdict.approved
    assert any("no longer independent" in r for r in verdict.reasons)


def test_a_clean_challenger_is_approved():
    verdict = check_promotion("c", _summary(0.20, 60), _summary(0.0, 60),
                              _summary(0.15, 20), holdout_looks=1)
    assert verdict.approved


def test_every_criterion_is_reported_not_just_the_first_failure():
    """A rejection naming one reason invites fixing that one thing and re-asking."""
    verdict = check_promotion("c", _summary(-0.10, 5), _summary(0.0, 60),
                              _summary(-0.10, 2), holdout_looks=0)
    assert len(verdict.reasons) >= 3


def test_synthetic_trades_do_not_count_toward_promotion():
    plenty_but_synthetic = _summary(0.20, promotable=0)
    verdict = check_promotion("c", plenty_but_synthetic, _summary(0.0, 60),
                              _summary(0.20, 20), holdout_looks=0)
    assert not verdict.approved


# -- the log -----------------------------------------------------------------------


def test_the_research_log_is_append_only(tmp_path):
    log = ResearchLog(tmp_path)
    log.propose("wider", "wings cost the same per leg", "beats champion by 0.02R")
    log.propose("later", "less exposure", "positive holdout")
    assert len(log.proposals()) == 2
    assert log.proposals()[0]["variant"] == "wider"


def test_holdout_looks_are_counted(tmp_path):
    log = ResearchLog(tmp_path)
    assert log.holdout_looks() == 0
    log.record_holdout_look("wider", "pre-registered")
    log.record_holdout_look("later", "pre-registered")
    assert log.holdout_looks() == 2


def test_promotions_record_both_outcomes(tmp_path):
    from trading_agent.research import PromotionVerdict

    log = ResearchLog(tmp_path)
    log.record_promotion(PromotionVerdict("a", True, ("ok",)), "champion", "a")
    log.record_promotion(PromotionVerdict("b", False, ("no",)), "a", "a")
    rows = log.promotions()
    assert [r["approved"] for r in rows] == [True, False]


def test_an_empty_log_reads_as_empty(tmp_path):
    log = ResearchLog(tmp_path / "nothing")
    assert log.proposals() == [] and log.promotions() == [] and log.holdout_looks() == 0

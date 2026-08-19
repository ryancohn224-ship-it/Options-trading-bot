from datetime import date, datetime

from trading_agent.journal import Journal, JournalEntry, new_entry, render_markdown


def _entry(**kw):
    e = new_entry(date(2026, 8, 18), "paper", "abc123", datetime(2026, 8, 18, 10, 15))
    for key, value in kw.items():
        setattr(e, key, value)
    return e


def test_the_afternoon_exit_does_not_erase_the_morning_reasoning(tmp_path):
    journal = Journal(tmp_path)
    journal.write(_entry(outcome="traded", gates=[{"name": "credit_ratio", "passed": True, "detail": "13%"}]))
    journal.write(_entry(outcome="traded", exit_reason="profit_target", realized_pnl=45.0))

    page = (tmp_path / "2026-08-18.md").read_text()
    assert "credit_ratio" in page
    assert "profit_target" in page
    assert page.count("# 2026-08-18") == 2


def test_every_session_appends_one_machine_readable_row(tmp_path):
    journal = Journal(tmp_path)
    journal.write(_entry(outcome="no_trade"))
    journal.write(_entry(outcome="traded"))
    lines = (tmp_path / "trades.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_a_stand_down_page_says_which_gate_failed(tmp_path):
    entry = _entry(
        outcome="no_trade",
        gates=[
            {"name": "credit_ratio", "passed": False, "detail": "9% of 1.00 width (floor 12%)"},
            {"name": "iv_band", "passed": True, "detail": "ATM IV 11%"},
        ],
    )
    page = render_markdown(entry)
    assert "Stood down" in page
    assert "❌ | `credit_ratio`" in page
    assert "✅ | `iv_band`" in page


def test_management_pages_do_not_render_empty_sections():
    page = render_markdown(_entry(outcome="traded", exit_reason="stop", realized_pnl=-160.0))
    assert "## Preflight" not in page
    assert "## Credit gate" not in page
    assert "Closed (stop)" in page


def test_the_page_records_what_produced_it():
    page = render_markdown(_entry(outcome="no_trade"))
    assert "config `abc123`" in page


def test_rows_round_trip_back_into_an_entry(tmp_path):
    """`review` and `show` rebuild entries from the JSONL, so the shape must survive."""
    import json

    journal = Journal(tmp_path)
    journal.write(_entry(outcome="traded", realized_pnl=12.5, contracts=3))
    row = json.loads((tmp_path / "trades.jsonl").read_text().splitlines()[0])
    assert JournalEntry(**row).realized_pnl == 12.5

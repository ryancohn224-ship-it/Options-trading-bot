import json

import pytest

from trading_agent import llm as llm_mod


@pytest.fixture
def enabled(config):
    return config.model_copy(update={"llm": config.llm.model_copy(update={"enabled": True})})


def _run(monkeypatch, config, stdout="", returncode=0):
    def fake_run(*a, **kw):
        return type("P", (), {"stdout": stdout, "stderr": "", "returncode": returncode})()

    monkeypatch.setattr(llm_mod.subprocess, "run", fake_run)
    return llm_mod.review({"spot": 600}, config)


def test_disabled_means_no_review_at_all(config):
    assert llm_mod.review({}, config) is None


def test_a_clean_veto_is_honoured(monkeypatch, enabled):
    payload = json.dumps({"stand_down": True, "reason": "CPI at 08:30", "note": "n"})
    assert _run(monkeypatch, enabled, payload).stand_down is True


def test_json_wrapped_in_prose_is_still_parsed(monkeypatch, enabled):
    noisy = 'Here is my review:\n{"stand_down": false, "reason": "ok", "note": "n"}\nDone.'
    verdict = _run(monkeypatch, enabled, noisy)
    assert verdict.stand_down is False
    assert verdict.error is None


def test_unparseable_output_fails_open_by_default(monkeypatch, enabled):
    verdict = _run(monkeypatch, enabled, "I think you should probably not trade today")
    assert verdict.stand_down is False
    assert verdict.error is not None, "a review that did not happen must say so"


def test_unparseable_output_fails_closed_when_configured(monkeypatch, enabled):
    strict = enabled.model_copy(
        update={"llm": enabled.llm.model_copy(update={"fail_open": False})}
    )
    assert _run(monkeypatch, strict, "nonsense").stand_down is True


def test_a_missing_stand_down_flag_is_not_treated_as_approval(monkeypatch, enabled):
    """An omitted field must never read as `false`."""
    verdict = _run(monkeypatch, enabled, json.dumps({"reason": "fine", "note": "n"}))
    assert verdict.error is not None


def test_a_nonzero_exit_is_handled_without_raising(monkeypatch, enabled):
    assert _run(monkeypatch, enabled, "", returncode=1).error is not None


def test_a_crashing_command_is_handled_without_raising(monkeypatch, enabled):
    def boom(*a, **kw):
        raise OSError("claude: not found")

    monkeypatch.setattr(llm_mod.subprocess, "run", boom)
    verdict = llm_mod.review({}, enabled)
    assert verdict.error is not None and verdict.stand_down is False


def test_the_prompt_states_the_model_cannot_change_the_trade():
    assert "veto" in llm_mod.PROMPT
    assert "cannot change" in llm_mod.PROMPT

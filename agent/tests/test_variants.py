import pytest
from pydantic import ValidationError

from trading_agent.variants import (
    DEFAULT_VARIANTS, Variant, active, champion, deep_merge, load_variants, save_variants,
)


def test_every_shipped_variant_resolves(config):
    for variant in DEFAULT_VARIANTS:
        assert variant.apply_to(config)


def test_an_overlay_changes_only_what_it_names(config):
    resolved = Variant("v", {"structure": {"wing_width": 3.0}}).apply_to(config)
    assert resolved.structure.wing_width == 3.0
    assert resolved.structure.short_delta == config.structure.short_delta
    assert resolved.gate.min_vrp == config.gate.min_vrp


def test_a_variant_cannot_escape_the_risk_limits(config):
    """The search runs inside the bounds in config.py, never through them."""
    with pytest.raises(ValidationError):
        Variant("reckless", {"risk": {"max_risk_pct": 0.30}}).apply_to(config)
    with pytest.raises(ValidationError):
        Variant("naked", {"structure": {"short_delta": 0.45}}).apply_to(config)
    with pytest.raises(ValidationError):
        Variant("overnight", {"market": {"dte": 30}}).apply_to(config)


def test_a_variant_cannot_invent_a_setting(config):
    with pytest.raises(ValidationError):
        Variant("typo", {"gate": {"min_credit_ration": 0.5}}).apply_to(config)


def test_deep_merge_leaves_the_base_untouched():
    base = {"a": {"b": 1, "c": 2}}
    merged = deep_merge(base, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}}
    assert base == {"a": {"b": 1, "c": 2}}


def test_champion_and_active_selection():
    variants = [
        Variant("old", {}, "", "retired", "lost by 0.1R"),
        Variant("live", {}, "", "champion"),
        Variant("new", {}, "", "challenger"),
    ]
    assert champion(variants).name == "live"
    assert [v.name for v in active(variants)] == ["live", "new"]


def test_variants_round_trip_through_yaml(tmp_path):
    path = tmp_path / "variants.yaml"
    save_variants(list(DEFAULT_VARIANTS), path)
    restored = load_variants(path)
    assert [v.name for v in restored] == [v.name for v in DEFAULT_VARIANTS]
    assert champion(restored).name == "champion"


def test_a_missing_file_falls_back_to_the_shipped_set(tmp_path):
    assert load_variants(tmp_path / "nope.yaml") == list(DEFAULT_VARIANTS)


def test_the_control_variants_exist():
    """`no-vrp-gate` and `no-stop` are how you find out a rule is doing nothing."""
    names = {v.name for v in DEFAULT_VARIANTS}
    assert {"no-vrp-gate", "no-stop", "champion"} <= names

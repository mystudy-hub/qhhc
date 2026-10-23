"""Foundation-core tests: tri-state logic, 10.6 parameter constraints,
section 4.2 contract eligibility, and section 4.3 fixed-point prices.

All fixtures are constructed in-memory; no database or network access.
"""

import dataclasses
from datetime import datetime

import pytest

from cta_lab.wyckoff_v4.contract import (
    BarV4,
    ContractViolation,
    EligibilityReason,
    EligibilityStatus,
    SeriesContract,
    boundary_epsilon,
    check_bar,
    check_bar_sequence,
    check_series_contract,
    check_weight_timing,
    confirm_step,
    from_ticks,
    to_ticks,
)
from cta_lab.wyckoff_v4.params import BaselineMode, WyckoffV4Params
from cta_lab.wyckoff_v4.types import (
    EventCode,
    TriState,
    and_,
    not_,
    or_,
    tri_equal,
    tri_not_equal,
)

T = TriState.TRUE
F = TriState.FALSE
N = TriState.NOT_EVALUABLE

ALL = (T, F, N)


# --- 8.3.3 tri-state operator table --------------------------------------


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (T, T, T),
        (T, F, F),
        (T, N, N),
        (F, T, F),
        (F, F, F),
        (F, N, F),
        (N, T, N),
        (N, F, F),
        (N, N, N),
    ],
)
def test_and_truth_table(a, b, expected):
    assert and_(a, b) is expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (T, T, T),
        (T, F, T),
        (T, N, T),
        (F, T, T),
        (F, F, F),
        (F, N, N),
        (N, T, T),
        (N, F, N),
        (N, N, N),
    ],
)
def test_or_truth_table(a, b, expected):
    assert or_(a, b) is expected


@pytest.mark.parametrize(
    "a,expected", [(T, F), (F, T), (N, N)]
)
def test_not_truth_table(a, expected):
    assert not_(a) is expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (T, T, T),
        (T, F, F),
        (T, N, N),
        (F, T, F),
        (F, F, T),
        (F, N, N),
        (N, T, N),
        (N, F, N),
        (N, N, N),
    ],
)
def test_tri_equal_truth_table(a, b, expected):
    assert tri_equal(a, b) is expected
    # not_equal is the value-wise complement whenever both sides are known,
    # and NOT_EVALUABLE whenever either side is NOT_EVALUABLE.
    if N in (a, b):
        assert tri_not_equal(a, b) is N
    else:
        assert tri_not_equal(a, b) is not_(tri_equal(a, b))


def test_tri_state_group_operators_variadic():
    # AND: any FALSE wins over NOT_EVALUABLE (8.3.3 precedence).
    assert and_(T, N, F) is F
    assert and_(T, N, N) is N
    assert and_(T, T, T) is T
    # OR: any TRUE wins over NOT_EVALUABLE.
    assert or_(F, N, T) is T
    assert or_(F, N, N) is N
    assert or_(F, F) is F
    with pytest.raises(ValueError):
        and_()
    with pytest.raises(ValueError):
        or_()
    with pytest.raises(TypeError):
        and_(T, True)


def test_event_code_table_complete():
    # Section 6.8 summary table: 55 codes, no invented members.
    assert len(EventCode) == 55
    for name in (
        "SC_FEATURE",
        "SPRING_LOW_VOLUME",
        "SHAKEOUT_RECLAIM",
        "UPTHRUST_REVERSAL_BREAKDOWN",
        "BREAKOUT_ACCEPTANCE_INVALIDATED",
        "SOS",
        "INTERNAL_FTI",
        "UTAD_WEAKNESS_SEQUENCE",
        "LPS",
        "LPSY",
        "DATA_GAP",
        "INDEX_SOURCE_MISMATCH",
        "EX_POST_WEIGHTS",
        "CONSTITUENT_CHANGE",
        "DELIVERY_SEASON",
        "WARMUP_INCOMPLETE",
    ):
        assert EventCode[name].value == name


# --- 10.6 parameter constraints -------------------------------------------


def test_default_params_validate():
    params = WyckoffV4Params()
    params.validate()
    assert params.rule_version == "4.1"
    assert params.parameter_version == "v4.1-default-unvalidated"
    # F-15: max(3, 2, 3) + 1 = 4.
    assert params.snapshot_ring_length == 4
    assert dataclasses.replace(params, spring_max_pattern_bars=1).snapshot_ring_length == 4
    assert dataclasses.replace(
        params, spring_max_pattern_bars=2, shakeout_max_pattern_bars=3
    ).snapshot_ring_length == 4


@pytest.mark.parametrize(
    "overrides,offending",
    [
        # min <= max window pairs (10.6).
        ({"ar_window_min": 9}, "ar_window_min"),
        ({"st_window_min": 16}, "st_window_min"),
        ({"test_window_min": 11}, "test_window_min"),
        ({"spring_sos_window_min": 11}, "spring_sos_window_min"),
        ({"utad_response_window_min": 13}, "utad_response_window_min"),
        # lps_min_pullback_bars < lps_max_pullback_bars (strict).
        ({"lps_min_pullback_bars": 8}, "lps_min_pullback_bars"),
        ({"lps_min_pullback_bars": 9}, "lps_min_pullback_bars"),
        # spring_low_volume_max_rvol < spring_high_volume_min_rvol (strict).
        ({"spring_low_volume_max_rvol": 1.3}, "spring_low_volume_max_rvol"),
        ({"spring_high_volume_min_rvol": 0.8}, "spring_high_volume_min_rvol"),
        # range_min_width_atr < range_max_width_atr (strict).
        ({"range_min_width_atr": 12.0}, "range_min_width_atr"),
        ({"range_max_width_atr": 3.0}, "range_max_width_atr"),
        # mature_range_age >= event_min_range_age.
        ({"mature_range_age": 14}, "mature_range_age"),
        # spring_max_pattern_bars in {1, 2}.
        ({"spring_max_pattern_bars": 3}, "spring_max_pattern_bars"),
        ({"spring_max_pattern_bars": 0}, "spring_max_pattern_bars"),
        # shakeout_max_pattern_bars >= spring_max_pattern_bars.
        ({"shakeout_max_pattern_bars": 1}, "shakeout_max_pattern_bars"),
        # strict_backup_min_pullback_bars <= lps_max_pullback_bars.
        ({"strict_backup_min_pullback_bars": 9}, "strict_backup_min_pullback_bars"),
        # warmup_bars covers all enabled windows (largest is 50 by default).
        ({"warmup_bars": 49}, "warmup_bars"),
        ({"warmup_bars": 10, "larger_context_window": 10, "trend_sma_slow": 10}, "warmup_bars"),
        # positive ints for bar counts.
        ({"atr_period": 0}, "atr_period"),
        ({"atr_period": -1}, "atr_period"),
        ({"regime_persistence_bars": 0}, "regime_persistence_bars"),
        ({"atr_period": 14.5}, "atr_period"),
        # confirm_step_units positive int.
        ({"confirm_step_units": 0}, "confirm_step_units"),
        ({"confirm_step_units": 1.5}, "confirm_step_units"),
        # positive ATR / volume multipliers.
        ({"tr_cap_mult": 0.0}, "tr_cap_mult"),
        ({"climax_min_rvol": -0.1}, "climax_min_rvol"),
        ({"spring_high_volume_min_rvol": 0.0}, "spring_high_volume_min_rvol"),
        # wave_dominance_ratio > 1.
        ({"wave_dominance_ratio": 1.0}, "wave_dominance_ratio"),
        # 0 < breakout_fail_deep_fraction < 1.
        ({"breakout_fail_deep_fraction": 0.0}, "breakout_fail_deep_fraction"),
        ({"breakout_fail_deep_fraction": 1.0}, "breakout_fail_deep_fraction"),
        ({"breakout_fail_deep_fraction": 1.5}, "breakout_fail_deep_fraction"),
        # mode switches and versions.
        ({"baseline_mode": "robust"}, "baseline_mode"),
        ({"rule_version": "3.0"}, "rule_version"),
        ({"rule_version": "4.0"}, "rule_version"),
        ({"parameter_version": ""}, "parameter_version"),
    ],
)
def test_param_constraint_violations(overrides, offending):
    params = dataclasses.replace(WyckoffV4Params(), **overrides)
    with pytest.raises(ValueError) as excinfo:
        params.validate()
    assert offending in str(excinfo.value)


def test_baseline_mode_standard_is_valid():
    params = dataclasses.replace(WyckoffV4Params(), baseline_mode=BaselineMode.STANDARD)
    params.validate()


def test_params_frozen():
    params = WyckoffV4Params()
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.atr_period = 20


# --- 4.3 fixed-point prices -------------------------------------------------


def test_to_ticks_exact_multiple():
    assert to_ticks(100.37, 0.01) == 10037
    assert to_ticks(100.37, 0.01) == 10037  # deterministic repeat
    assert from_ticks(10037, 0.01) == 100.37


def test_to_ticks_rejects_non_multiple():
    with pytest.raises(ContractViolation):
        to_ticks(100.371, 0.01)


@pytest.mark.parametrize(
    "price,resolution,expected_ticks",
    [
        (100.37, 0.01, 10037),
        (100.36, 0.01, 10036),
        (1.0, 0.25, 4),
        (0.75, 0.25, 3),
        (-0.02, 0.01, -2),  # prices are not required to be positive
        (3456.0, 1.0, 3456),
        (0.5, 0.5, 1),
    ],
)
def test_to_ticks_values(price, resolution, expected_ticks):
    assert to_ticks(price, resolution) == expected_ticks
    assert from_ticks(expected_ticks, resolution) == price


def test_to_ticks_rejects_non_positive_resolution():
    with pytest.raises(ValueError) as excinfo:
        to_ticks(100.37, 0.0)
    assert "price_resolution" in str(excinfo.value)


def test_confirm_step():
    assert confirm_step(1, 0.01) == pytest.approx(0.01)
    assert confirm_step(2, 0.5) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        confirm_step(0, 0.01)
    with pytest.raises(ValueError):
        confirm_step(1, 0.0)


def test_boundary_epsilon():
    # max(2 * 0.01, 0.10 * 0.5) = max(0.02, 0.05) = 0.05
    assert boundary_epsilon(2, 0.10, 0.01, 0.5) == pytest.approx(0.05)
    # resolution side dominates when ATR is tiny
    assert boundary_epsilon(2, 0.10, 0.01, 0.1) == pytest.approx(0.02)


# --- 4.1 / 4.2 contract eligibility -----------------------------------------


def make_contract(**overrides) -> SeriesContract:
    base = dict(
        index_id="IDX001",
        index_source="vendor_weighted",
        index_method_version="v1",
        data_version="d1",
        constituent_universe_definition="all listed delivery months",
        weight_method="open_interest",
        weight_asof_rule="previous_trading_day_close",
        weight_update_schedule="daily",
        ohlc_method="vendor_published",
        price_resolution=0.01,
        volume_method="sum",
        volume_unit="contracts",
        oi_method="sum",
        revision_policy="no_revision",
    )
    base.update(overrides)
    return SeriesContract(**base)


def test_valid_contract_is_eligible():
    result = check_series_contract(make_contract())
    assert result.status is EligibilityStatus.ELIGIBLE
    assert result.reason is None
    assert result.eligible


@pytest.mark.parametrize(
    "overrides",
    [
        {"index_source": "synthetic"},  # A01-style synthetic index
        {"index_source": "main_continuous"},
        {"index_source": "back_adjusted"},
        {"index_source": "panama"},
        {"index_source": ""},  # undeclared source
        {"weight_method": ""},  # no weight provenance
        {"weight_asof_rule": ""},
        {"constituent_universe_definition": ""},
    ],
)
def test_non_weighted_sources_rejected(overrides):
    result = check_series_contract(make_contract(**overrides))
    assert result.status is EligibilityStatus.REJECTED
    assert result.reason is EligibilityReason.INDEX_SOURCE_MISMATCH
    assert result.reason.value == "index_source_mismatch"


def test_per_constituent_ohlc_method_rejected():
    # A02-style: weighting each constituent's H/L separately creates
    # non-synchronous extremes.
    result = check_series_contract(make_contract(ohlc_method="per_constituent_weighted"))
    assert result.status is EligibilityStatus.REJECTED
    assert result.reason is EligibilityReason.OHLC_METHOD_INVALID


def test_synchronous_path_ohlc_method_allowed():
    result = check_series_contract(make_contract(ohlc_method="synchronous_path"))
    assert result.status is EligibilityStatus.ELIGIBLE


def test_unsupported_data_profile_rejected():
    result = check_series_contract(make_contract(data_profile="constituent_bars"))
    assert result.status is EligibilityStatus.REJECTED
    assert result.reason is EligibilityReason.DATA_PROFILE_UNSUPPORTED


def test_declared_index_missing_returns_missing_no_substitution():
    # A missing declared index is a missing entry; the eligibility result
    # carries no alternative series and the checker performs no fallback.
    result = check_series_contract(make_contract(), index_available=False)
    assert result.status is EligibilityStatus.MISSING
    assert result.reason is EligibilityReason.DECLARED_INDEX_MISSING
    assert not result.eligible
    assert not hasattr(result, "fallback_index_id")


def test_non_positive_price_resolution_raises():
    with pytest.raises(ValueError) as excinfo:
        check_series_contract(make_contract(price_resolution=0.0))
    assert "price_resolution" in str(excinfo.value)


def make_bar(**overrides) -> BarV4:
    base = dict(
        bar_start=datetime(2024, 1, 2, 9, 0),
        bar_end=datetime(2024, 1, 2, 9, 5),
        open=100.10,
        high=100.50,
        low=100.00,
        close=100.30,
        volume=1200.0,
    )
    base.update(overrides)
    return BarV4(**base)


def test_ex_post_weights_rejected_for_realtime():
    # A07-style: weights taken as-of later than the bar's computation time.
    bar = make_bar(received_at=datetime(2024, 1, 2, 9, 5, 1))
    late = check_weight_timing(bar, datetime(2024, 1, 2, 15, 0))
    assert late is EligibilityReason.EX_POST_WEIGHTS
    assert late.value == "ex_post_weights"
    # Weights knowable at bar time are fine.
    assert check_weight_timing(bar, datetime(2024, 1, 1, 15, 0)) is None
    assert check_weight_timing(bar, datetime(2024, 1, 2, 9, 5, 1)) is None
    # Without received_at, the bar end is the computation time.
    assert check_weight_timing(make_bar(), datetime(2024, 1, 2, 9, 6)) is (
        EligibilityReason.EX_POST_WEIGHTS
    )


def test_bar_sanity_problems():
    assert check_bar(make_bar()) == ()
    assert check_bar(make_bar(high=100.20)) == ("high_below_max_open_close",)
    assert check_bar(make_bar(low=100.20)) == ("low_above_min_open_close",)
    assert check_bar(make_bar(high=99.0, low=100.0, open=100.0, close=100.0)) == (
        "high_below_max_open_close",
        "high_below_low",
    )
    assert check_bar(make_bar(volume=-1.0)) == ("negative_volume",)
    assert check_bar(make_bar(open_interest=500.0)) == (
        "open_interest_without_oi_available_at",
    )
    assert check_bar(make_bar(open_interest=-5.0, oi_available_at=datetime(2024, 1, 2, 10, 0))) == (
        "negative_open_interest",
    )
    assert check_bar(make_bar(bar_end=datetime(2024, 1, 2, 8, 59))) == (
        "bar_end_before_bar_start",
    )
    # Negative prices are legal (4.2: prices are not required to be positive).
    assert check_bar(
        make_bar(open=-10.5, high=-10.0, low=-11.0, close=-10.2)
    ) == ()


def test_bar_sequence_problems():
    bars = [
        make_bar(bar_start=datetime(2024, 1, 2, 9, 0), bar_end=datetime(2024, 1, 2, 9, 5)),
        make_bar(bar_start=datetime(2024, 1, 2, 9, 5), bar_end=datetime(2024, 1, 2, 9, 10)),
        make_bar(bar_start=datetime(2024, 1, 2, 9, 10), bar_end=datetime(2024, 1, 2, 9, 15)),
    ]
    assert check_bar_sequence(bars) == ()
    out_of_order = [bars[0], bars[2], bars[1]]
    assert check_bar_sequence(out_of_order) == ("bar_start_out_of_order",)
    duplicate = [bars[0], bars[0]]
    assert check_bar_sequence(duplicate) == ("duplicate_bar_key",)
    # Different sessions may share a bar_start without colliding.
    other_session = make_bar(
        bar_start=datetime(2024, 1, 2, 9, 0), bar_end=datetime(2024, 1, 2, 9, 5), session_id="night"
    )
    assert check_bar_sequence([bars[0], other_session]) == ()


def test_bar_key_and_flags():
    bar = make_bar(quality_flags=frozenset({"vendor_late"}))
    assert bar.key == (None, datetime(2024, 1, 2, 9, 0))
    assert bar.quality_flags == frozenset({"vendor_late"})

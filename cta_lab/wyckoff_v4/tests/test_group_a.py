"""Group A acceptance cases (spec 11.2) and focused indicator unit tests.

All fixtures are constructed in-memory; no database or network access.
"""

import dataclasses
from datetime import datetime, timedelta

import pytest

from cta_lab.wyckoff_v4.contract import (
    ALLOWED_OHLC_METHODS,
    BarV4,
    ContractViolation,
    EligibilityReason,
    EligibilityStatus,
    SeriesContract,
    boundary_epsilon,
    check_series_contract,
    check_weight_timing,
    to_ticks,
)
from cta_lab.wyckoff_v4.indicators import (
    DATA_GAP_FLAG,
    IndicatorState,
    NotEvaluableReason,
    NumFeature,
    OiQuadrant,
    SwingKind,
    cumulative_effort,
    effort_ratio,
)
from cta_lab.wyckoff_v4.params import BaselineMode, WyckoffV4Params


# --- helpers ---------------------------------------------------------------


def daily_bar(
    i,
    o,
    h,
    l,
    c,
    v,
    *,
    oi=None,
    oi_at=None,
    received=None,
    flags=frozenset(),
):
    day = datetime(2024, 1, 1) + timedelta(days=i)
    return BarV4(
        bar_start=day.replace(hour=9),
        bar_end=day.replace(hour=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        trading_day=day.date().isoformat(),
        received_at=received,
        open_interest=oi,
        oi_available_at=oi_at,
        quality_flags=flags,
    )


def intraday_bar(
    i,
    o,
    h,
    l,
    c,
    v,
    *,
    session="S1",
    slot=(9, 30),
    oi=None,
    oi_at=None,
    received=None,
    flags=frozenset(),
):
    day = datetime(2024, 1, 1) + timedelta(days=i)
    start = day.replace(hour=slot[0], minute=slot[1])
    return BarV4(
        bar_start=start,
        bar_end=start + timedelta(minutes=1),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        trading_day=day.date().isoformat(),
        session_id=session,
        received_at=received,
        open_interest=oi,
        oi_available_at=oi_at,
        quality_flags=flags,
    )


def flat_daily(i, close, v):
    return daily_bar(i, close, close, close, close, v)


SMALL = WyckoffV4Params(
    atr_period=2,
    volume_period=3,
    volume_min_samples=3,
    oi_window=3,
)
SMALL_STANDARD = dataclasses.replace(SMALL, baseline_mode=BaselineMode.STANDARD)


def ne(feature):
    assert isinstance(feature, NumFeature)
    assert feature.value is None
    assert feature.reason is not None
    return feature.reason


# --- A01: declared weighted index missing, same-name synthetic *_index ----


def test_a01_declared_missing_synthetic_same_name_not_substituted():
    declared = SeriesContract(
        index_id="WI500",
        index_source="vendor_weighted",
        index_method_version="1",
        data_version="d1",
        constituent_universe_definition="top_500_float",
        weight_method="float_cap",
        weight_asof_rule="previous_close",
        weight_update_schedule="quarterly",
        ohlc_method="synchronous_path",
        price_resolution=0.01,
        volume_method="sum_raw",
        volume_unit="contracts",
        oi_method="turnover",
        revision_policy="frozen",
    )
    # The database holds only a same-name synthetic *_index series.
    synthetic = dataclasses.replace(
        declared,
        index_id="WI500_index",
        index_source="synthetic",
        weight_method="",
        weight_asof_rule="",
        constituent_universe_definition="",
    )
    store = {synthetic.index_id: synthetic}

    # The declared weighted index is missing: surface the missing entry.
    eligibility = check_series_contract(declared, index_available=False)
    assert eligibility.status is EligibilityStatus.MISSING
    assert eligibility.reason is EligibilityReason.DECLARED_INDEX_MISSING

    # The same-name synthetic series is rejected (index_source_mismatch) and
    # is never substituted as the input.
    candidate = store.get("WI500_index")
    assert candidate is not None
    candidate_eligibility = check_series_contract(candidate)
    assert candidate_eligibility.status is EligibilityStatus.REJECTED
    assert candidate_eligibility.reason is EligibilityReason.INDEX_SOURCE_MISMATCH

    # No substitution: neither the declared index nor the synthetic stand-in
    # yields an eligible input.
    assert not eligibility.eligible
    assert not candidate_eligibility.eligible


# --- A02: per-constituent weighted H/L ohlc_method --------------------------


def test_a02_per_constituent_weighted_hl_rejected():
    # Construction: two constituents whose extremes are non-synchronous
    # (constituent A peaks on bar t, constituent B peaks on bar t+1), with
    # ohlc_method weighting each constituent's H/L separately. The method
    # itself is rejected before any data is read.
    contract = SeriesContract(
        index_id="WI001",
        index_source="vendor_weighted",
        constituent_universe_definition="two_legs",
        weight_method="float_cap",
        weight_asof_rule="previous_close",
        ohlc_method="constituent_weighted_high_low",
        price_resolution=0.01,
    )
    assert "constituent_weighted_high_low" not in ALLOWED_OHLC_METHODS
    eligibility = check_series_contract(contract)
    assert eligibility.status is EligibilityStatus.REJECTED
    assert eligibility.reason is EligibilityReason.OHLC_METHOD_INVALID


# --- A03: epsilon from price_resolution, not the constituent tick ----------


def test_a03_comparison_at_index_price_with_price_resolution_epsilon():
    # Index price 100.37 with price_resolution 0.01: compared at 100.37.
    assert to_ticks(100.37, 0.01) == 10037
    # Epsilon comes from price_resolution (2 units => 0.02), not from the
    # constituent tick of 1.
    assert boundary_epsilon(2, 0.10, 0.01, 0.05) == pytest.approx(0.02)
    # The ATR fraction can dominate when ATR is large.
    assert boundary_epsilon(2, 0.10, 0.01, 1.0) == pytest.approx(0.10)
    # The constituent tick (1) cannot represent 100.37 without rounding;
    # using it for the comparison is a contract violation, not a fallback.
    with pytest.raises(ContractViolation):
        to_ticks(100.37, 1.0)


# --- A04: Bv cap recovery ---------------------------------------------------


def test_a04_bv_cap_recovery():
    state = IndicatorState(WyckoffV4Params())
    t0 = 10
    for i in range(40):
        volume = 500 if i == t0 else 100
        state.advance(flat_daily(i, 100.0, volume))
    f20 = state.features[20]  # t0+10
    assert f20.bv_baseline_standard.value == pytest.approx(120.0)
    assert f20.bv_baseline_robust.value == pytest.approx(110.0)  # cap 3*100
    assert f20.rvol.value == pytest.approx(100.0 / 110.0)  # robust selected
    f31 = state.features[31]  # t0+21: the spike left the sampling set
    assert f31.bv_baseline_standard.value == pytest.approx(100.0)
    assert f31.bv_baseline_robust.value == pytest.approx(100.0)


# --- A05: initial ATR untruncated, truncation from the next bar ------------


def test_a05_initial_atr_untruncated():
    state = IndicatorState(WyckoffV4Params())
    n = 14
    closes = [100.0]
    closes += [110.0, 100.0, 110.0, 100.0]  # TRs of 10
    closes += [1100.0]  # a TR of 1000 inside the first n_atr TRs
    closes += [1090.0, 1100.0, 1090.0, 1100.0, 1090.0, 1100.0, 1090.0, 1100.0, 1090.0]
    closes += [2090.0]  # bar 15: another TR of 1000, now truncated
    closes += [2080.0]  # bar 16: TR of 10
    for i, close in enumerate(closes):
        state.advance(flat_daily(i, close, 100.0))
    initial = (13 * 10.0 + 1000.0) / n  # untruncated mean of the first 14 TRs

    # While accumulating, no ATR exists and no capping ratio is defined.
    assert state.features[13].atr_capping_ratio is None
    # The initial value commits after bar 14's features: A_t at bar 14 was
    # still unavailable.
    assert ne(state.features[14].atr_baseline) is NotEvaluableReason.ATR_UNAVAILABLE
    # The initial value is the untruncated mean; robust == standard.
    assert state.features[14].atr_capping_ratio == 1.0
    assert state.features[15].atr_baseline_standard == pytest.approx(initial)
    assert state.features[15].atr_baseline_robust == pytest.approx(initial)
    # Bar 15's update truncates the 1000 TR to 3 * median(10) = 30.
    assert state.features[15].tr_capping_ratio == pytest.approx(30.0 / 1000.0)
    assert state.features[16].atr_baseline_standard == pytest.approx(
        ((n - 1) * initial + 1000.0) / n
    )
    assert state.features[16].atr_baseline_robust == pytest.approx(
        ((n - 1) * initial + 30.0) / n
    )
    # Bar 16's small TR is not capped.
    assert state.features[16].tr_capping_ratio == 1.0


# --- A06: intraday session-matched gate -------------------------------------


def _slot_series(days, base_volume, current_volume):
    bars = []
    for i in range(days):
        v = current_volume if i == days - 1 else base_volume
        bars.append(intraday_bar(i, 100.0, 101.0, 99.0, 100.0, v))
    return bars


def test_a06_session_gate_used_for_intraday():
    bars = _slot_series(13, 100.0, 150.0)  # 12 valid same-slot prior samples
    state = IndicatorState(WyckoffV4Params(), intraday=True)
    for bar in bars:
        state.advance(bar)
    f = state.features[12]
    assert f.bv_baseline_standard.value == pytest.approx(100.0)
    assert f.bv_baseline_robust.value == pytest.approx(100.0)
    assert f.rvol.value == pytest.approx(1.5)

    # The daily gate (15) would not make the same bars available.
    daily_state = IndicatorState(WyckoffV4Params())
    for bar in bars:
        daily_state.advance(bar)
    fd = daily_state.features[12]
    assert ne(fd.bv_baseline) is NotEvaluableReason.BV_UNAVAILABLE

    # 9 prior same-slot samples do not clear the session gate of 10.
    short_state = IndicatorState(WyckoffV4Params(), intraday=True)
    for bar in _slot_series(10, 100.0, 150.0):
        short_state.advance(bar)
    assert ne(short_state.features[9].bv_baseline) is NotEvaluableReason.BV_UNAVAILABLE


# --- A07: same-day-close OI weights => ex_post_weights ----------------------


def test_a07_ex_post_weights_rejected_for_realtime():
    # An intraday bar closing 14:55 (received 14:55:30) whose weights were
    # taken as-of the same-day close (15:00) is ex_post_weights.
    bar = intraday_bar(
        0,
        100.0,
        101.0,
        99.0,
        100.5,
        1000.0,
        received=datetime(2024, 1, 1, 14, 55, 30),
    )
    same_day_close = datetime(2024, 1, 1, 15, 0)
    assert (
        check_weight_timing(bar, same_day_close)
        is EligibilityReason.EX_POST_WEIGHTS
    )
    # Weights knowable before the bar's computation time pass.
    earlier = datetime(2024, 1, 1, 9, 0)
    assert check_weight_timing(bar, earlier) is None


# --- focused unit tests ------------------------------------------------------


def test_rvol_ns_cp_hand_computed():
    state = IndicatorState(SMALL)
    state.advance(daily_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0))
    state.advance(daily_bar(1, 100.0, 103.0, 99.0, 102.0, 100.0))  # TR=4
    state.advance(daily_bar(2, 102.0, 104.0, 101.0, 103.0, 500.0))  # TR=3
    f1 = state.features[1]
    assert ne(f1.ns) is NotEvaluableReason.ATR_UNAVAILABLE
    assert f1.cp.value == pytest.approx(0.75)  # (102-99)/4
    f3 = state.advance(
        daily_bar(3, 103.0, 106.0, 102.0, 105.0, 150.0)
    )
    # A_t = mean(4, 3) = 3.5 (initial, both modes).
    assert f3.atr_baseline_standard == pytest.approx(3.5)
    assert f3.atr_baseline_robust == pytest.approx(3.5)
    assert f3.ns.value == pytest.approx(4.0 / 3.5)
    assert f3.cp.value == pytest.approx(0.75)  # (105-102)/4
    assert f3.lw.value == pytest.approx(0.25)  # (103-102)/4
    assert f3.uw.value == pytest.approx(0.25)  # (106-105)/4
    assert f3.net_move.value == pytest.approx(2.0 / 3.5)
    assert f3.gap.value == pytest.approx(0.0)
    # Bv from W = [100, 100, 500]: standard 700/3, robust (cap 300) 500/3.
    assert f3.bv_baseline_standard.value == pytest.approx(700.0 / 3.0)
    assert f3.bv_baseline_robust.value == pytest.approx(500.0 / 3.0)
    assert f3.rvol.value == pytest.approx(150.0 / (500.0 / 3.0))  # robust mode
    assert f3.bv_capping_ratio == pytest.approx(5.0 / 7.0)
    # First bar: no prior close.
    f0 = state.features[0]
    assert ne(f0.net_move) is NotEvaluableReason.NO_PRIOR_CLOSE
    assert ne(f0.gap) is NotEvaluableReason.NO_PRIOR_CLOSE


def test_hl_equal_makes_position_features_not_evaluable():
    state = IndicatorState(SMALL)
    state.advance(daily_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0))
    state.advance(daily_bar(1, 100.0, 103.0, 99.0, 102.0, 100.0))
    state.advance(daily_bar(2, 102.0, 104.0, 101.0, 103.0, 100.0))
    f = state.advance(daily_bar(4, 105.0, 105.0, 105.0, 105.0, 100.0))
    for feature in (f.cp, f.lw, f.uw):
        assert ne(feature) is NotEvaluableReason.SPREAD_ZERO
        assert feature.value != 0.5  # never the silent 0.5 fill
    assert f.spread == 0.0
    assert f.ns.value == pytest.approx(0.0)  # 0 / ATR is a real value


def test_effort_vs_cum_effort_doc_example():
    # 10 bars at RVOL 0.8 vs 2 bars at RVOL 1.2 (Bv = 100 throughout).
    effort_a = effort_ratio([80.0] * 10, [100.0] * 10)
    effort_b = effort_ratio([120.0] * 2, [100.0] * 2)
    assert effort_a.value == pytest.approx(0.8)
    assert effort_b.value == pytest.approx(1.2)
    assert effort_b.value > effort_a.value  # the 2-bar wave is more active
    cum_a = cumulative_effort([NumFeature.available(0.8)] * 10)
    cum_b = cumulative_effort([NumFeature.available(1.2)] * 2)
    assert cum_a.value == pytest.approx(8.0)
    assert cum_b.value == pytest.approx(2.4)
    assert cum_a.value > cum_b.value  # but the 10-bar wave has more effort
    # Never zero-filled or silently dropped.
    assert ne(effort_ratio([100.0], [0.0])) is NotEvaluableReason.BV_ZERO
    assert (
        ne(cumulative_effort([NumFeature.available(1.0), NumFeature.not_evaluable(NotEvaluableReason.BV_ZERO)]))
        is NotEvaluableReason.BV_ZERO
    )
    assert ne(effort_ratio([], [])) is NotEvaluableReason.WINDOW_INVALID


def test_state_effort_windows():
    state = IndicatorState(SMALL_STANDARD)
    state.advance(daily_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0))
    state.advance(daily_bar(1, 100.0, 103.0, 99.0, 102.0, 100.0))
    state.advance(daily_bar(2, 102.0, 104.0, 101.0, 103.0, 500.0))
    state.advance(daily_bar(3, 103.0, 106.0, 102.0, 105.0, 150.0))
    state.advance(daily_bar(4, 105.0, 105.0, 105.0, 105.0, 100.0))
    state.advance(daily_bar(5, 105.0, 106.0, 104.0, 105.0, 100.0))
    # Standard Bv: bars 3..5 = 700/3, 250, 250 (volumes 150, 100, 100).
    assert state.effort(3, 5).value == pytest.approx(350.0 / (700.0 / 3.0 + 500.0))
    assert state.cum_effort(3, 5).value == pytest.approx(9.0 / 14.0 + 0.8)
    # Every Bv in the window must be available (4.5).
    assert ne(state.effort(0, 2)) is NotEvaluableReason.BV_MISSING_IN_WINDOW
    # Window sanity.
    assert ne(state.effort(5, 3)) is NotEvaluableReason.WINDOW_INVALID
    assert ne(state.effort(0, 99)) is NotEvaluableReason.WINDOW_INVALID


def test_zero_volume_bar_retained_with_flag():
    state = IndicatorState(SMALL)
    for i in range(4):
        state.advance(
            daily_bar(i, 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i, 0.0)
        )
    for f in state.features:
        assert f.valid is True
        assert f.zero_volume is True
    # Bv = 0 makes RVOL not evaluable (never 0/0).
    assert ne(state.features[3].rvol) is NotEvaluableReason.BV_ZERO
    assert state.features[3].bv_baseline_standard.value == 0.0
    # A real-volume bar after the zero-volume warmup still sees Bv = 0.
    f = state.advance(daily_bar(4, 104.0, 105.0, 103.0, 104.0, 50.0))
    assert f.zero_volume is False
    assert ne(f.rvol) is NotEvaluableReason.BV_ZERO


def test_data_gap_placeholder_pauses_windows():
    state = IndicatorState(SMALL)
    state.advance(daily_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0))
    state.advance(daily_bar(1, 100.0, 103.0, 99.0, 102.0, 100.0))
    state.advance(daily_bar(2, 102.0, 104.0, 101.0, 103.0, 100.0))
    gap = state.advance(
        daily_bar(
            3,
            100.0,
            100.0,
            100.0,
            100.0,
            0.0,
            flags=frozenset({DATA_GAP_FLAG}),
        )
    )
    assert gap.valid is False
    for feature in (gap.ns, gap.cp, gap.rvol, gap.net_move):
        assert ne(feature) is NotEvaluableReason.DATA_GAP
    # The next bar's Bv sampling set contains the gap: 2 qualified of 3.
    f4 = state.advance(daily_bar(4, 103.0, 105.0, 102.0, 104.0, 100.0))
    assert ne(f4.bv_baseline) is NotEvaluableReason.BV_UNAVAILABLE
    # The ATR baseline is unchanged by the gap (no TR commit).
    assert f4.atr_baseline_standard == pytest.approx(3.5)  # mean(4, 3)
    # Windows containing the gap pause.
    assert ne(state.effort(0, 4)) is NotEvaluableReason.DATA_GAP
    assert ne(state.cum_effort(2, 4)) is NotEvaluableReason.DATA_GAP


def test_swing_point_confirmation_timing():
    params = WyckoffV4Params()
    state = IndicatorState(params)
    series = []
    for i in range(20):
        series.append(daily_bar(i, 100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i, 100.0))
    series.append(daily_bar(20, 129.0, 130.0, 128.0, 129.0, 100.0))
    series.append(daily_bar(21, 125.0, 126.0, 122.0, 124.0, 100.0))
    series.append(daily_bar(22, 124.0, 125.0, 121.0, 123.0, 100.0))
    series.append(daily_bar(23, 123.0, 124.0, 120.0, 122.0, 100.0))  # t = j+3
    series.append(daily_bar(24, 121.0, 123.0, 118.0, 120.0, 100.0))  # trough
    series.append(daily_bar(25, 120.0, 124.0, 119.0, 123.0, 100.0))
    series.append(daily_bar(26, 123.0, 125.0, 120.0, 124.0, 100.0))
    series.append(daily_bar(27, 124.0, 126.0, 121.0, 125.0, 100.0))  # t = j+3

    for bar in series[:23]:  # indices 0..22: nothing confirmed yet
        state.advance(bar)
    assert state.swing_points == ()

    state.advance(series[23])  # j=20 confirmed at the j+k close
    (point,) = state.swing_points
    assert point.kind is SwingKind.HIGH
    assert point.index == 20
    assert point.price == pytest.approx(130.0)
    assert point.confirmed_at_index == 23
    assert point.usable_from_index == 24  # usable only from j+k+1

    for bar in series[24:27]:
        state.advance(bar)
    assert len(state.swing_points) == 1  # immutable, no additions

    state.advance(series[27])  # j=24 swing low confirmed
    high_point, low_point = state.swing_points
    assert high_point.index == 20 and high_point.price == pytest.approx(130.0)
    assert low_point.kind is SwingKind.LOW
    assert low_point.index == 24
    assert low_point.price == pytest.approx(118.0)
    assert low_point.confirmed_at_index == 27
    assert low_point.usable_from_index == 28
    assert state.swing_discards == ()


def test_oi_features_reasons_and_quadrant():
    params = dataclasses.replace(SMALL, oi_window=3)
    day = lambda i: datetime(2024, 1, 1) + timedelta(days=i)  # noqa: E731

    def oi_bar(i, o, h, l, c, oi, oi_at=None, **kw):
        return daily_bar(
            i, o, h, l, c, 100.0, oi=oi,
            oi_at=oi_at or day(i).replace(hour=15), **kw,
        )

    closes = [(100.0, 100.0), (101.0, 102.0), (100.0, 104.0), (101.0, 103.0), (103.0, 107.0)]
    state = IndicatorState(params, oi_method="turnover")
    for i, (close, oi) in enumerate(closes):
        state.advance(oi_bar(i, close, close + 1.0, close - 1.0, close, oi))
    f1, f2, f3, f4 = state.features[1:5]
    assert f1.oi.d_oi.value == pytest.approx(2.0)
    assert ne(f1.oi.oi_z) is NotEvaluableReason.OI_HISTORY_INSUFFICIENT
    assert f2.oi.d_oi.value == pytest.approx(2.0)
    assert ne(f3.oi.oi_z) is NotEvaluableReason.OI_HISTORY_INSUFFICIENT
    # bar 4: dOI = 4, median(|2|, |2|, |-1|) = 2 => OIz = 2.0, close up.
    assert f4.oi.d_oi.value == pytest.approx(4.0)
    assert f4.oi.oi_z.value == pytest.approx(2.0)
    assert f4.oi.oi_quadrant is OiQuadrant.UP_OI_UP
    assert f4.oi.oi_z_available_at == day(4).replace(hour=15)
    assert f4.oi.realtime_view().oi_z.value == pytest.approx(2.0)
    # Windowed OIz(a..b).
    window = state.oi_z_window(4, 4)
    assert window.num.value == pytest.approx(2.0)
    assert window.available_at == day(4).replace(hour=15)
    assert (
        ne(state.oi_z_window(2, 2).num)
        is NotEvaluableReason.OI_HISTORY_INSUFFICIENT
    )
    assert ne(state.oi_z_window(5, 4).num) is NotEvaluableReason.WINDOW_INVALID


def test_oi_late_flagged_not_yet_available():
    params = dataclasses.replace(SMALL, oi_window=3)
    day = lambda i: datetime(2024, 1, 1) + timedelta(days=i)  # noqa: E731
    state = IndicatorState(params, oi_method="turnover")
    values = [(100.0, 100.0), (101.0, 102.0), (100.0, 104.0), (101.0, 103.0)]
    for i, (close, oi) in enumerate(values):
        state.advance(
            daily_bar(
                i, close, close + 1.0, close - 1.0, close, 100.0,
                oi=oi, oi_at=day(i).replace(hour=15),
            )
        )
    # Bar 4's OI is published 1 hour after the close: late OI.
    f = state.advance(
        daily_bar(
            4, 103.0, 104.0, 102.0, 103.0, 100.0,
            oi=107.0, oi_at=day(4).replace(hour=16),
        )
    )
    assert f.oi.oi_z.value == pytest.approx(2.0)  # descriptive record kept
    realtime = f.oi.realtime_view()
    assert ne(realtime.oi_z) is NotEvaluableReason.OI_NOT_YET_AVAILABLE
    assert realtime.oi_quadrant is None
    assert realtime.oi_quadrant_reason is NotEvaluableReason.OI_NOT_YET_AVAILABLE
    assert realtime.d_oi.value is None


def test_oi_missing_method_and_zero_scale():
    params = dataclasses.replace(SMALL, oi_window=3)
    # Undeclared method.
    state = IndicatorState(params, oi_method="")
    for i in range(5):
        state.advance(
            daily_bar(
                i, 100.0, 101.0, 99.0, 100.0, 100.0,
                oi=100.0 + i,
                oi_at=datetime(2024, 1, 1) + timedelta(days=i, hours=15),
            )
        )
    f = state.features[4]
    assert ne(f.oi.d_oi) is NotEvaluableReason.OI_METHOD_UNKNOWN
    assert ne(f.oi.oi_z) is NotEvaluableReason.OI_METHOD_UNKNOWN
    assert f.oi.oi_quadrant_reason is NotEvaluableReason.OI_METHOD_UNKNOWN
    assert ne(state.oi_z_window(4, 4).num) is NotEvaluableReason.OI_METHOD_UNKNOWN

    # Missing values: a bar without OI makes its own and the next dOI
    # not evaluable (never zero-filled).
    state2 = IndicatorState(params, oi_method="turnover")
    state2.advance(
        daily_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0, oi=100.0,
                  oi_at=datetime(2024, 1, 1, 15))
    )
    state2.advance(daily_bar(1, 100.0, 101.0, 99.0, 100.0, 100.0, oi=None))
    f1 = state2.features[1]
    assert ne(f1.oi.d_oi) is NotEvaluableReason.OI_MISSING
    state2.advance(
        daily_bar(2, 100.0, 101.0, 99.0, 100.0, 100.0, oi=104.0,
                  oi_at=datetime(2024, 1, 3, 15))
    )
    f2 = state2.features[2]
    assert ne(f2.oi.d_oi) is NotEvaluableReason.OI_MISSING

    # Zero scale: constant OI => every dOI is 0 => median 0.
    state3 = IndicatorState(params, oi_method="turnover")
    for i in range(5):
        state3.advance(
            daily_bar(i, 100.0, 101.0, 99.0, 100.0, 100.0, oi=100.0,
                      oi_at=datetime(2024, 1, 1) + timedelta(days=i, hours=15))
        )
    f4 = state3.features[4]
    assert f4.oi.d_oi.value == pytest.approx(0.0)
    assert ne(f4.oi.oi_z) is NotEvaluableReason.OI_ZERO_SCALE
    assert f4.oi.oi_quadrant is None
    assert f4.oi.oi_quadrant_reason is NotEvaluableReason.OI_ZERO_SCALE


def test_flat_prices_atr_zero():
    state = IndicatorState(SMALL)
    for i in range(5):
        state.advance(flat_daily(i, 100.0, 100.0))
    f = state.features[3]  # A_t = mean(0, 0) = 0
    assert f.atr_baseline.value == pytest.approx(0.0)
    assert ne(f.ns) is NotEvaluableReason.ATR_ZERO
    assert ne(f.net_move) is NotEvaluableReason.ATR_ZERO
    assert ne(f.gap) is NotEvaluableReason.ATR_ZERO


def test_determinism_same_input_same_output():
    def build_bars():
        bars = []
        for i in range(30):
            close = 100.0 + (i % 5)
            oi = 100.0 + (i % 3) if i != 7 else None
            bars.append(
                daily_bar(
                    i,
                    close,
                    close + 1.0,
                    close - 1.0,
                    close,
                    100.0 + 10 * (i % 4),
                    oi=oi,
                    oi_at=datetime(2024, 1, 1) + timedelta(days=i, hours=15),
                )
            )
        bars[11] = daily_bar(
                11,
                100.0,
                100.0,
                100.0,
                100.0,
                0.0,
                flags=frozenset({DATA_GAP_FLAG}),
            )
        return bars

    params = dataclasses.replace(SMALL, oi_window=3)
    state_a = IndicatorState(params, oi_method="turnover")
    state_b = IndicatorState(params, oi_method="turnover")
    for bar in build_bars():
        state_a.advance(bar)
        state_b.advance(bar)
    assert state_a.features == state_b.features
    assert state_a.swing_points == state_b.swing_points
    assert state_a.swing_discards == state_b.swing_discards
    assert state_a.effort(5, 9) == state_b.effort(5, 9)
    assert state_a.oi_z_window(5, 9) == state_b.oi_z_window(5, 9)

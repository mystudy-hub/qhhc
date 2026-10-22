"""Tests for range.py (spec 5.1-5.6). Constructed fixtures only."""

from datetime import datetime

import pytest

from cta_lab.wyckoff_v4.indicators import SwingKind, SwingPoint
from cta_lab.wyckoff_v4.params import CreekMode, WyckoffV4Params
from cta_lab.wyckoff_v4.range import (
    DATA_GAP,
    ER_TOO_HIGH,
    INTERIOR_FRACTION,
    NotImplementedMarker,
    RangeBar,
    RangeRejection,
    TOUCHES,
    TradingRange,
    WIDTH_TOO_LARGE,
    WIDTH_TOO_SMALL,
    checked_internal_creek,
    checked_internal_ice,
    establish_path_a,
    establish_path_b,
    greedy_touch_positions,
    internal_creek,
    internal_ice,
    make_trend_snapshot,
    ols_slope,
    should_expire,
    should_retire,
    should_supersede,
    sma,
    trend_at,
    trend_at_detailed,
)
from cta_lab.wyckoff_v4.types import RangeState, TrendState

T0 = datetime(2024, 1, 2, 9, 30)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def trend_params() -> WyckoffV4Params:
    return WyckoffV4Params(
        trend_window=4,
        trend_sma_fast=3,
        trend_sma_slow=5,
        trend_min_displacement_atr=1.0,
        trend_min_abs_slope_atr=0.05,
        trend_min_er=0.30,
    )


def range_params(**overrides) -> WyckoffV4Params:
    base = dict(
        generic_range_window=8,
        generic_range_max_er=0.25,
        range_interior_close_fraction=0.80,
        range_min_touches_per_side=2,
        touch_min_separation_bars=3,
        range_min_width_atr=3.0,
        range_max_width_atr=12.0,
        range_max_age=200,
        range_stale_min_age=60,
        range_stale_bars=40,
    )
    base.update(overrides)
    return WyckoffV4Params(**base)


def swing(kind, index, price, confirm_bars=3):
    return SwingPoint(
        kind=kind,
        index=index,
        price=price,
        bar_start=T0,
        confirmed_at_index=index + confirm_bars,
        usable_from_index=index + confirm_bars + 1,
    )


def make_range(s=100.0, r=110.0, a_ref=2.0, origin=1, established=8):
    params = range_params()
    out = establish_path_a(
        origin, established, s, r, a_ref, params, price_resolution=0.01
    )
    assert isinstance(out, TradingRange)
    return out, params


# A path-B happy-path window: 9 bars (window + 1 prior close), atr_ref=2.0,
# price_resolution=0.01 => epsilon = max(0.02, 0.2) = 0.2; S=100, R=110, W=10.
# Support touches at window positions 0 and 6, resistance at 3 and 7.
HAPPY_BARS = [
    RangeBar(0, 106.0, 104.0, 105.0),   # prior bar (only its close feeds ER)
    RangeBar(1, 101.0, 100.0, 100.5),   # touches support
    RangeBar(2, 106.0, 103.0, 104.5),
    RangeBar(3, 107.0, 104.0, 105.0),
    RangeBar(4, 110.0, 108.0, 109.0),   # touches resistance
    RangeBar(5, 107.0, 104.0, 105.5),
    RangeBar(6, 106.0, 103.0, 104.0),
    RangeBar(7, 102.0, 100.0, 101.0),   # touches support
    RangeBar(8, 110.0, 105.0, 106.5),   # touches resistance
]


def path_b(params=None, bars=None, atr_ref=2.0):
    return establish_path_b(
        bars if bars is not None else HAPPY_BARS,
        params if params is not None else range_params(),
        atr_ref,
        price_resolution=0.01,
    )


# ---------------------------------------------------------------------------
# 5.1 trend_at
# ---------------------------------------------------------------------------


class TestTrendAt:
    def test_clear_downtrend(self):
        closes = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]
        assert trend_at(closes, 1.0, trend_params()) == TrendState.DOWN

    def test_clear_uptrend(self):
        closes = [95.0, 96.0, 97.0, 98.0, 99.0, 100.0]
        assert trend_at(closes, 1.0, trend_params()) == TrendState.UP

    def test_flat_series_is_neutral_not_up(self):
        closes = [100.0] * 6
        state = trend_at(closes, 1.0, trend_params())
        assert state == TrendState.NEUTRAL
        assert state != TrendState.UP

    def test_insufficient_history_is_unknown(self):
        closes = [100.0, 99.0, 98.0, 97.0]  # need max(5, 4+1)=5
        result = trend_at_detailed(closes, 1.0, trend_params())
        assert result.state == TrendState.UNKNOWN
        assert result.reason == "history_insufficient"

    def test_atr_unavailable_is_unknown(self):
        closes = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]
        result = trend_at_detailed(closes, None, trend_params())
        assert result.state == TrendState.UNKNOWN
        assert result.reason == "atr_unavailable"

    def test_atr_zero_is_unknown(self):
        closes = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]
        result = trend_at_detailed(closes, 0.0, trend_params())
        assert result.state == TrendState.UNKNOWN
        assert result.reason == "atr_zero"

    def test_missing_one_trend_condition_blocks_down(self):
        # displacement satisfied, slope satisfied, but close above SMA fast.
        closes = [100.0, 99.0, 98.0, 97.0, 96.0, 97.5]
        assert trend_at(closes, 1.0, trend_params()) == TrendState.NEUTRAL

    def test_er_denominator_zero_defined_as_zero(self):
        # n price changes all zero except the first window change.
        closes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        state = trend_at(closes, 1.0, trend_params())
        assert state == TrendState.NEUTRAL  # ER=0 < trend_min_er

    def test_sma_and_ols_helpers(self):
        assert sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5
        assert sma([1.0], 2) is None
        assert ols_slope([1.0, 2.0, 3.0]) == 1.0
        assert ols_slope([3.0, 1.0]) == -2.0
        assert ols_slope([1.0]) is None


class TestTrendSnapshot:
    def test_both_fields_store_the_same_value(self):
        closes = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]
        snap = make_trend_snapshot(5, closes, 1.0, trend_params(), T0)
        assert snap.asof_bar == 5
        assert snap.trend_now == TrendState.DOWN
        assert snap.context_for_next_bar == TrendState.DOWN
        assert snap.trend_now is not None
        assert snap.context_available_at == T0


# ---------------------------------------------------------------------------
# 5.2/5.3 TradingRange and path A
# ---------------------------------------------------------------------------


class TestPathA:
    def test_happy_path_fields(self):
        rng, _ = make_range()
        assert rng.S == 100.0
        assert rng.R == 110.0
        assert rng.M == 105.0
        assert rng.W == 10.0
        assert rng.A_ref == 2.0
        # epsilon = max(2*0.01, 0.10*2.0)
        assert rng.epsilon == pytest.approx(0.2)
        assert rng.state == RangeState.ACTIVE
        assert rng.parent_range_id is None
        assert rng.origin_bar == 1
        assert rng.established_bar == 8

    def test_width_too_large_rejected(self):
        out = establish_path_a(1, 8, 90.0, 130.0, 2.0, range_params(), 0.01)
        assert isinstance(out, RangeRejection)
        assert out.reason == WIDTH_TOO_LARGE  # W=40 > 12*2

    def test_width_too_small_rejected(self):
        out = establish_path_a(1, 8, 100.0, 103.0, 2.0, range_params(), 0.01)
        assert isinstance(out, RangeRejection)
        assert out.reason == WIDTH_TOO_SMALL  # W=3 < 3*2

    def test_deterministic_range_id(self):
        a, _ = make_range()
        b, _ = make_range()
        assert a.range_id == b.range_id
        c = establish_path_a(1, 9, 100.0, 110.0, 2.0, range_params(), 0.01)
        assert isinstance(c, TradingRange)
        assert c.range_id != a.range_id

    def test_age_and_bars_since_established(self):
        rng, _ = make_range(origin=1, established=8)
        assert rng.age(8) == 8  # origin inclusive
        assert rng.age(10) == 10
        assert rng.bars_since_established(10) == 2

    def test_frozen_fields(self):
        rng, _ = make_range()
        with pytest.raises(Exception):
            rng.S = 99.0  # type: ignore[misc]

    def test_next_version_resets_counters_and_keeps_id(self):
        rng, _ = make_range()
        bumped = rng.with_outside_streaks(3, 0).next_version(RangeState.ACTIVE)
        assert bumped.version == rng.version + 1
        assert bumped.range_id == rng.range_id
        assert bumped.outside_up_streak == 0
        assert bumped.outside_down_streak == 0
        assert bumped.S == rng.S and bumped.epsilon == rng.epsilon


# ---------------------------------------------------------------------------
# 5.4 path B
# ---------------------------------------------------------------------------


class TestPathB:
    def test_happy_path(self):
        out = path_b()
        assert isinstance(out, TradingRange)
        assert out.S == 100.0
        assert out.R == 110.0
        assert out.W == 10.0
        assert out.origin_bar == 1  # first window bar
        assert out.established_bar == 8
        assert out.epsilon == pytest.approx(0.2)

    def test_too_few_touches_rejected(self):
        bars = list(HAPPY_BARS)
        bars[7] = RangeBar(7, 106.0, 103.0, 104.0)  # remove 2nd support touch
        out = path_b(bars=bars)
        assert isinstance(out, RangeRejection)
        assert out.reason == TOUCHES
        assert "support=1" in out.detail

    def test_interior_fraction_too_low_rejected(self):
        bars = list(HAPPY_BARS)
        bars[2] = RangeBar(2, 106.0, 103.0, 99.5)  # close below S+eps
        bars[3] = RangeBar(3, 107.0, 104.0, 99.0)
        out = path_b(bars=bars)
        assert isinstance(out, RangeRejection)
        assert out.reason == INTERIOR_FRACTION

    def test_data_gap_in_window_rejected(self):
        bars = list(HAPPY_BARS)
        bars[5] = RangeBar(5, 0.0, 0.0, 0.0, is_gap=True)
        out = path_b(bars=bars)
        assert isinstance(out, RangeRejection)
        assert out.reason == DATA_GAP

    def test_er_too_high_rejected(self):
        bars = [RangeBar(i, float(101 + i), float(100 + i), float(100 + i)) for i in range(9)]
        out = path_b(bars=bars)
        assert isinstance(out, RangeRejection)
        assert out.reason == ER_TOO_HIGH

    def test_width_too_large_rejected(self):
        bars = [RangeBar(i, 100.0 + 4.0 * i, 96.0 + 4.0 * i, 98.0 + 4.0 * i) for i in range(9)]
        out = path_b(bars=bars)
        assert isinstance(out, RangeRejection)
        assert out.reason == WIDTH_TOO_LARGE  # W=32 > 12*2

    def test_insufficient_bars_raises(self):
        bars = HAPPY_BARS[:8]
        with pytest.raises(ValueError):
            path_b(bars=bars)

    def test_atr_ref_unavailable_rejected(self):
        out = path_b(atr_ref=None)
        assert isinstance(out, RangeRejection)

    def test_parent_range_id_recorded(self):
        old, _ = make_range()
        out = establish_path_b(
            HAPPY_BARS, range_params(), 2.0, 0.01, parent_range_id=old.range_id
        )
        assert isinstance(out, TradingRange)
        assert out.parent_range_id == old.range_id


class TestGreedyTouches:
    def test_two_touches_two_bars_apart_count_once(self):
        bars = [
            RangeBar(0, 110.0, 109.0, 109.5),  # touches area at pos 0
            RangeBar(1, 105.0, 104.0, 104.5),  # fully outside
            RangeBar(2, 110.0, 109.0, 109.5),  # touches area at pos 2
            RangeBar(3, 105.0, 104.0, 104.5),
        ]
        assert greedy_touch_positions(bars, 110.0, 0.2, 3) == (0,)

    def test_isolated_long_wick_counts_once(self):
        # One bar whose wick reaches the level; nothing else near it.
        bars = [
            RangeBar(0, 104.0, 103.0, 103.5),
            RangeBar(1, 110.0, 103.0, 103.5),  # isolated long upper wick
            RangeBar(2, 104.0, 103.0, 103.5),
            RangeBar(3, 104.0, 103.0, 103.5),
        ]
        assert greedy_touch_positions(bars, 110.0, 0.2, 3) == (1,)

    def test_no_bar_outside_between_candidates_blocks_second(self):
        # Candidates at 0 and 4 (separation ok) but every bar in between
        # also touches the area => the second does not count.
        bars = [
            RangeBar(0, 110.0, 109.5, 109.7),
            RangeBar(1, 110.0, 109.5, 109.7),
            RangeBar(2, 110.0, 109.5, 109.7),
            RangeBar(3, 110.0, 109.5, 109.7),
            RangeBar(4, 110.0, 109.5, 109.7),
        ]
        assert greedy_touch_positions(bars, 110.0, 0.2, 3) == (0,)

    def test_well_separated_touches_all_count(self):
        bars = [
            RangeBar(0, 110.0, 109.0, 109.5),
            RangeBar(1, 105.0, 104.0, 104.5),
            RangeBar(2, 105.0, 104.0, 104.5),
            RangeBar(3, 110.0, 109.0, 109.5),
        ]
        assert greedy_touch_positions(bars, 110.0, 0.2, 3) == (0, 3)


# ---------------------------------------------------------------------------
# 5.5 outside streaks and lifecycle
# ---------------------------------------------------------------------------


class TestOutsideStreaks:
    def test_cross_out_increments(self):
        rng, _ = make_range()  # S=100, R=110, eps=0.2
        assert rng.update_outside_streaks(110.5) == (1, 0)
        up1 = rng.with_outside_streaks(1, 0)
        assert up1.update_outside_streaks(111.0) == (2, 0)
        assert up1.update_outside_streaks(99.0) == (0, 1)
        assert up1.update_outside_streaks(101.0) == (0, 0)

    def test_neutral_inside_band_resets(self):
        rng = make_range()[0].with_outside_streaks(4, 3)
        # Inside the tolerance band [S-eps, R+eps] resets both sides.
        assert rng.update_outside_streaks(105.0) == (0, 0)
        assert rng.update_outside_streaks(110.1) == (0, 0)  # within R+eps
        assert rng.update_outside_streaks(99.9) == (0, 0)  # within S-eps

    def test_deep_fraction_close_still_counts_outside(self):
        rng = make_range()[0].with_outside_streaks(0, 2)
        assert rng.update_outside_streaks(80.0) == (0, 3)

    def test_update_does_not_mutate(self):
        rng, _ = make_range()
        rng.update_outside_streaks(110.5)
        assert rng.outside_up_streak == 0


class TestLifecycle:
    def _params(self):
        return range_params(range_max_age=10, range_stale_min_age=5, range_stale_bars=3)

    def test_expire(self):
        rng, _ = make_range(origin=1, established=8)
        params = self._params()
        assert should_expire(rng, 10, params, clear_outside_trend=False)  # age 10
        assert not should_expire(rng, 9, params, clear_outside_trend=False)  # age 9
        assert not should_expire(rng, 10, params, clear_outside_trend=True)

    def test_supersede(self):
        rng, _ = make_range(origin=1, established=8)
        params = self._params()
        assert should_supersede(rng, 6, params, last_touch_index=2, interior_range_completed=True)
        assert not should_supersede(
            rng, 6, params, last_touch_index=4, interior_range_completed=True
        )  # touch at 4 > 6-3
        assert not should_supersede(rng, 6, params, last_touch_index=None, interior_range_completed=False)
        # age at current=4 is 4 < range_stale_min_age=5
        assert not should_supersede(rng, 4, params, last_touch_index=None, interior_range_completed=True)

    def test_retire(self):
        assert should_retire(True, True)
        assert not should_retire(True, False)
        assert not should_retire(False, True)


# ---------------------------------------------------------------------------
# 5.6 Creek / Ice
# ---------------------------------------------------------------------------


class TestCreekIce:
    def test_creek_picks_most_recent_usable_swing_in_band(self):
        rng, _ = make_range()  # M=105, R=110, eps=0.2, origin=1
        points = [
            swing(SwingKind.HIGH, 2, 108.0),   # in band, older
            swing(SwingKind.HIGH, 5, 107.0),   # in band, more recent
            swing(SwingKind.LOW, 3, 102.0),    # wrong kind
            swing(SwingKind.HIGH, 6, 112.0),   # above R+eps
        ]
        got = internal_creek(points, rng, current_bar=9)
        assert got is not None and got.index == 5

    def test_creek_excludes_before_origin_and_unusable(self):
        rng, _ = make_range(origin=1)
        at_origin = swing(SwingKind.HIGH, 1, 107.0)
        not_usable_yet = swing(SwingKind.HIGH, 7, 108.0)  # usable_from=11
        assert internal_creek([at_origin, not_usable_yet], rng, current_bar=9) is None
        # becomes usable one bar later
        got = internal_creek([not_usable_yet], rng, current_bar=11)
        assert got is not None and got.index == 7

    def test_creek_none_when_band_empty(self):
        rng, _ = make_range()
        points = [swing(SwingKind.HIGH, 4, 104.0)]  # below M
        assert internal_creek(points, rng, current_bar=9) is None

    def test_ice_picks_most_recent_usable_swing_low_in_band(self):
        rng, _ = make_range()  # S=100, M=105, eps=0.2
        points = [
            swing(SwingKind.LOW, 2, 101.0),
            swing(SwingKind.LOW, 5, 102.5),
            swing(SwingKind.LOW, 6, 98.0),    # below S-eps
            swing(SwingKind.HIGH, 3, 108.0),  # wrong kind
        ]
        got = internal_ice(points, rng, current_bar=9)
        assert got is not None and got.index == 5

    def test_ice_none_when_absent(self):
        rng, _ = make_range()
        assert internal_ice([], rng, current_bar=9) is None

    def test_checked_accessors_range_only_not_implemented(self):
        rng, _ = make_range()
        points = [swing(SwingKind.HIGH, 4, 108.0)]
        creek = checked_internal_creek(points, rng, 9, CreekMode.RANGE_ONLY)
        ice = checked_internal_ice(points, rng, 9, CreekMode.RANGE_ONLY)
        assert isinstance(creek, NotImplementedMarker)
        assert isinstance(ice, NotImplementedMarker)
        assert creek.feature == "internal_creek"

    def test_checked_accessors_range_and_internal(self):
        rng, _ = make_range()
        points = [swing(SwingKind.HIGH, 4, 108.0)]
        creek = checked_internal_creek(points, rng, 9, CreekMode.RANGE_AND_INTERNAL)
        assert isinstance(creek, SwingPoint)
        assert creek.index == 4


# ---------------------------------------------------------------------------
# boundary touch helpers
# ---------------------------------------------------------------------------


class TestBoundaryTouches:
    def test_touch_predicates(self):
        rng, _ = make_range()  # S=100, R=110, eps=0.2
        assert rng.touches_support(101.0, 100.0)
        assert rng.touches_support(100.1, 99.0)
        assert not rng.touches_support(101.0, 100.3)
        assert rng.touches_resistance(110.0, 109.0)
        assert rng.touches_resistance(111.0, 109.9)
        assert not rng.touches_resistance(109.7, 108.0)
        assert rng.touches_any_boundary(110.0, 109.9)
        assert not rng.touches_any_boundary(108.0, 104.0)

    def test_validate_width(self):
        rng, params = make_range()
        assert rng.validate_width(params) is None
        narrow = make_range(s=100.0, r=103.0, a_ref=1.0)[0]  # W=3 at A_ref=1
        assert narrow.validate_width(range_params(range_min_width_atr=4.0)) == WIDTH_TOO_SMALL
        assert narrow.validate_width(range_params()) is None

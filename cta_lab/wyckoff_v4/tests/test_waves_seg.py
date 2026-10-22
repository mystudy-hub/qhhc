"""Wave segmentation acceptance cases (spec 8.1 / 8.2).

All fixtures are constructed in-memory; no database or network access.
Prices are chosen so every ATR / threshold is exactly predictable
(atr_period=2, first bar commits no TR, prior-state baselines per F-16).
"""

import statistics
from datetime import datetime, timedelta

import pytest

from cta_lab.wyckoff_v4.contract import BarV4
from cta_lab.wyckoff_v4.indicators import (
    DATA_GAP_FLAG,
    IndicatorState,
    NotEvaluableReason,
)
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.waves import (
    CONSTITUENT_CHANGE_FLAG,
    WaveStatus,
    WaveState,
    run_segmentation,
)

# --- helpers ---------------------------------------------------------------


def daily_bar(i, o, h, l, c, v, *, flags=frozenset()):
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
        quality_flags=flags,
    )


PARAMS = WyckoffV4Params(
    atr_period=2,
    volume_period=3,
    volume_min_samples=3,
    oi_window=3,
    wave_reversal_atr=1.0,
)


def drive(bars, params=PARAMS):
    """Run the causal pipeline; returns (waves, indicators, features)."""
    indicators = IndicatorState(params)
    waves = WaveState(params)
    features = []
    for bar in bars:
        features.append(indicators.advance(bar))
        waves.feed(features[-1])
    return waves, indicators, features


# Scenario A: one full up wave (determination at bar 3, equality reversal
# at bar 5) followed by the forming down wave.
A_BARS = [
    daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),   # anchor close
    daily_bar(1, 100.0, 100.6, 99.6, 100.2, 110),
    daily_bar(2, 100.5, 103.0, 100.4, 102.8, 120),
    daily_bar(3, 102.8, 104.0, 102.3, 102.8, 130),
    daily_bar(4, 101.5, 102.0, 101.0, 101.5, 140),
    daily_bar(5, 101.0, 101.4, 100.5, 100.9, 150),  # C = E - rho exactly
]
# Committed TRs: TR1 = 1.0, TR2 = 2.8, TR3 = 1.7, TR4 = 1.8, TR5 = 1.0
# features[3].atr = mean(TR1, TR2) = 1.9  -> wave 1 rho / A0
# features[4].atr = mean(TR2, TR3) = 2.25 -> wave 2 rho / A0


# Scenario B: down mirror with equality reversal at bar 5.
B_BARS = [
    daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),
    daily_bar(1, 100.0, 100.4, 99.4, 99.8, 110),
    daily_bar(2, 99.0, 99.5, 96.9, 97.0, 120),
    daily_bar(3, 97.2, 97.8, 96.7, 97.2, 130),
    daily_bar(4, 98.0, 98.5, 97.9, 98.0, 140),
    daily_bar(5, 98.9, 99.4, 98.8, 98.95, 150),     # C = E + rho exactly
]
# TR1 = 1.0, TR2 = 2.9, TR3 = 1.1, TR4 = 1.3
# features[3].atr = 1.95 -> wave 1 rho; features[4].atr = 2.0 -> wave 2 rho


# Scenario D: C04 shape -- a later up wave that ends below its own start
# reference (negative signed_end_displacement).
D_BARS = [
    daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),
    daily_bar(1, 100.0, 100.4, 99.4, 99.8, 110),
    daily_bar(2, 99.5, 99.9, 96.9, 97.0, 120),
    daily_bar(3, 97.0, 98.6, 96.6, 97.2, 130),
    daily_bar(4, 98.5, 99.1, 98.4, 99.0, 140),      # equality up reversal
    daily_bar(5, 98.8, 99.2, 98.6, 98.8, 150),
    daily_bar(6, 98.4, 98.9, 98.3, 98.5, 160),
    daily_bar(7, 97.0, 97.5, 96.9, 97.1, 170),
    daily_bar(8, 96.6, 97.0, 96.4, 96.8, 180),      # down reversal of wave 2
]
# TR1 = 1.0, TR2 = 3.0, TR3 = 1.0, TR4 = 1.9, ...
# features[3].atr = 2.0 -> wave 1 AND wave 2 rho (wave 2 reference bar = 3)


# --- 8.1 segmentation ------------------------------------------------------


def test_initializing_wave_before_rho_displacement():
    bars = [
        daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),
        daily_bar(1, 100.0, 100.6, 99.6, 100.2, 110),
        daily_bar(2, 100.4, 101.2, 100.3, 101.0, 120),
        daily_bar(3, 101.0, 101.5, 100.5, 100.5, 130),
        daily_bar(4, 100.5, 101.1, 100.1, 100.6, 140),
    ]
    waves, _, features = drive(bars)
    # rho freezes at the first available ATR (features[3]).
    assert features[3].atr_baseline.value == pytest.approx(1.0)
    assert waves.completed_waves == ()
    current = waves.current_wave
    assert current is not None
    assert current.direction == 0
    assert current.status is WaveStatus.FORMING
    assert current.start_reference_close == pytest.approx(100.0)
    assert current.extreme_close is None
    assert current.end_close == pytest.approx(100.6)
    assert current.bar_count == 4          # bars 1..4; anchor bar excluded
    assert current.raw_volume_sum == pytest.approx(500.0)  # anchor excluded
    assert current.available_at is None
    assert current.peak_excursion is None
    assert current.signed_end_displacement is None
    assert current.reversal_threshold == pytest.approx(1.0)
    assert current.quality_limited is False
    assert current.constituent_change is False


def test_first_direction_determination_including_equality():
    bars = [
        daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),
        daily_bar(1, 100.0, 100.6, 99.6, 100.2, 110),
        daily_bar(2, 100.4, 101.2, 100.3, 101.0, 120),
        daily_bar(3, 101.0, 101.5, 100.5, 101.0, 130),
    ]
    # rho = features[3].atr = mean(TR1, TR2) = 1.0 and |C3 - anchor| = 1.0:
    # equality satisfies determination ("reaches rho").
    waves, _, _ = drive(bars)
    current = waves.current_wave
    assert current.direction == 1
    assert current.status is WaveStatus.FORMING
    assert current.extreme_close == pytest.approx(101.0)
    assert current.atr_at_start == pytest.approx(1.0)
    assert current.available_at == bars[3].bar_end
    assert current.wave_id == "w-0001"
    assert waves.completed_waves == ()


def test_initializing_state_before_any_atr_exists():
    # After two bars only: no ATR yet, wave still initializing (no rho).
    waves, _, _ = drive(A_BARS[:2])
    current = waves.current_wave
    assert current.direction == 0
    assert current.reversal_threshold is None
    assert current.atr_at_start is None
    assert current.bar_count == 1


def test_up_reversal_equality_and_volume_attribution():
    waves, _, features = drive(A_BARS)
    assert len(waves.completed_waves) == 1
    r = waves.completed_waves[0]
    assert r.direction == 1
    assert r.status is WaveStatus.COMPLETED
    assert r.wave_id == "w-0001"
    assert (r.start_index, r.end_index, r.reference_index) == (1, 4, 0)
    assert r.confirmed_at_index == 5
    assert r.confirmed_at == A_BARS[5].bar_end
    assert r.available_at == A_BARS[5].bar_end
    assert r.start_time == A_BARS[1].bar_start
    assert r.end_time == A_BARS[4].bar_start
    # Old wave ends at t-1; reversal bar volume belongs to the NEW wave.
    assert r.raw_volume_sum == pytest.approx(500.0)  # 110+120+130+140
    assert r.end_close == pytest.approx(101.5)       # C_(t-1)
    assert r.extreme_close == pytest.approx(102.8)
    assert r.bar_count == 4
    # Confirmation-bar fact: E - C_t >= rho (equality included here).
    assert r.extreme_close - A_BARS[5].close == pytest.approx(r.reversal_threshold)
    # rho frozen from the first available ATR (fallback at bar 3).
    assert r.reversal_threshold == pytest.approx(1.9)
    assert r.atr_at_start == pytest.approx(1.9)

    current = waves.current_wave
    assert current.direction == -1
    assert current.start_index == 5
    assert current.start_reference_close == pytest.approx(101.5)  # C_(t-1)
    assert current.raw_volume_sum == pytest.approx(150.0)         # bar t volume
    assert current.end_close == pytest.approx(100.9)
    assert current.extreme_close == pytest.approx(100.9)
    assert current.bar_count == 1
    assert current.wave_id == "w-0002"


def test_down_reversal_mirror():
    waves, _, features = drive(B_BARS)
    assert len(waves.completed_waves) == 1
    r = waves.completed_waves[0]
    assert r.direction == -1
    assert (r.start_index, r.end_index) == (1, 4)
    assert r.confirmed_at_index == 5
    assert r.extreme_close == pytest.approx(97.0)      # running min E
    assert r.end_close == pytest.approx(98.0)
    assert r.reversal_threshold == pytest.approx(1.95)  # mean(TR1, TR2)
    assert r.raw_volume_sum == pytest.approx(500.0)
    assert B_BARS[5].close - r.extreme_close == pytest.approx(
        r.reversal_threshold
    )  # C_t - E == rho (equality reversal)
    current = waves.current_wave
    assert current.direction == 1
    assert current.start_reference_close == pytest.approx(98.0)
    assert current.reversal_threshold == pytest.approx(
        features[5].atr_baseline.value
    )  # New wave uses ATR_(t-1), already available at reversal t.
    assert current.reversal_threshold == pytest.approx(1.4125)
    assert current.raw_volume_sum == pytest.approx(150.0)


def test_rho_frozen_per_wave_from_reference_bar_atr():
    waves, _, features = drive(A_BARS)
    r1 = waves.completed_waves[0]
    r2 = waves.current_wave
    # Wave 1 froze via the fallback (first available ATR, bar 3).
    assert r1.reversal_threshold == pytest.approx(
        features[3].atr_baseline.value
    )
    # Wave 2 froze from the ATR of the bar BEFORE its first bar (bar 4).
    assert r2.reversal_threshold == pytest.approx(
        features[4].atr_baseline.value
    )
    # Different ATRs at the two freeze points -> different thresholds.
    assert r1.reversal_threshold != pytest.approx(r2.reversal_threshold)


# --- 8.2 fields ------------------------------------------------------------


def test_completed_wave_fields_and_giveback_invariant():
    waves, _, _ = drive(A_BARS)
    r = waves.completed_waves[0]
    a0 = r.atr_at_start
    assert a0 == pytest.approx(1.9)
    assert r.peak_excursion == pytest.approx(2.8 / 1.9)
    assert r.signed_end_displacement == pytest.approx(1.5 / 1.9)
    assert r.giveback == pytest.approx(1.3 / 1.9)
    assert r.path_distance == pytest.approx(4.1 / 1.9)
    assert r.directional_efficiency == pytest.approx(1.5 / 4.1)
    assert r.wave_speed == pytest.approx((2.8 / 1.9) / 4)
    # Spec 8.2 completed-wave invariant: giveback * A0 < reversal_threshold.
    assert r.giveback * a0 < r.reversal_threshold
    assert r.extreme_close - r.start_reference_close == pytest.approx(2.8)


def test_giveback_invariant_holds_across_scenarios():
    for bars in (A_BARS, B_BARS, D_BARS):
        waves, _, _ = drive(bars)
        for r in waves.completed_waves:
            assert r.status is WaveStatus.COMPLETED
            assert r.peak_excursion > 0                     # 8.2 theorem
            assert r.giveback is not None
            assert r.giveback * r.atr_at_start < r.reversal_threshold


def test_negative_signed_end_displacement_c04_shape():
    waves, indicators, features = drive(D_BARS)
    assert len(waves.completed_waves) == 2
    r1, r2 = waves.completed_waves
    # Wave 1: down wave, closes [99.8, 97.0, 97.2], reference 100.
    assert r1.direction == -1
    assert r1.reversal_threshold == pytest.approx(2.0)
    assert r1.peak_excursion == pytest.approx(3.0 / 2.0)
    assert r1.signed_end_displacement == pytest.approx(2.8 / 2.0)
    # Wave 2: up wave that ends below its start reference 97.2.
    assert r2.direction == 1
    assert (r2.start_index, r2.end_index) == (4, 7)
    assert r2.start_reference_close == pytest.approx(97.2)
    assert r2.extreme_close == pytest.approx(99.0)
    assert r2.end_close == pytest.approx(97.1)
    assert r2.signed_end_displacement < 0
    assert r2.signed_end_displacement == pytest.approx(-0.05)
    assert r2.peak_excursion == pytest.approx(0.9)
    assert r2.giveback == pytest.approx(0.95)
    assert r2.giveback * r2.atr_at_start == pytest.approx(1.9)
    assert r2.giveback * r2.atr_at_start < r2.reversal_threshold
    assert r2.path_distance == pytest.approx(3.7 / 2.0)
    assert r2.directional_efficiency == pytest.approx(-0.05 / 1.85)
    assert r2.wave_speed == pytest.approx(0.9 / 4)
    assert r2.bar_count == 4
    assert r2.raw_volume_sum == pytest.approx(620.0)
    # Effort fields equal the foundation window functions over a..e.
    assert r2.wave_activity_rate == indicators.effort(4, 7)
    assert r2.wave_cum_effort == indicators.cum_effort(4, 7)
    assert r2.wave_oiz == indicators.oi_z_window(4, 7)
    assert r2.wave_activity_rate.is_available
    expected_bv = sum(features[i].bv_baseline.value for i in range(4, 8))
    assert r2.baseline_volume_sum == pytest.approx(expected_bv)
    expected_ns = statistics.fmean(
        [features[i].ns.value for i in range(4, 8)]
    )
    assert r2.mean_bar_ns == pytest.approx(expected_ns)


def test_early_wave_effort_not_evaluable_without_baselines():
    # Wave 1 covers bars 1..4 where bar 1 has no Bv history yet: the
    # effort features stay NOT_EVALUABLE (never zero-filled).
    waves, _, _ = drive(A_BARS)
    r = waves.completed_waves[0]
    assert not r.wave_activity_rate.is_available
    assert r.wave_cum_effort.reason is NotEvaluableReason.BV_UNAVAILABLE
    assert r.baseline_volume_sum is None


# --- quality flags (8.1) ---------------------------------------------------


def test_data_gap_flags_wave_quality_limited_but_record_kept():
    bars = [
        daily_bar(0, 100.0, 100.5, 99.5, 100.0, 100),
        daily_bar(1, 100.0, 100.6, 99.6, 100.2, 110),
        daily_bar(2, 100.5, 103.0, 100.4, 102.8, 120),
        daily_bar(3, 100.0, 100.0, 100.0, 100.0, 0.0,
                  flags=frozenset({DATA_GAP_FLAG})),
        daily_bar(4, 102.8, 103.3, 102.3, 102.8, 130),
        daily_bar(5, 101.0, 101.5, 100.9, 100.9, 140),  # C = E - rho = 100.9
    ]
    waves, _, features = drive(bars)
    # rho froze at bar 4 (the gap bar carries no ATR): mean(TR1, TR2) = 1.9.
    assert features[4].atr_baseline.value == pytest.approx(1.9)
    assert len(waves.completed_waves) == 1
    r = waves.completed_waves[0]
    assert r.quality_limited is True                 # excluded from stats
    assert r.constituent_change is False
    assert r.start_index == 1
    assert r.end_index == 4                          # gap bar inside a..e
    assert r.bar_count == 4
    assert r.raw_volume_sum == pytest.approx(360.0)  # gap bar volume excluded
    assert r.extreme_close == pytest.approx(102.8)
    assert r.end_close == pytest.approx(102.8)       # last VALID close
    assert r.reversal_threshold == pytest.approx(1.9)
    # Windowed fields are not evaluable, never silently zero-filled.
    assert r.wave_activity_rate.reason is NotEvaluableReason.DATA_GAP
    assert r.wave_cum_effort.reason is NotEvaluableReason.DATA_GAP
    assert r.baseline_volume_sum is None
    assert r.path_distance is None
    assert r.directional_efficiency is None
    assert r.mean_bar_ns is None
    # New wave reference skips the gap bar: last valid close = C4.
    current = waves.current_wave
    assert current.start_reference_close == pytest.approx(102.8)
    assert current.direction == -1


def test_constituent_change_bar_kept_with_flag():
    bars = [
        b if i != 5
        else daily_bar(
            5, 98.8, 99.2, 98.6, 98.8, 150,
            flags=frozenset({CONSTITUENT_CHANGE_FLAG}),
        )
        for i, b in enumerate(D_BARS)
    ]
    waves, indicators, _ = drive(bars)
    r1, r2 = waves.completed_waves
    assert r1.constituent_change is False
    assert r1.quality_limited is False
    assert r2.constituent_change is True            # kept WITH the flag
    assert r2.quality_limited is False              # NOT excluded
    # Fields remain fully evaluable (constituent_change does not pause).
    assert r2.wave_activity_rate == indicators.effort(4, 7)
    assert r2.wave_activity_rate.is_available
    assert r2.mean_bar_ns is not None


# --- determinism / robustness ----------------------------------------------


def test_determinism_two_runs_identical_records():
    first = drive(D_BARS)
    second = drive(D_BARS)
    assert first[0].completed_waves == second[0].completed_waves
    assert first[0].current_wave == second[0].current_wave
    assert first[0].waves == second[0].waves
    assert [r.wave_id for r in first[0].waves] == [
        "w-0001", "w-0002", "w-0003",
    ]


def test_feed_rejects_out_of_order_features():
    import dataclasses

    indicators = IndicatorState(PARAMS)
    waves = WaveState(PARAMS)
    f0 = indicators.advance(A_BARS[0])
    f1 = indicators.advance(A_BARS[1])
    waves.feed(f0)
    with pytest.raises(ValueError):
        waves.feed(f0)                                  # duplicate index
    with pytest.raises(ValueError):
        waves.feed(dataclasses.replace(f1, index=2))    # skipped index 1
    waves.feed(f1)                                      # in order: accepted


# --- 8.1 sensitivity helper -------------------------------------------------


SENSITIVITY_BARS = [
    daily_bar(0, 100.0, 100.2, 99.8, 100.0, 100),
    daily_bar(1, 100.0, 100.7, 99.9, 100.3, 110),
    daily_bar(2, 100.3, 101.6, 100.2, 101.2, 120),
    daily_bar(3, 101.2, 102.5, 101.1, 102.1, 130),
    daily_bar(4, 102.1, 103.2, 102.0, 103.0, 140),
    daily_bar(5, 103.0, 103.2, 101.8, 102.5, 150),
    daily_bar(6, 102.5, 103.0, 101.7, 102.0, 160),
    daily_bar(7, 102.0, 102.5, 101.2, 101.5, 170),   # giveback 1.5 from E
    daily_bar(8, 101.5, 102.5, 101.4, 102.0, 180),
    daily_bar(9, 102.0, 103.7, 101.9, 103.2, 190),
    daily_bar(10, 103.2, 104.2, 103.1, 104.0, 200),
    daily_bar(11, 104.0, 104.2, 102.9, 103.5, 210),
    daily_bar(12, 103.5, 104.0, 102.5, 102.8, 220),
    daily_bar(13, 102.8, 103.0, 101.5, 102.1, 230),
    daily_bar(14, 102.1, 102.6, 100.9, 101.6, 240),  # giveback 2.4 from E
    daily_bar(15, 101.6, 102.6, 101.5, 102.0, 250),
    daily_bar(16, 102.0, 103.1, 101.9, 103.0, 260),
    daily_bar(17, 103.0, 104.1, 102.9, 104.0, 270),
    daily_bar(18, 104.0, 105.2, 103.9, 105.0, 280),
]


def test_run_segmentation_sensitivity_counts():
    counts = run_segmentation(SENSITIVITY_BARS, params=PARAMS)
    assert set(counts) == {1.0, 1.5, 2.0}
    # Higher thresholds can only merge waves: counts are non-increasing.
    assert counts[1.0] >= counts[1.5] >= counts[2.0]
    # The sensitivity analysis must actually separate the multipliers.
    assert counts[1.0] > counts[2.0]
    # Each count equals a direct WaveState run with the same multiplier.
    import dataclasses

    for multiplier, count in counts.items():
        params = dataclasses.replace(PARAMS, wave_reversal_atr=multiplier)
        waves, _, _ = drive(SENSITIVITY_BARS, params)
        assert count == len(waves.completed_waves)


def test_run_segmentation_is_deterministic():
    assert run_segmentation(SENSITIVITY_BARS, params=PARAMS) == (
        run_segmentation(SENSITIVITY_BARS, params=PARAMS)
    )

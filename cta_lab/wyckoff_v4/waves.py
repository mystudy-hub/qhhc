"""Causal wave segmentation and wave fields (spec sections 8.1 / 8.2).

Segmentation uses closes only (spec 8.1: volume is never used to confirm a
wave reversal -- using volume to split waves and then comparing volume
across those waves would be circular). Every wave freezes its own
reversal threshold ``rho = wave_reversal_atr * ATR_previous`` at wave
start; completed waves carry the full 8.2 field set. A forming wave may be
displayed but never enters statistics (8.1).

SPEC DECISIONS
--------------
- ATR_previous / A0: ``features[t].atr_baseline`` at new-wave creation t,
  which is ATR_(t-1), already committed before the new wave. A0 is that
  same frozen ATR value. If the baseline is not available when the
  wave starts (the anchor is usually stream bar 0, whose prior-state ATR
  cannot exist), rho freezes at the first subsequent bar whose
  ``atr_baseline`` is available; direction determination and reversal
  checks wait for the freeze (no zero fallback, F-19).
- Wave 1 spans stream bars ``anchor + 1 ..``: the anchor bar is the
  reference bar and is excluded, mirroring 8.1 "the anchor bar's volume
  does not belong to the first wave"; its volume is attributed to no wave.
  Every later wave's reference close is the close of the bar immediately
  before its first bar; that bar belongs to the previous wave.
- Completed-wave invariant (spec 8.2): with
  ``end_close = C_e`` and ``e = t - 1`` the strict inequality
  ``giveback * A0 < reversal_threshold`` always holds, because the
  reversal fires at the FIRST bar with ``C_t <= E - rho`` and therefore
  ``C_e > E - rho``. At the confirmation bar itself ``d*(E - C_t) >= rho``
  holds (equality satisfies reversal, 8.1).
- data_gap bars stay inside the wave's bar range (they extend end_index
  and bar_count), flag the wave ``quality_limited`` (the record is kept
  but the wave is excluded from direction statistics), contribute no
  close and no volume, and make windowed fields not evaluable: Effort /
  CumEffort carry the data_gap reason; baseline_volume_sum,
  path_distance, directional_efficiency and mean_bar_ns become None
  (never silently zero-filled).
- constituent_change bars stay in their wave; the wave is kept with the
  ``constituent_change`` flag (not excluded, 8.1).
- Direction determination: ``|C - anchor| >= rho`` ("reaches rho",
  equality included); direction is sign(C - anchor). While undetermined
  the wave reports direction 0 and status forming.
- If the bar immediately before a reversal bar is itself a data_gap bar,
  the new wave's start_reference_close is the last valid close before the
  reversal bar (normally ``C_(t-1)``).
- available_at: maximum bar_end/received_at of all contributing bars; for
  forming waves, the actual arrival of the bar that fixed the direction
  (None while the initial wave is still initializing).
- wave_speed divides by bar_count, which counts stream bars in ``a..e``
  (data_gap bars included).
- wave_oiz replicates ``IndicatorState.oi_z_window(a, e)`` from the stored
  per-bar OiFeatures (descriptive only, spec 8.2); per-bar not-evaluable
  reasons are propagated unchanged.
"""

import dataclasses
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from cta_lab.wyckoff_v4.contract import BarV4
from decimal import Decimal
from cta_lab.wyckoff_v4.indicators import (
    BarFeatures,
    IndicatorState,
    NotEvaluableReason,
    NumFeature,
    OiZWindow,
    cumulative_effort,
    effort_ratio,
)
from cta_lab.wyckoff_v4.params import WyckoffV4Params

CONSTITUENT_CHANGE_FLAG = "constituent_change"
"""Quality flag marking a bar whose index constituents changed (kept in
the wave, flagged on the WaveRecord, never excluded)."""

_SENSITIVITY_MULTIPLIERS = (1.0, 1.5, 2.0)


class WaveStatus(Enum):
    """Lifecycle of a wave (spec 8.1: forming waves never enter statistics)."""

    FORMING = "forming"
    COMPLETED = "completed"


@dataclass(frozen=True)
class WaveRecord:
    """A wave snapshot with the complete 8.2 field set.

    Stream indices refer to BarFeatures positions fed into WaveState.
    direction is +1 (up), -1 (down) or 0 while the initial wave is still
    initializing (reference displacement below rho). Float derived fields
    are None exactly when their inputs are not evaluable (F-19: never a
    silent fallback).
    """

    wave_id: str
    direction: int
    status: WaveStatus
    start_index: int
    end_index: int
    reference_index: int
    confirmed_at_index: int | None
    start_time: datetime
    end_time: datetime
    confirmed_at: datetime | None
    available_at: datetime | None
    start_reference_close: float
    extreme_close: float | None
    end_close: float | None
    reversal_threshold: float | None
    atr_at_start: float | None
    bar_count: int
    raw_volume_sum: float
    baseline_volume_sum: float | None
    wave_activity_rate: NumFeature
    wave_cum_effort: NumFeature
    peak_excursion: float | None
    signed_end_displacement: float | None
    giveback: float | None
    path_distance: float | None
    directional_efficiency: float | None
    wave_speed: float | None
    mean_bar_ns: float | None
    wave_oiz: OiZWindow
    quality_limited: bool
    constituent_change: bool
    minimum_close: float | None = None
    maximum_close: float | None = None


@dataclass
class _Forming:
    """Mutable state of the wave currently forming."""

    number: int
    start_index: int
    reference_index: int
    reference_close: float
    end_index: int
    direction: int = 0
    rho: float | None = None
    atr_at_start: float | None = None
    extreme: float | None = None
    end_close: float | None = None
    quality_limited: bool = False
    constituent_change: bool = False
    direction_available_at: datetime | None = None


class WaveState:
    """Incremental causal wave segmentation (spec 8.1) with 8.2 fields.

    Feed the BarFeatures produced by one IndicatorState.advance() call per
    valid closed bar, in strict stream order. The state maintains the
    current forming wave plus the ordered list of completed waves; every
    record is deterministic (I-07: monotonic wave ids, no randomness).
    """

    def __init__(self, params: WyckoffV4Params) -> None:
        self._params = params
        self._features: list[BarFeatures] = []
        self._completed: list[WaveRecord] = []
        self._current: _Forming | None = None
        self._next_number = 1

    # --- public surface --------------------------------------------------

    @property
    def completed_waves(self) -> tuple[WaveRecord, ...]:
        """Completed waves in confirmation order (forming wave excluded)."""
        return tuple(self._completed)

    @property
    def current_wave(self) -> WaveRecord | None:
        """Snapshot of the forming wave, or None before the first valid bar."""
        if self._current is None:
            return None
        return self._build_record(
            self._current,
            status=WaveStatus.FORMING,
            end_index=self._current.end_index,
        )

    @property
    def waves(self) -> tuple[WaveRecord, ...]:
        """All waves: completed waves followed by the forming wave."""
        out = list(self._completed)
        current = self.current_wave
        if current is not None:
            out.append(current)
        return tuple(out)

    def feed(self, features: BarFeatures) -> None:
        """Advance the segmentation by one bar (stream order enforced)."""
        idx = len(self._features)
        if features.index != idx:
            raise ValueError(
                "waves: BarFeatures must be fed in strict stream order "
                f"(expected index {idx}, got {features.index})"
            )
        self._features.append(features)

        if not features.valid:
            # data_gap: inside the wave's span, no close / volume / reversal.
            if self._current is not None:
                self._current.end_index = idx
                self._current.quality_limited = True
            return

        close = features.bar.close

        if self._current is None:
            # Anchor: the first valid close starts the initializing wave.
            self._current = _Forming(
                number=self._next_number,
                start_index=idx + 1,
                reference_index=idx,
                reference_close=close,
                end_index=idx,
            )
            self._next_number += 1
            self._try_freeze_rho(self._current, features)
            return

        wave = self._current
        self._try_freeze_rho(wave, features)

        if wave.direction == 0:
            determined = wave.rho is not None and abs(close - wave.reference_close) >= wave.rho
            if not determined:
                self._append_bar(wave, features)
                return
            wave.direction = 1 if close > wave.reference_close else -1
            wave.direction_available_at = max(features.bar.bar_end, features.bar.received_at or features.bar.bar_end)
            closes = [
                g.bar.close
                for g in self._features[wave.start_index : idx + 1]
                if g.valid
            ]
            wave.extreme = max(closes) if wave.direction == 1 else min(closes)

        # Directed wave: reversal check happens BEFORE attributing the bar,
        # so the reversal bar's volume can only land in the new wave.
        if wave.direction == 1:
            candidate = max(wave.extreme if wave.extreme is not None else close, close)
            reversed_ = wave.rho is not None and Decimal(str(close)) <= Decimal(str(candidate)) - Decimal(str(wave.rho))
        else:
            candidate = min(wave.extreme if wave.extreme is not None else close, close)
            reversed_ = wave.rho is not None and Decimal(str(close)) >= Decimal(str(candidate)) + Decimal(str(wave.rho))
        if reversed_:
            self._reverse(wave, features, idx, close)
            return
        wave.extreme = candidate
        self._append_bar(wave, features)

    # --- internals --------------------------------------------------------

    def _try_freeze_rho(self, wave: _Forming, features: BarFeatures) -> None:
        """Freeze ATR_(t-1) at creation; initialization waits for a positive ATR."""
        if wave.rho is not None:
            return
        source = features.atr_baseline
        atr = None
        if source.is_available:
            atr = source.value
        elif features.atr_baseline.is_available:
            atr = features.atr_baseline.value
        if atr is not None and atr > 0:
            wave.atr_at_start = atr
            wave.rho = self._params.wave_reversal_atr * atr

    def _append_bar(self, wave: _Forming, features: BarFeatures) -> None:
        wave.end_index = features.index
        wave.end_close = features.bar.close
        if CONSTITUENT_CHANGE_FLAG in features.bar.quality_flags:
            wave.constituent_change = True

    def _reverse(
        self, wave: _Forming, features: BarFeatures, idx: int, close: float
    ) -> None:
        """Complete the old wave at t-1 and open the new wave at t (8.1)."""
        self._completed.append(
            self._build_record(
                wave,
                status=WaveStatus.COMPLETED,
                end_index=idx - 1,
                confirmed_index=idx,
                confirmed_at=features.bar.bar_end,
            )
        )
        # New wave reference close: C_(t-1); if that bar is a data_gap bar,
        # the last valid close before the reversal bar (SPEC DECISION).
        reference_close = None
        for j in range(idx - 1, -1, -1):
            if self._features[j].valid:
                reference_close = self._features[j].bar.close
                break
        if reference_close is None:  # unreachable: reversal needs closes
            reference_close = wave.reference_close
        new = _Forming(
            number=self._next_number,
            start_index=idx,
            reference_index=idx - 1,
            reference_close=reference_close,
            end_index=idx,
            direction=-wave.direction,
            extreme=close,
            end_close=close,
            direction_available_at=max(features.bar.bar_end, features.bar.received_at or features.bar.bar_end),
        )
        if CONSTITUENT_CHANGE_FLAG in features.bar.quality_flags:
            new.constituent_change = True
        self._next_number += 1
        self._current = new
        self._try_freeze_rho(new, features)

    def _build_record(
        self,
        wave: _Forming,
        *,
        status: WaveStatus,
        end_index: int,
        confirmed_index: int | None = None,
        confirmed_at: datetime | None = None,
    ) -> WaveRecord:
        feats = self._features
        a = wave.start_index
        e = end_index
        bars = feats[a : e + 1] if e >= a else []
        valid_bars = [g for g in bars if g.valid]
        valid_closes = [g.bar.close for g in valid_bars]
        bar_count = e - a + 1
        direction = wave.direction

        raw_volume_sum = float(sum(g.bar.volume for g in valid_bars))
        baselines = [g.bv_baseline.value for g in bars]
        if bars and all(b is not None for b in baselines):
            baseline_volume_sum = float(sum(baselines))
        else:
            baseline_volume_sum = None

        extreme = None
        if direction != 0 and valid_closes:
            extreme = max(valid_closes) if direction == 1 else min(valid_closes)
        end_close = valid_closes[-1] if valid_closes else None

        peak = signed = giveback = path = efficiency = speed = None
        a0 = wave.atr_at_start
        if direction != 0 and a0 is not None and a0 > 0 and extreme is not None:
            ref = wave.reference_close
            peak = direction * (extreme - ref) / a0
            if end_close is not None:
                signed = direction * (end_close - ref) / a0
                giveback = peak - signed
            if len(valid_bars) == len(bars) and bars:
                total = 0.0
                prev = ref
                for g in bars:
                    total += abs(g.bar.close - prev)
                    prev = g.bar.close
                path = total / a0
                if path > 0 and signed is not None:
                    efficiency = signed / path
            if bar_count > 0:
                speed = peak / bar_count

        ns_values = [g.ns.value for g in bars]
        if bars and all(v is not None for v in ns_values):
            mean_bar_ns = statistics.fmean(ns_values)
        else:
            mean_bar_ns = None

        return WaveRecord(
            wave_id=f"w-{wave.number:04d}",
            direction=direction,
            status=status,
            start_index=a,
            end_index=e,
            reference_index=wave.reference_index,
            confirmed_at_index=confirmed_index,
            start_time=feats[min(a, e)].bar.bar_start,
            end_time=feats[e].bar.bar_start,
            confirmed_at=confirmed_at,
            available_at=max(max(g.bar.bar_end, g.bar.received_at or g.bar.bar_end)
                             for g in feats[:confirmed_index + 1])
            if status is WaveStatus.COMPLETED
            else wave.direction_available_at,
            start_reference_close=wave.reference_close,
            extreme_close=extreme,
            end_close=end_close,
            reversal_threshold=wave.rho,
            atr_at_start=a0,
            bar_count=bar_count,
            raw_volume_sum=raw_volume_sum,
            baseline_volume_sum=baseline_volume_sum,
            wave_activity_rate=self._effort_feature(bars),
            wave_cum_effort=self._cum_effort_feature(bars),
            peak_excursion=peak,
            signed_end_displacement=signed,
            giveback=giveback,
            path_distance=path,
            directional_efficiency=efficiency,
            wave_speed=speed,
            mean_bar_ns=mean_bar_ns,
            wave_oiz=self._wave_oiz(a, e),
            quality_limited=wave.quality_limited,
            constituent_change=wave.constituent_change,
            minimum_close=min(valid_closes) if valid_closes else None,
            maximum_close=max(valid_closes) if valid_closes else None,
        )

    def _effort_feature(self, bars: Sequence[BarFeatures]) -> NumFeature:
        """Effort(a..e) = sum(V) / sum(Bv) (spec 4.5) over the wave bars."""
        if not bars:
            return NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID)
        if any(not g.valid for g in bars):
            return NumFeature.not_evaluable(NotEvaluableReason.DATA_GAP)
        volumes = [g.bar.volume for g in bars]
        baselines = []
        for g in bars:
            if not g.bv_baseline.is_available:
                return NumFeature.not_evaluable(
                    g.bv_baseline.reason or NotEvaluableReason.BV_UNAVAILABLE
                )
            baselines.append(g.bv_baseline.value)
        return effort_ratio(volumes, baselines)

    def _cum_effort_feature(self, bars: Sequence[BarFeatures]) -> NumFeature:
        """CumEffort(a..e) = sum(RVOL) (spec 4.5) over the wave bars."""
        if not bars:
            return NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID)
        if any(not g.valid for g in bars):
            return NumFeature.not_evaluable(NotEvaluableReason.DATA_GAP)
        return cumulative_effort([g.rvol for g in bars])

    def _wave_oiz(self, a: int, e: int) -> OiZWindow:
        """OIz(a..e) of spec 4.6 replicated from per-bar OiFeatures."""
        n = self._params.oi_window
        if e < a:
            return OiZWindow(
                NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID),
                None,
            )
        if a - n < 0:
            return OiZWindow(
                NumFeature.not_evaluable(NotEvaluableReason.OI_HISTORY_INSUFFICIENT),
                None,
            )
        records = self._features[a - n : e + 1]
        d_features = [g.oi.d_oi for g in records]
        for feat in d_features:
            if not feat.is_available:
                return OiZWindow(
                    NumFeature.not_evaluable(
                        feat.reason or NotEvaluableReason.OI_MISSING
                    ),
                    None,
                )
        denominator = [f.value for f in d_features[:n]]
        numerator = [f.value for f in d_features[n:]]
        scale = statistics.median([abs(v) for v in denominator])
        if scale <= 0:
            return OiZWindow(
                NumFeature.not_evaluable(NotEvaluableReason.OI_ZERO_SCALE),
                None,
            )
        available_ats = [
            g.oi.d_oi_available_at for g in records if g.oi.d_oi_available_at is not None
        ]
        return OiZWindow(
            NumFeature.available(sum(numerator) / scale),
            max(available_ats) if available_ats else None,
        )


def run_segmentation(
    bars: Sequence[BarV4],
    wave_reversal_atr: Sequence[float] = _SENSITIVITY_MULTIPLIERS,
    *,
    params: WyckoffV4Params | None = None,
) -> dict[float, int]:
    """Spec 8.1 sensitivity helper: completed-wave counts per multiplier.

    Runs the full causal pipeline (IndicatorState + WaveState) once per
    ``wave_reversal_atr`` multiplier (defaults 1.0 / 1.5 / 2.0, the
    fragmentation-risk sensitivity analysis of 8.1) over the same bars and
    returns ``{multiplier: number_of_completed_waves}``. Deterministic.
    """
    counts: dict[float, int] = {}
    for multiplier in wave_reversal_atr:
        base = params if params is not None else WyckoffV4Params()
        run_params = dataclasses.replace(base, wave_reversal_atr=multiplier)
        indicators = IndicatorState(run_params)
        waves = WaveState(run_params)
        for bar in bars:
            waves.feed(indicators.advance(bar))
        counts[multiplier] = len(waves.completed_waves)
    return counts


__all__ = [
    "CONSTITUENT_CHANGE_FLAG",
    "WaveStatus",
    "WaveRecord",
    "WaveState",
    "run_segmentation",
]

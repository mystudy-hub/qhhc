"""Wyckoff V4 incremental indicators: baselines, bar features, effort,
open-interest features and causal swing points.

Spec references:
- Section 4.4 (amplitude and volume baselines): true range, Wilder ATR with
  robust/standard truncation, the Bv volume baseline, Spread/NS/CP/LW/UW/
  NetMove/Gap/RVOL, and F-16 (current-bar features read only prior-state
  baselines: A_t = ATR_(t-1) and Bv_t excludes V_t; the ATR_t / next-bar Bv
  commit happens after the current bar's features are final).
- Section 4.5 (activity rate, cumulative effort, volume caliber): Effort,
  CumEffort, the daily and session-matched Bv sampling sets with their own
  minimum-sample gates, and the requirement that every Bv in an Effort
  window be available.
- Section 4.6 (open-interest features, descriptive): dOI, OIz, OIz(a..b),
  the oi_quadrant table, the exact not_evaluable reasons, and per-feature
  feature_available_at.
- Section 4.7 (zero volume, gaps, constituent changes): true zero-volume
  bars are retained, missing bars pause affected windows (data_gap).
- Section 4.8 (time conventions): inclusive a..b windows, warmup counting.
- Section 5.6 (causal swing points): confirmation at the j+k close, first
  usable at j+k+1, immutable afterwards.
- Section 11.2 group A: the acceptance cases exercised by
  tests/test_group_a.py.

SPEC DECISIONS (literal-reading choices):
- Timeframe: IndicatorState takes an explicit `intraday` flag (a series-level
  property, not a rule parameter). Daily => Bv samples the previous
  `volume_period` bars (qualified ones inside that positional window) with
  the `volume_min_samples` gate. Intraday => session-matched sampling: same
  session_id and same bar slot over the last `session_baseline_days`
  observed trading days, with the `session_min_samples` gate. A bar slot is
  identified by the bar_start time-of-day (stable and causal even when a
  session contains gaps); a trading day key is bar.trading_day when present,
  else the bar_start date.
- data_gap bars: the caller advances a placeholder BarV4 carrying the
  quality flag 'data_gap' (DATA_GAP_FLAG, the spec 4.7 marker). Such a bar
  is recorded as invalid: every feature is NOT_EVALUABLE with reason
  data_gap, no baseline (ATR / Bv / OI) is updated, and any window that
  contains it is not evaluable (the 4.7 pause; the immutable streaming
  timeline is never "repaired"). A volume that is not a finite real value
  (NaN/inf) likewise makes the bar invalid with reason volume_not_real.
- Previous close: C_(t-1) is the close of the previous *valid* bar (gap
  bars carry no data). The first valid bar has no prior close, so NetMove,
  Gap and the oi_quadrant price direction are not evaluable
  (no_prior_close).
- Not-evaluable reasons for price/volume features are engineering names
  (spread_zero, atr_unavailable, atr_zero, bv_unavailable, bv_zero, ...);
  the OI reasons are the exact spec names of 4.6. ATR warmup
  (atr_unavailable) and ATR == 0 (atr_zero) are distinct reasons, as are
  Bv warmup (bv_unavailable) and Bv == 0 (bv_zero).
- oi_method declaration: an empty oi_method means undeclared => every OI
  feature is not_evaluable with oi_method_unknown. A declared non-empty
  oi_method is treated as a registered identifier (section 4.1) and is not
  re-validated here.
- dOI history: the OIz denominator uses the preceding n_oi consecutive
  computable changes. Missing OI clears the contiguous history instead of
  skipping a bar to borrow an older sample; insufficient history is explicit.
- Late OI (4.6): the OiFeatures embedded in BarFeatures is the descriptive
  (ex-post) record; it carries each feature's available_at (the maximum
  contributing oi_available_at, never earlier). realtime_view() returns the
  realtime snapshot in which any feature whose available_at is later than
  the bar's computation time (received_at if present else bar_end) is
  NOT_EVALUABLE with oi_not_yet_available. Late OI appends a descriptive
  record and never rewrites the earlier snapshot.
- Capping ratios: per committed bar, tr_capping_ratio = tr_in_robust /
  true_range (input truncation), atr_capping_ratio = ATR_robust_t /
  ATR_standard_t (committed effect, 1.0 at the untruncated initial value),
  and bv_capping_ratio = Bv_robust / Bv_standard of the current bar. Each
  is None when not computable; nothing is inferred from them.
- Swing points: candidate j is evaluated exactly once, at t = j+k. All bars
  j-k..j+k must be valid; a window containing an invalid bar discards the
  candidate with reason data_gap (recorded in swing_discards). If the
  extremes pass but A_j (the selected-mode ATR baseline at bar j) is
  unavailable, the reaction condition is not evaluable and the candidate is
  discarded with reason reaction_not_evaluable - never silently confirmed
  or denied (F-19).
- Effort / CumEffort / OIz windows use inclusive 0-based stream bar indices;
  a window over an invalid bar, with an unavailable Bv member (4.5), or
  with an unavailable RVOL member is NOT_EVALUABLE (the leftmost member's
  reason is propagated).

Determinism (I-07): all iteration is over insertion-ordered lists/deques;
medians and means use the statistics module; no set or dict iteration
order enters any output.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from cta_lab.wyckoff_v4.contract import BarV4, ContractViolation, check_bar
from cta_lab.wyckoff_v4.params import BaselineMode, WyckoffV4Params

DATA_GAP_FLAG = "data_gap"
"""Quality flag marking an expected-but-missing bar (spec 4.7)."""


class NotEvaluableReason(Enum):
    """Reason codes for NOT_EVALUABLE indicator features.

    The oi_* members are the exact reason names of spec 4.6; the rest are
    engineering names for the 4.4/4.5/4.7 not-available rows.
    """

    DATA_GAP = "data_gap"
    VOLUME_NOT_REAL = "volume_not_real"
    SPREAD_ZERO = "spread_zero"
    ATR_UNAVAILABLE = "atr_unavailable"
    ATR_ZERO = "atr_zero"
    NO_PRIOR_CLOSE = "no_prior_close"
    BV_UNAVAILABLE = "bv_unavailable"
    BV_ZERO = "bv_zero"
    BV_MISSING_IN_WINDOW = "bv_missing_in_window"
    WINDOW_INVALID = "window_invalid"
    OI_METHOD_UNKNOWN = "oi_method_unknown"
    OI_MISSING = "oi_missing"
    OI_HISTORY_INSUFFICIENT = "oi_history_insufficient"
    OI_ZERO_SCALE = "oi_zero_scale"
    OI_NOT_YET_AVAILABLE = "oi_not_yet_available"
    REACTION_NOT_EVALUABLE = "reaction_not_evaluable"


@dataclass(frozen=True)
class NumFeature:
    """A numeric feature: a value, or a not_evaluable reason (never both).

    F-19: a NOT_EVALUABLE feature carries its reason and is never coerced
    to 0, 0.5, or any neutral value.
    """

    value: float | None = None
    reason: NotEvaluableReason | None = None

    @classmethod
    def available(cls, value: float) -> "NumFeature":
        return cls(value=value, reason=None)

    @classmethod
    def not_evaluable(cls, reason: NotEvaluableReason) -> "NumFeature":
        return cls(value=None, reason=reason)

    @property
    def is_available(self) -> bool:
        return self.value is not None


class OiQuadrant(Enum):
    """The oi_quadrant values of the spec 4.6 table."""

    UP_OI_UP = "up_oi_up"
    UP_OI_DOWN = "up_oi_down"
    UP_OI_FLAT = "up_oi_flat"
    DOWN_OI_UP = "down_oi_up"
    DOWN_OI_DOWN = "down_oi_down"
    DOWN_OI_FLAT = "down_oi_flat"
    FLAT_OI_UP = "flat_oi_up"
    FLAT_OI_DOWN = "flat_oi_down"
    FLAT_OI_FLAT = "flat_oi_flat"


@dataclass(frozen=True)
class OiFeatures:
    """Open-interest features of one bar (spec 4.6, descriptive).

    The values are the ex-post descriptive records; available_at is the
    earliest time all contributing OI values were available (the maximum
    contributing oi_available_at). realtime_view() yields the realtime
    snapshot: any feature that was not yet available at the bar's
    computation time becomes NOT_EVALUABLE with oi_not_yet_available.
    """

    d_oi: NumFeature
    d_oi_available_at: datetime | None
    oi_z: NumFeature
    oi_z_available_at: datetime | None
    oi_quadrant: OiQuadrant | None
    oi_quadrant_reason: NotEvaluableReason | None
    oi_quadrant_available_at: datetime | None
    computation_time: datetime

    def realtime_view(self, decision_time: datetime | None = None) -> "OiFeatures":
        """View at a decision time; the default is this bar's computation time."""
        asof = self.computation_time if decision_time is None else decision_time
        ne = NumFeature.not_evaluable(NotEvaluableReason.OI_NOT_YET_AVAILABLE)
        d = self.d_oi
        if d.is_available and self.d_oi_available_at is not None:
            if self.d_oi_available_at > asof:
                d = ne
        z = self.oi_z
        z_late = (
            self.oi_z_available_at is not None
            and self.oi_z_available_at > asof
        )
        if z_late:
            z = ne
        quadrant = self.oi_quadrant
        q_reason = self.oi_quadrant_reason
        if z_late:
            quadrant = None
            q_reason = NotEvaluableReason.OI_NOT_YET_AVAILABLE
        return OiFeatures(
            d_oi=d,
            d_oi_available_at=self.d_oi_available_at,
            oi_z=z,
            oi_z_available_at=self.oi_z_available_at,
            oi_quadrant=quadrant,
            oi_quadrant_reason=q_reason,
            oi_quadrant_available_at=self.oi_quadrant_available_at,
            computation_time=self.computation_time,
        )


@dataclass(frozen=True)
class BarFeatures:
    """Per-bar features of spec 4.4 (F-16: baselines are prior-state values).

    atr_baseline_* / bv_baseline_* are the baselines used by this bar's
    features (A_t = ATR_(t-1), Bv_t excluding V_t). tr_capping_ratio /
    atr_capping_ratio describe the ATR_t commit performed *after* the
    features were finalized; bv_capping_ratio describes this bar's Bv.
    """

    index: int
    bar: BarV4
    valid: bool
    zero_volume: bool
    spread: float
    ns: NumFeature
    cp: NumFeature
    lw: NumFeature
    uw: NumFeature
    net_move: NumFeature
    gap: NumFeature
    rvol: NumFeature
    atr_baseline: NumFeature
    atr_baseline_standard: float | None
    atr_baseline_robust: float | None
    bv_baseline: NumFeature
    bv_baseline_standard: NumFeature
    bv_baseline_robust: NumFeature
    tr_capping_ratio: float | None
    atr_capping_ratio: float | None
    bv_capping_ratio: float | None
    oi: OiFeatures


class SwingKind(Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class SwingPoint:
    """A confirmed causal swing point (spec 5.6).

    Confirmed at the j+k close (confirmed_at_index = j+k); first usable for
    detection at j+k+1 (usable_from_index). Immutable afterwards.
    """

    kind: SwingKind
    index: int
    price: float
    bar_start: datetime
    confirmed_at_index: int
    usable_from_index: int


@dataclass(frozen=True)
class SwingDiscard:
    """A swing candidate that could not be evaluated (transparency, I-06)."""

    kind: SwingKind
    index: int
    reason: NotEvaluableReason


@dataclass(frozen=True)
class OiZWindow:
    """Windowed OIz(a..b) of spec 4.6 with its feature_available_at."""

    num: NumFeature
    available_at: datetime | None

    def realtime_view(self, decision_time: datetime) -> "OiZWindow":
        """Mask a future OI result while preserving its descriptive record."""
        if self.available_at is not None and self.available_at > decision_time:
            return OiZWindow(
                NumFeature.not_evaluable(NotEvaluableReason.OI_NOT_YET_AVAILABLE),
                self.available_at,
            )
        return self


def oi_z_from_features(features: Sequence[BarFeatures], start: int, end: int,
                       window: int, *, decision_time: datetime | None = None,
                       oi_method_known: bool | None = None) -> OiZWindow:
    """Shared OIz calculation from complete descriptive per-bar records.

    Method declaration precedes window/history checks. Missing or invalid
    inputs precede late availability; future zero-scale inputs reveal only
    oi_not_yet_available until their availability time.
    """
    if oi_method_known is False or (oi_method_known is None and any(
            f.oi.d_oi.reason is NotEvaluableReason.OI_METHOD_UNKNOWN for f in features)):
        return OiZWindow(NumFeature.not_evaluable(NotEvaluableReason.OI_METHOD_UNKNOWN), None)
    if not 0 <= start <= end < len(features):
        return OiZWindow(NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID), None)
    if start < window:
        return OiZWindow(NumFeature.not_evaluable(NotEvaluableReason.OI_HISTORY_INSUFFICIENT), None)
    records = features[start - window:end + 1]
    unavailable = [f.oi.d_oi for f in records if not f.oi.d_oi.is_available]
    if unavailable:
        first = next((num for num in unavailable
                      if num.reason is not NotEvaluableReason.OI_NOT_YET_AVAILABLE), unavailable[0])
        return OiZWindow(NumFeature.not_evaluable(first.reason or NotEvaluableReason.OI_MISSING), None)
    available_ats = [f.oi.d_oi_available_at for f in records if f.oi.d_oi_available_at is not None]
    available_at = max(available_ats) if available_ats else None
    if decision_time is not None and available_at is not None and available_at > decision_time:
        return OiZWindow(NumFeature.not_evaluable(NotEvaluableReason.OI_NOT_YET_AVAILABLE), available_at)
    scale = statistics.median(abs(f.oi.d_oi.value) for f in records[:window])
    if scale <= 0:
        return OiZWindow(NumFeature.not_evaluable(NotEvaluableReason.OI_ZERO_SCALE), available_at)
    return OiZWindow(NumFeature.available(sum(f.oi.d_oi.value for f in records[window:]) / scale),
                     available_at)


def true_range(
    high: float, low: float, close: float, prev_close: float
) -> float:
    """true_range_t of spec 4.4."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def effort_ratio(
    volumes: Sequence[float], baselines: Sequence[float]
) -> NumFeature:
    """Effort(a..b) = sum(V) / sum(Bv) (spec 4.5).

    Pure helper; every baseline must be a positive available value,
    otherwise the ratio is not evaluable (never zero-filled).
    """
    if len(volumes) != len(baselines):
        raise ValueError(
            "effort_ratio: volumes and baselines must have equal length"
        )
    if not volumes:
        return NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID)
    if any(b <= 0 for b in baselines):
        return NumFeature.not_evaluable(NotEvaluableReason.BV_ZERO)
    return NumFeature.available(sum(volumes) / sum(baselines))


def cumulative_effort(rvols: Sequence[NumFeature]) -> NumFeature:
    """CumEffort(a..b) = sum(RVOL) (spec 4.5).

    Any unavailable RVOL member makes the sum not evaluable; the leftmost
    member's reason is propagated.
    """
    if not rvols:
        return NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID)
    total = 0.0
    for rvol in rvols:
        if not rvol.is_available:
            return NumFeature.not_evaluable(rvol.reason)
        total += rvol.value
    return NumFeature.available(total)


@dataclass(frozen=True)
class _HistoryRecord:
    """Internal per-bar record kept for windowed features."""

    index: int
    bar: BarV4
    valid: bool
    volume: float | None
    bv_standard: float | None
    bv_robust: float | None
    bv_selected: float | None
    rvol: NumFeature
    atr_baseline_selected: float | None
    d_oi: float | None
    d_oi_available_at: datetime | None


class IndicatorState:
    """Incremental indicator state (spec 4.4-4.6, 5.6).

    advance(bar) enforces F-16: the returned BarFeatures are computed from
    prior-state baselines only, and the ATR_t / next-bar Bv / OI history
    commits happen afterwards, so they can never retro-affect the current
    bar's features.
    """

    def __init__(
        self,
        params: WyckoffV4Params,
        *,
        intraday: bool = False,
        oi_method: str = "",
    ):
        params.validate()
        self._params = params
        self._intraday = intraday
        self._oi_method = oi_method or ""
        # Prior-state baselines (None until computed).
        self._atr_standard: float | None = None
        self._atr_robust: float | None = None
        self._tr_committed = 0
        self._tr_deque: deque[float] = deque(maxlen=params.atr_period)
        self._prev_close: float | None = None
        # Volume sampling history.
        self._volume_window: deque[tuple[bool, float | None]] = deque(
            maxlen=params.volume_period
        )
        self._slots: dict[
            tuple[str | None, datetime.time], list[tuple[str, datetime, float]]
        ] = {}
        self._trading_days: list[str] = []
        self._day_seen: set[str] = set()
        # Open-interest history.
        self._prev_bar_oi: tuple[float, datetime] | None = None
        self._doi_deque: deque[tuple[float, datetime]] = deque(
            maxlen=params.oi_window
        )
        # Stream history and swing points.
        self._history: list[_HistoryRecord] = []
        self._features: list[BarFeatures] = []
        self._swing_points: list[SwingPoint] = []
        self._swing_discards: list[SwingDiscard] = []

    # --- public read-only views ------------------------------------------

    @property
    def params(self) -> WyckoffV4Params:
        return self._params

    @property
    def features(self) -> tuple[BarFeatures, ...]:
        """All advanced bars' features, in stream order."""
        return tuple(self._features)

    @property
    def swing_points(self) -> tuple[SwingPoint, ...]:
        """Confirmed swing points, in confirmation order (immutable)."""
        return tuple(self._swing_points)

    @property
    def swing_discards(self) -> tuple[SwingDiscard, ...]:
        """Swing candidates that could not be evaluated, with reasons."""
        return tuple(self._swing_discards)

    @property
    def committed_atr(self) -> float | None:
        return self._select(self._atr_standard, self._atr_robust)

    # --- main advance -----------------------------------------------------

    def advance(self, bar: BarV4, *, defer_commit: bool = False) -> BarFeatures:
        """Advance the state by one bar and return its features (F-16)."""
        if getattr(self, "_pending_commit", None) is not None:
            raise RuntimeError("commit the previously prepared bar before advancing")
        if not bar.is_closed:
            raise ContractViolation("unclosed_bar")
        if self._history and bar.bar_start <= self._history[-1].bar.bar_start:
            raise ContractViolation("bar_start_out_of_order")
        index = len(self._history)
        is_gap = DATA_GAP_FLAG in bar.quality_flags
        if is_gap:
            valid = False
            bar_reason = NotEvaluableReason.DATA_GAP
        else:
            problems = check_bar(bar)
            if problems:
                raise ContractViolation(
                    f"bar {bar.key!r} failed basic legality checks: {problems}"
                )
            valid = math.isfinite(bar.volume)
            bar_reason = None if valid else NotEvaluableReason.VOLUME_NOT_REAL

        if not valid:
            features = self._invalid_bar_features(index, bar, bar_reason)
            self._prev_close = None
            self._prev_bar_oi = None
            self._doi_deque.clear()
            self._volume_window.append((False, None))
            self._history.append(
                _HistoryRecord(
                    index=index,
                    bar=bar,
                    valid=False,
                    volume=None,
                    bv_standard=None,
                    bv_robust=None,
                    bv_selected=None,
                    rvol=features.rvol,
                    atr_baseline_selected=None,
                    d_oi=None,
                    d_oi_available_at=None,
                )
            )
            self._features.append(features)
            self._detect_swing(index)
            return features

        computation_time = max(bar.bar_end, bar.received_at or bar.bar_end)
        prev_close = self._prev_close

        # --- current-bar features from prior-state baselines (F-16) ------
        a_std = self._atr_standard
        a_rob = self._atr_robust
        a_sel = self._select(a_std, a_rob)
        spread = bar.high - bar.low

        if a_sel is None:
            ns = NumFeature.not_evaluable(NotEvaluableReason.ATR_UNAVAILABLE)
        elif a_sel == 0:
            ns = NumFeature.not_evaluable(NotEvaluableReason.ATR_ZERO)
        else:
            ns = NumFeature.available(spread / a_sel)

        if spread > 0:
            cp = NumFeature.available((bar.close - bar.low) / spread)
            lw = NumFeature.available(
                (min(bar.open, bar.close) - bar.low) / spread
            )
            uw = NumFeature.available(
                (bar.high - max(bar.open, bar.close)) / spread
            )
        else:
            cp = NumFeature.not_evaluable(NotEvaluableReason.SPREAD_ZERO)
            lw = NumFeature.not_evaluable(NotEvaluableReason.SPREAD_ZERO)
            uw = NumFeature.not_evaluable(NotEvaluableReason.SPREAD_ZERO)

        if prev_close is None:
            net_move = NumFeature.not_evaluable(NotEvaluableReason.NO_PRIOR_CLOSE)
            gap = NumFeature.not_evaluable(NotEvaluableReason.NO_PRIOR_CLOSE)
        elif a_sel is None:
            net_move = NumFeature.not_evaluable(NotEvaluableReason.ATR_UNAVAILABLE)
            gap = NumFeature.not_evaluable(NotEvaluableReason.ATR_UNAVAILABLE)
        elif a_sel == 0:
            net_move = NumFeature.not_evaluable(NotEvaluableReason.ATR_ZERO)
            gap = NumFeature.not_evaluable(NotEvaluableReason.ATR_ZERO)
        else:
            net_move = NumFeature.available((bar.close - prev_close) / a_sel)
            gap = NumFeature.available(abs(bar.open - prev_close) / a_sel)

        bv_std, bv_rob = self._compute_bv(bar)
        bv_sel = self._select(
            bv_std.value, bv_rob.value
        )
        if bv_sel is None:
            rvol = NumFeature.not_evaluable(NotEvaluableReason.BV_UNAVAILABLE)
        elif bv_sel == 0:
            rvol = NumFeature.not_evaluable(NotEvaluableReason.BV_ZERO)
        else:
            rvol = NumFeature.available(bar.volume / bv_sel)

        oi = self._oi_features(bar, prev_close, computation_time)

        # --- ATR_t commit values (computed from prior state; applied below,
        # so they can never retro-affect this bar's features, F-16) --------
        tr = (
            true_range(bar.high, bar.low, bar.close, prev_close)
            if prev_close is not None
            else None
        )
        tr_capping_ratio: float | None = None
        atr_capping_ratio: float | None = None
        new_atr_std: float | None = None
        new_atr_rob: float | None = None
        tr_append = tr is not None
        if tr is not None:
            n = self._params.atr_period
            if self._tr_committed < n:
                if self._tr_committed + 1 == n:
                    initial = statistics.mean(list(self._tr_deque) + [tr])
                    new_atr_std = initial
                    new_atr_rob = initial
                    atr_capping_ratio = 1.0
            else:
                median_prev = statistics.median(self._tr_deque)
                cap = self._params.tr_cap_mult * median_prev
                tr_in_robust = min(tr, cap)
                new_atr_std = ((n - 1) * self._atr_standard + tr) / n
                new_atr_rob = ((n - 1) * self._atr_robust + tr_in_robust) / n
                if tr > 0:
                    tr_capping_ratio = tr_in_robust / tr
                if new_atr_std != 0:
                    atr_capping_ratio = new_atr_rob / new_atr_std

        bv_capping_ratio: float | None = None
        if (
            bv_std.is_available
            and bv_rob.is_available
            and bv_std.value is not None
            and bv_std.value != 0
        ):
            bv_capping_ratio = bv_rob.value / bv_std.value

        features = BarFeatures(
            index=index,
            bar=bar,
            valid=True,
            zero_volume=bar.volume == 0,
            spread=spread,
            ns=ns,
            cp=cp,
            lw=lw,
            uw=uw,
            net_move=net_move,
            gap=gap,
            rvol=rvol,
            atr_baseline=(
                NumFeature.available(a_sel) if a_sel is not None
                else NumFeature.not_evaluable(NotEvaluableReason.ATR_UNAVAILABLE)
            ),
            atr_baseline_standard=a_std,
            atr_baseline_robust=a_rob,
            bv_baseline=(
                NumFeature.available(bv_sel) if bv_sel is not None
                else NumFeature.not_evaluable(NotEvaluableReason.BV_UNAVAILABLE)
            ),
            bv_baseline_standard=bv_std,
            bv_baseline_robust=bv_rob,
            tr_capping_ratio=tr_capping_ratio,
            atr_capping_ratio=atr_capping_ratio,
            bv_capping_ratio=bv_capping_ratio,
            oi=oi,
        )

        # --- commits: these serve later bars only, never this one --------
        self._pending_commit = (bar, features, tr_append, tr, new_atr_std, new_atr_rob,
                                bv_std, bv_rob, bv_sel, oi)
        if defer_commit:
            return features
        self.commit_prepared()
        return features

    def commit_prepared(self) -> None:
        bar, features, tr_append, tr, new_atr_std, new_atr_rob, bv_std, bv_rob, bv_sel, oi = self._pending_commit
        index = features.index
        a_sel = features.atr_baseline.value
        rvol = features.rvol
        if tr_append:
            n = self._params.atr_period
            self._tr_deque.append(tr)
            self._tr_committed += 1
            if new_atr_std is not None:
                self._atr_standard = new_atr_std
            if new_atr_rob is not None:
                self._atr_robust = new_atr_rob

        if self._intraday:
            day = bar.trading_day or bar.bar_start.date().isoformat()
            if day not in self._day_seen:
                self._day_seen.add(day)
                self._trading_days.append(day)
            slot_key = (bar.session_id, bar.bar_start.time())
            self._slots.setdefault(slot_key, []).append(
                (day, bar.bar_start, bar.volume)
            )
        else:
            self._volume_window.append((True, bar.volume))

        if bar.open_interest is None:
            self._prev_bar_oi = None
            self._doi_deque.clear()
        else:
            if oi.d_oi.is_available and oi.d_oi_available_at is not None:
                self._doi_deque.append(
                    (oi.d_oi.value, oi.d_oi_available_at)
                )
            self._prev_bar_oi = (bar.open_interest, bar.oi_available_at)

        self._prev_close = bar.close

        self._history.append(
            _HistoryRecord(
                index=index,
                bar=bar,
                valid=True,
                volume=bar.volume,
                bv_standard=bv_std.value,
                bv_robust=bv_rob.value,
                bv_selected=bv_sel,
                rvol=rvol,
                atr_baseline_selected=a_sel,
                d_oi=oi.d_oi.value,
                d_oi_available_at=oi.d_oi_available_at,
            )
        )
        self._features.append(features)
        self._detect_swing(index)
        self._pending_commit = None

    # --- helpers ----------------------------------------------------------

    def _select(self, standard: float | None, robust: float | None) -> float | None:
        """Mode selection: only the current baseline_mode participates."""
        if self._params.baseline_mode is BaselineMode.STANDARD:
            return standard
        return robust

    def _invalid_bar_features(
        self, index: int, bar: BarV4, reason: NotEvaluableReason
    ) -> BarFeatures:
        ne = NumFeature.not_evaluable(reason)
        oi = OiFeatures(
            d_oi=ne,
            d_oi_available_at=None,
            oi_z=ne,
            oi_z_available_at=None,
            oi_quadrant=None,
            oi_quadrant_reason=reason,
            oi_quadrant_available_at=None,
            computation_time=(
                bar.received_at if bar.received_at is not None else bar.bar_end
            ),
        )
        return BarFeatures(
            index=index,
            bar=bar,
            valid=False,
            zero_volume=False,
            spread=bar.high - bar.low,
            ns=ne,
            cp=ne,
            lw=ne,
            uw=ne,
            net_move=ne,
            gap=ne,
            rvol=ne,
            atr_baseline=ne,
            atr_baseline_standard=None,
            atr_baseline_robust=None,
            bv_baseline=ne,
            bv_baseline_standard=ne,
            bv_baseline_robust=ne,
            tr_capping_ratio=None,
            atr_capping_ratio=None,
            bv_capping_ratio=None,
            oi=oi,
        )

    def _compute_bv(
        self, bar: BarV4
    ) -> tuple[NumFeature, NumFeature]:
        """Bv_t from the prior-state sampling set (excludes V_t, F-16)."""
        if self._intraday:
            samples = self._session_samples(bar)
            insufficient = len(samples) < self._params.session_min_samples
        else:
            samples = [
                v
                for valid, v in self._volume_window
                if valid and v is not None
            ]
            insufficient = len(samples) < self._params.volume_min_samples
        if insufficient or not samples:
            ne = NumFeature.not_evaluable(NotEvaluableReason.BV_UNAVAILABLE)
            return ne, ne
        standard = statistics.mean(samples)
        cap = self._params.volume_cap_mult * statistics.median(samples)
        robust = statistics.mean(min(v, cap) for v in samples)
        return (
            NumFeature.available(standard),
            NumFeature.available(robust),
        )

    def _session_samples(self, bar: BarV4) -> list[float]:
        """Same-session, same-slot volumes over the past baseline days."""
        current_day = bar.trading_day or bar.bar_start.date().isoformat()
        past_days = [d for d in self._trading_days if d != current_day]
        allowed = set(past_days[-self._params.session_baseline_days :])
        slot_key = (bar.session_id, bar.bar_start.time())
        return [
            v
            for d, _start, v in self._slots.get(slot_key, [])
            if d in allowed and d < current_day
        ]

    def _oi_features(
        self,
        bar: BarV4,
        prev_close: float | None,
        computation_time: datetime,
    ) -> OiFeatures:
        if not self._oi_method:
            ne = NumFeature.not_evaluable(NotEvaluableReason.OI_METHOD_UNKNOWN)
            return OiFeatures(
                d_oi=ne,
                d_oi_available_at=None,
                oi_z=ne,
                oi_z_available_at=None,
                oi_quadrant=None,
                oi_quadrant_reason=NotEvaluableReason.OI_METHOD_UNKNOWN,
                oi_quadrant_available_at=None,
                computation_time=computation_time,
            )
        d_oi = NumFeature.not_evaluable(NotEvaluableReason.OI_MISSING)
        d_at: datetime | None = None
        if bar.open_interest is not None and self._prev_bar_oi is not None:
            prev_value, prev_at = self._prev_bar_oi
            d_oi = NumFeature.available(bar.open_interest - prev_value)
            d_at = max(prev_at, bar.oi_available_at)

        if not d_oi.is_available:
            oi_z = NumFeature.not_evaluable(d_oi.reason)
            z_at = None
        elif len(self._doi_deque) < self._params.oi_window:
            oi_z = NumFeature.not_evaluable(
                NotEvaluableReason.OI_HISTORY_INSUFFICIENT
            )
            z_at = None
        else:
            scale = statistics.median(
                [abs(v) for v, _at in self._doi_deque]
            )
            if scale <= 0:
                oi_z = NumFeature.not_evaluable(
                    NotEvaluableReason.OI_ZERO_SCALE
                )
                z_at = max([at for _v, at in self._doi_deque] + [d_at])
            else:
                oi_z = NumFeature.available(d_oi.value / scale)
                z_at = max([at for _v, at in self._doi_deque] + [d_at])

        if not oi_z.is_available:
            quadrant = None
            q_reason = oi_z.reason
            q_at = None
        elif prev_close is None:
            quadrant = None
            q_reason = NotEvaluableReason.NO_PRIOR_CLOSE
            q_at = z_at
        else:
            if oi_z.value >= self._params.oi_flat_band:
                column = "oi_up"
            elif oi_z.value <= -self._params.oi_flat_band:
                column = "oi_down"
            else:
                column = "oi_flat"
            if bar.close > prev_close:
                row = "up"
            elif bar.close < prev_close:
                row = "down"
            else:
                row = "flat"
            quadrant = OiQuadrant(f"{row}_{column}")
            q_reason = None
            q_at = z_at
        return OiFeatures(
            d_oi=d_oi,
            d_oi_available_at=d_at,
            oi_z=oi_z,
            oi_z_available_at=z_at,
            oi_quadrant=quadrant,
            oi_quadrant_reason=q_reason,
            oi_quadrant_available_at=q_at,
            computation_time=computation_time,
        )

    # --- windowed features (spec 4.5, 4.6) --------------------------------

    def _window_records(
        self, start: int, end: int
    ) -> list[_HistoryRecord] | NumFeature:
        if not (0 <= start <= end < len(self._history)):
            return NumFeature.not_evaluable(NotEvaluableReason.WINDOW_INVALID)
        records = self._history[start : end + 1]
        if any(not r.valid for r in records):
            return NumFeature.not_evaluable(NotEvaluableReason.DATA_GAP)
        return records

    def effort(self, start: int, end: int) -> NumFeature:
        """Effort(a..b) = sum(V) / sum(Bv) over the inclusive window."""
        records = self._window_records(start, end)
        if isinstance(records, NumFeature):
            return records
        volumes = [r.volume for r in records]
        baselines = [r.bv_selected for r in records]
        if any(b is None for b in baselines):
            return NumFeature.not_evaluable(
                NotEvaluableReason.BV_MISSING_IN_WINDOW
            )
        return effort_ratio(volumes, baselines)

    def cum_effort(self, start: int, end: int) -> NumFeature:
        """CumEffort(a..b) = sum(RVOL) over the inclusive window."""
        records = self._window_records(start, end)
        if isinstance(records, NumFeature):
            return records
        return cumulative_effort([r.rvol for r in records])

    def oi_z_window(self, start: int, end: int, *, decision_time: datetime | None = None) -> OiZWindow:
        """OIz(a..b) of spec 4.6 with its feature_available_at."""
        return oi_z_from_features(self._features, start, end, self._params.oi_window,
                                  decision_time=decision_time, oi_method_known=bool(self._oi_method))

    # --- causal swing points (spec 5.6) -----------------------------------

    def _detect_swing(self, t: int) -> None:
        k = self._params.pivot_confirm_bars
        j = t - k
        if j - k < 0:
            return
        records = self._history[j - k : t + 1]
        if any(not r.valid for r in records):
            self._swing_discards.append(
                SwingDiscard(SwingKind.HIGH, j, NotEvaluableReason.DATA_GAP)
            )
            self._swing_discards.append(
                SwingDiscard(SwingKind.LOW, j, NotEvaluableReason.DATA_GAP)
            )
            return
        highs = [r.bar.high for r in records]
        lows = [r.bar.low for r in records]
        a_j = records[k].atr_baseline_selected

        # Swing high: strict on the left, non-strict on the right.
        if highs[k] > max(highs[:k]) and highs[k] >= max(highs[k + 1 :]):
            if a_j is None:
                self._swing_discards.append(
                    SwingDiscard(
                        SwingKind.HIGH,
                        j,
                        NotEvaluableReason.REACTION_NOT_EVALUABLE,
                    )
                )
            elif (
                highs[k] - min(lows[k + 1 :])
                >= self._params.pivot_min_reaction_atr * a_j
            ):
                self._swing_points.append(
                    SwingPoint(
                        kind=SwingKind.HIGH,
                        index=j,
                        price=highs[k],
                        bar_start=records[k].bar.bar_start,
                        confirmed_at_index=t,
                        usable_from_index=t + 1,
                    )
                )

        # Swing low: symmetric (strict left, non-strict right).
        if lows[k] < min(lows[:k]) and lows[k] <= min(lows[k + 1 :]):
            if a_j is None:
                self._swing_discards.append(
                    SwingDiscard(
                        SwingKind.LOW,
                        j,
                        NotEvaluableReason.REACTION_NOT_EVALUABLE,
                    )
                )
            elif (
                max(highs[k + 1 :]) - lows[k]
                >= self._params.pivot_min_reaction_atr * a_j
            ):
                self._swing_points.append(
                    SwingPoint(
                        kind=SwingKind.LOW,
                        index=j,
                        price=lows[k],
                        bar_start=records[k].bar.bar_start,
                        confirmed_at_index=t,
                        usable_from_index=t + 1,
                    )
                )


__all__ = [
    "DATA_GAP_FLAG",
    "BarFeatures",
    "IndicatorState",
    "NotEvaluableReason",
    "NumFeature",
    "OiFeatures",
    "OiQuadrant",
    "OiZWindow",
    "oi_z_from_features",
    "SwingDiscard",
    "SwingKind",
    "SwingPoint",
    "cumulative_effort",
    "effort_ratio",
    "true_range",
]

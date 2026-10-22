"""Trend context, trading range and internal reference levels (spec 5.1-5.6).

Implements:
- 5.1 ``trend_at`` classification and the trend snapshot fields.
- 5.2/5.5 the ``TradingRange`` frozen record: frozen S/R/epsilon per version,
  width validity, age, per-bar outside streaks and lifecycle predicates.
- 5.4 path-B generic range establishment (greedy touch selection).
- 5.6 internal Creek / Ice selection from confirmed causal swing points.

SPEC DECISIONS
- trend_at returns ``TrendState``; ``TrendState.UNKNOWN`` is the not-evaluable
  marker for trend evidence (types.py). ``trend_at_detailed`` additionally
  returns the reason ('history_insufficient', 'atr_unavailable', 'atr_zero').
- ER denominator 0 with a complete price series is defined as 0 (spec 5.1);
  this also applies to the path-B window ER.
- Path A width rejection keeps the 5.2 two-sided constraint:
  W < range_min_width_atr*A_ref => 'width_too_small' (engineering name,
  mirroring the spec's 'width_too_large' for the upper bound).
- establish_path_b checks, in order: atr reference available, data_gap,
  width, ER, interior fraction, touches. The data-gap check precedes the
  numeric checks because gap placeholder bars carry no valid OHLC.
- ``bars`` passed to establish_path_b are the raw recent bars (window + 1
  closes); the last ``generic_range_window`` entries form the range window
  and one extra prior close is used only for the window ER.
- Greedy touch selection walks candidates in time order and counts a
  candidate when it is >= touch_min_separation_bars positions after the last
  COUNTED touch and at least one bar strictly between them lies fully outside
  that side's area. The extreme-defining bar is an ordinary candidate, so it
  contributes at most one touch; it is not force-included.
- Lifecycle predicates (should_expire / should_supersede / should_retire) are
  pure helpers evaluating the 5.5 conditions; the 7.5 transition table (a
  later phase) is the single authority that applies them.
- 'most recent' Creek/Ice is ordered by confirmed_at_index (ties by index).
- ``creek_mode=range_only`` yields a ``NotImplementedMarker`` from the checked
  accessors so callers can emit not_implemented without exceptions.
- range_id is a deterministic content hash (sha256 of the frozen identity
  fields); stable across runs, no randomness, no time-of-day (I-07).
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, replace
from decimal import Decimal
from datetime import datetime
from typing import NamedTuple, Sequence

from .contract import boundary_epsilon
from .indicators import SwingKind, SwingPoint
from .params import CreekMode, WyckoffV4Params
from .types import RangeState, TrendState

__all__ = [
    "RangeBar",
    "RangeRejection",
    "WIDTH_TOO_LARGE",
    "WIDTH_TOO_SMALL",
    "ER_TOO_HIGH",
    "INTERIOR_FRACTION",
    "TOUCHES",
    "DATA_GAP",
    "ATR_REF_UNAVAILABLE",
    "TrendEvaluation",
    "TrendSnapshot",
    "make_trend_snapshot",
    "TradingRange",
    "NotImplementedMarker",
    "sma",
    "ols_slope",
    "trend_at",
    "trend_at_detailed",
    "establish_path_a",
    "establish_path_b",
    "greedy_touch_positions",
    "internal_creek",
    "internal_ice",
    "checked_internal_creek",
    "checked_internal_ice",
    "should_expire",
    "should_supersede",
    "should_retire",
]

# Rejection reason codes for range establishment.
WIDTH_TOO_LARGE = "width_too_large"
WIDTH_TOO_SMALL = "width_too_small"
ER_TOO_HIGH = "er_too_high"
INTERIOR_FRACTION = "interior_fraction"
TOUCHES = "touches"
DATA_GAP = "data_gap"
ATR_REF_UNAVAILABLE = "atr_ref_unavailable"


class RangeBar(NamedTuple):
    """A minimal bar view used by range window checks.

    ``index`` is the 0-based stream index of the bar; ``is_gap`` marks a
    data_gap placeholder bar (spec 4.7 / DATA_GAP_FLAG).
    """

    index: int
    high: float
    low: float
    close: float
    is_gap: bool = False


@dataclass(frozen=True)
class RangeRejection:
    """A range-establishment rejection with its reason code."""

    reason: str
    detail: str = ""


# ---------------------------------------------------------------------------
# 5.1 Trend context
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` values, or None."""
    if period <= 0 or len(values) < period:
        return None
    return statistics.fmean(values[-period:])


def ols_slope(values: Sequence[float]) -> float | None:
    """OLS slope of ``values`` against x = 0..n-1, or None if fewer than 2."""
    n = len(values)
    if n < 2:
        return None
    x_mean = (n - 1) / 2.0
    y_mean = statistics.fmean(values)
    num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


@dataclass(frozen=True)
class TrendEvaluation:
    """Trend classification plus the not-evaluable reason (None when known)."""

    state: TrendState
    reason: str | None


def trend_at_detailed(
    closes: Sequence[float],
    atr: float | None,
    params: WyckoffV4Params,
) -> TrendEvaluation:
    """Spec 5.1 ``trend_at(u)`` with the not-evaluable reason.

    ``closes`` ends at bar u (C_u = closes[-1]); ``atr`` is ATR_u, the trend
    denominator. Insufficient history or an unavailable/zero ATR makes the
    trend not evaluable (TrendState.UNKNOWN).
    """
    n = params.trend_window
    need = max(params.trend_sma_slow, n + 1, params.trend_sma_fast)
    if len(closes) < need:
        return TrendEvaluation(TrendState.UNKNOWN, "history_insufficient")
    if atr is None:
        return TrendEvaluation(TrendState.UNKNOWN, "atr_unavailable")
    if atr <= 0:
        return TrendEvaluation(TrendState.UNKNOWN, "atr_zero")

    sma_fast = sma(closes, params.trend_sma_fast)
    sma_slow = sma(closes, params.trend_sma_slow)
    if sma_fast is None or sma_slow is None:
        return TrendEvaluation(TrendState.UNKNOWN, "history_insufficient")

    c_u = closes[-1]
    c_prev = closes[-(n + 1)]
    delta = (c_u - c_prev) / atr
    path = abs(c_u - c_prev)
    walked = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    er = path / walked if walked > 0 else 0.0
    slope = ols_slope(closes[-n:]) / atr

    down = (
        c_u < sma_fast
        and c_u < sma_slow
        and delta <= -params.trend_min_displacement_atr
        and slope <= -params.trend_min_abs_slope_atr
        and er >= params.trend_min_er
    )
    up = (
        c_u > sma_fast
        and c_u > sma_slow
        and delta >= params.trend_min_displacement_atr
        and slope >= params.trend_min_abs_slope_atr
        and er >= params.trend_min_er
    )
    if down:
        return TrendEvaluation(TrendState.DOWN, None)
    if up:
        return TrendEvaluation(TrendState.UP, None)
    return TrendEvaluation(TrendState.NEUTRAL, None)


def trend_at(
    closes: Sequence[float],
    atr: float | None,
    params: WyckoffV4Params,
) -> TrendState:
    """Spec 5.1 trend classification at the last bar of ``closes``."""
    return trend_at_detailed(closes, atr, params).state


@dataclass(frozen=True)
class TrendSnapshot:
    """Spec 5.1 snapshot fields submitted with snapshot u.

    ``trend_now`` and ``context_for_next_bar`` store the SAME known value
    ``trend_at(u)``; they are two uses of one value, not two evaluations.
    Mode-p start trends always read ``snapshot_(p-1).context_for_next_bar``.
    ``context_available_at`` is the actual availability time of that value
    (e.g. the bar-end timestamp of bar u); it is bookkeeping, not an input.
    """

    asof_bar: int
    trend_now: TrendState
    context_for_next_bar: TrendState
    context_available_at: datetime | None = None


def make_trend_snapshot(
    asof_bar: int,
    closes: Sequence[float],
    atr: float | None,
    params: WyckoffV4Params,
    context_available_at: datetime | None = None,
) -> TrendSnapshot:
    """Build a TrendSnapshot: trend_at(u) is computed once, stored twice."""
    state = trend_at(closes, atr, params)
    return TrendSnapshot(
        asof_bar=asof_bar,
        trend_now=state,
        context_for_next_bar=state,
        context_available_at=context_available_at,
    )


# ---------------------------------------------------------------------------
# 5.2 / 5.5 TradingRange
# ---------------------------------------------------------------------------


def _range_id(
    origin_bar: int,
    established_bar: int,
    version: int,
    s: float,
    r: float,
    a_ref: float,
    parent_range_id: str | None,
) -> str:
    payload = "|".join(
        [
            "wyckoff-v4-range",
            str(origin_bar),
            str(established_bar),
            str(version),
            repr(s),
            repr(r),
            repr(a_ref),
            parent_range_id or "",
        ]
    )
    return "rng-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _width_reason(w: float, a_ref: float, params: WyckoffV4Params) -> str | None:
    """5.2 width validity: range_min_width_atr*A_ref <= W <= range_max_width_atr*A_ref."""
    if w > params.range_max_width_atr * a_ref:
        return WIDTH_TOO_LARGE
    if w < params.range_min_width_atr * a_ref:
        return WIDTH_TOO_SMALL
    return None


@dataclass(frozen=True)
class TradingRange:
    """A frozen trading-range version (spec 5.2/5.5).

    S, R, epsilon and A_ref are frozen per version. Age counts valid bars
    from the origin inclusive; ``bars_since_established`` is tracked
    separately. A new version becomes effective from the NEXT bar and its
    outside counters start at 0 (never backfilled).
    """

    range_id: str
    version: int
    state: RangeState
    S: float
    R: float
    M: float
    W: float
    A_ref: float
    epsilon: float
    origin_bar: int
    established_bar: int
    parent_range_id: str | None = None
    outside_up_streak: int = 0
    outside_down_streak: int = 0
    invalid_bar_indices: tuple[int, ...] = ()

    def age(self, current_index: int) -> int:
        """Valid bars from origin to current_index, inclusive of both ends."""
        return current_index - self.origin_bar + 1 - sum(self.origin_bar <= i <= current_index for i in self.invalid_bar_indices)

    def bars_since_established(self, current_index: int) -> int:
        return current_index - self.established_bar - sum(self.established_bar < i <= current_index for i in self.invalid_bar_indices)

    def update_outside_streaks(self, close: float) -> tuple[int, int]:
        """5.5 per-bar outside counters vs the frozen boundaries.

        Returns (outside_up_streak, outside_down_streak) after this bar's
        close: a close inside the tolerance band resets that side.
        """
        price = Decimal(str(close))
        eps = Decimal(str(self.epsilon))
        up = self.outside_up_streak + 1 if price > Decimal(str(self.R)) + eps else 0
        down = self.outside_down_streak + 1 if price < Decimal(str(self.S)) - eps else 0
        return up, down

    def with_outside_streaks(self, up: int, down: int) -> "TradingRange":
        """Copy with updated outside counters (the record itself is frozen)."""
        return replace(self, outside_up_streak=up, outside_down_streak=down)

    def next_version(
        self,
        state: RangeState,
        parent_range_id: str | None = None,
    ) -> "TradingRange":
        """New boundary version: same range_id, version+1, counters reset to 0.

        The new version is effective from the NEXT bar; the counters are
        initialized at creation and never backfilled.
        """
        return replace(
            self,
            version=self.version + 1,
            state=state,
            parent_range_id=parent_range_id,
            outside_up_streak=0,
            outside_down_streak=0,
        )

    def touches_support(self, high: float, low: float) -> bool:
        """Bar range [low, high] intersects the support area [S-eps, S+eps]."""
        return low <= self.S + self.epsilon and high >= self.S - self.epsilon

    def touches_resistance(self, high: float, low: float) -> bool:
        """Bar range [low, high] intersects the resistance area [R-eps, R+eps]."""
        return low <= self.R + self.epsilon and high >= self.R - self.epsilon

    def touches_any_boundary(self, high: float, low: float) -> bool:
        return self.touches_support(high, low) or self.touches_resistance(high, low)

    def validate_width(self, params: WyckoffV4Params) -> str | None:
        """Width rejection reason for this version, or None when valid."""
        return _width_reason(self.W, self.A_ref, params)


def establish_path_a(
    origin_bar: int,
    established_bar: int,
    s: float,
    r: float,
    a_ref: float,
    params: WyckoffV4Params,
    price_resolution: float,
    *,
    state: RangeState = RangeState.ACTIVE,
    parent_range_id: str | None = None,
    version: int = 1,
    epsilon: float | None = None,
) -> TradingRange | RangeRejection:
    """Spec 5.3 path-A range constructor (origin = first climax bar).

    ``a_ref`` is A_SC / A_BC. Width is validated against the 5.2 constraint;
    W > range_max_width_atr*A_ref rejects with width_too_large (the AR fact
    stays alive for the caller, this only rejects the range candidate).
    """
    if not isinstance(a_ref, (int, float)) or a_ref <= 0:
        return RangeRejection(ATR_REF_UNAVAILABLE, f"A_ref={a_ref!r}")
    w = r - s
    reason = _width_reason(w, a_ref, params)
    if reason is not None:
        return RangeRejection(reason, f"W={w!r}, A_ref={a_ref!r}")
    if epsilon is None:
        epsilon = boundary_epsilon(
            params.boundary_resolution_units,
            params.boundary_atr_fraction,
            price_resolution,
            a_ref,
        )
    return TradingRange(
        range_id=_range_id(origin_bar, established_bar, version, s, r, a_ref, parent_range_id),
        version=version,
        state=state,
        S=s,
        R=r,
        M=(s + r) / 2.0,
        W=w,
        A_ref=a_ref,
        epsilon=epsilon,
        origin_bar=origin_bar,
        established_bar=established_bar,
        parent_range_id=parent_range_id,
    )


# ---------------------------------------------------------------------------
# 5.4 Path B: greedy touch selection and establishment
# ---------------------------------------------------------------------------


def _bar_touches_area(bar: RangeBar, level: float, eps: float) -> bool:
    return bar.low <= level + eps and bar.high >= level - eps


def greedy_touch_positions(
    bars: Sequence[RangeBar],
    level: float,
    epsilon: float,
    min_separation_bars: int,
) -> tuple[int, ...]:
    """Spec 5.4 greedy touch selection for one side's boundary area.

    Walks candidate bars (bar range intersects [level-eps, level+eps]) in
    time order and counts a candidate when it is at least
    ``min_separation_bars`` positions after the last COUNTED touch and at
    least one bar strictly between them is fully outside the area. Returns
    the positions (0-based indices into ``bars``) of the counted touches.
    """
    counted: list[int] = []
    last: int | None = None
    for pos, bar in enumerate(bars):
        if not _bar_touches_area(bar, level, epsilon):
            continue
        if last is None:
            counted.append(pos)
            last = pos
            continue
        separated = (pos - last) >= min_separation_bars
        gap_between = any(
            not _bar_touches_area(bars[j], level, epsilon) for j in range(last + 1, pos)
        )
        if separated and gap_between:
            counted.append(pos)
            last = pos
    return tuple(counted)


def _window_er(closes: Sequence[float]) -> float:
    """Efficiency ratio over the given closes (first to last).

    Denominator 0 with a complete price series is defined as 0 (spec 5.1).
    """
    path = abs(closes[-1] - closes[0])
    walked = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return path / walked if walked > 0 else 0.0


def establish_path_b(
    bars: Sequence[RangeBar],
    params: WyckoffV4Params,
    atr_ref: float,
    price_resolution: float,
    *,
    state: RangeState = RangeState.ACTIVE,
    parent_range_id: str | None = None,
    version: int = 1,
) -> TradingRange | RangeRejection:
    """Spec 5.4 path-B establishment over the last ``generic_range_window`` bars.

    ``bars`` must contain at least generic_range_window + 1 recent bars ending
    at the establishment bar (the window bars plus one prior close for the
    window ER). ``atr_ref`` is the ATR of the bar BEFORE establishment
    (path-B A_ref). Path B only confirms an engineering trading range, not
    accumulation, distribution or Phase A.
    """
    window_len = params.generic_range_window
    if len(bars) < window_len + 1:
        raise ValueError(
            f"establish_path_b needs at least {window_len + 1} bars "
            f"(window + 1 closes), got {len(bars)}"
        )
    if not isinstance(atr_ref, (int, float)) or atr_ref <= 0:
        return RangeRejection(ATR_REF_UNAVAILABLE, f"atr_ref={atr_ref!r}")

    window = list(bars[-window_len:])
    er_closes = [b.close for b in bars[-(window_len + 1):]]

    if any(b.is_gap for b in bars[-(window_len + 1):]):
        return RangeRejection(DATA_GAP, "data_gap bar inside the range window")

    s = min(b.low for b in window)
    r = max(b.high for b in window)
    w = r - s
    reason = _width_reason(w, atr_ref, params)
    if reason is not None:
        return RangeRejection(reason, f"W={w!r}, atr_ref={atr_ref!r}")

    epsilon = boundary_epsilon(
        params.boundary_resolution_units,
        params.boundary_atr_fraction,
        price_resolution,
        atr_ref,
    )

    er = _window_er(er_closes)
    if er > params.generic_range_max_er:
        return RangeRejection(ER_TOO_HIGH, f"ER_window={er!r}")

    interior = sum(1 for b in window if s + epsilon <= b.close <= r - epsilon)
    if interior < params.range_interior_close_fraction * window_len:
        return RangeRejection(
            INTERIOR_FRACTION,
            f"interior closes {interior}/{window_len}",
        )

    support_touches = greedy_touch_positions(
        window, s, epsilon, params.touch_min_separation_bars
    )
    resistance_touches = greedy_touch_positions(
        window, r, epsilon, params.touch_min_separation_bars
    )
    need = params.range_min_touches_per_side
    if len(support_touches) < need or len(resistance_touches) < need:
        return RangeRejection(
            TOUCHES,
            f"support={len(support_touches)}, resistance={len(resistance_touches)}",
        )

    return TradingRange(
        range_id=_range_id(
            window[0].index, window[-1].index, version, s, r, atr_ref, parent_range_id
        ),
        version=version,
        state=state,
        S=s,
        R=r,
        M=(s + r) / 2.0,
        W=w,
        A_ref=atr_ref,
        epsilon=epsilon,
        origin_bar=window[0].index,
        established_bar=window[-1].index,
        parent_range_id=parent_range_id,
    )


# ---------------------------------------------------------------------------
# 5.5 Lifecycle predicates
# ---------------------------------------------------------------------------


def should_expire(
    rng: TradingRange,
    current_index: int,
    params: WyckoffV4Params,
    clear_outside_trend: bool,
) -> bool:
    """5.5 expired: age >= range_max_age and still no clear outside trend.

    Expiry carries no direction; a clear outside trend routes to the exit
    transitions instead (section 7.5).
    """
    return rng.age(current_index) >= params.range_max_age and not clear_outside_trend


def should_supersede(
    rng: TradingRange,
    current_index: int,
    params: WyckoffV4Params,
    last_touch_index: int | None,
    interior_range_completed: bool,
) -> bool:
    """5.5 superseded conditions for the foreground range.

    Age >= range_stale_min_age, no touch of either boundary area within the
    last ``range_stale_bars`` bars (``last_touch_index`` is the stream index
    of the most recent boundary-touching bar, or None), and a new path-B
    range completed inside. The new range carries parent_range_id -> old one.
    """
    age_ok = rng.age(current_index) >= params.range_stale_min_age
    stale = last_touch_index is None or last_touch_index <= current_index - params.range_stale_bars
    return age_ok and stale and interior_range_completed


def should_retire(phase_e_supported: bool, new_path_b_formed_outside: bool) -> bool:
    """5.5 retired: Phase E got support, then a new path-B consolidation
    forms outside the old range; the old range retires and the new range is
    effective from the next bar."""
    return phase_e_supported and new_path_b_formed_outside


# ---------------------------------------------------------------------------
# 5.6 Internal Creek / Ice
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotImplementedMarker:
    """Marks a spec area intentionally not implemented in this configuration."""

    feature: str


def internal_creek(
    swing_points: Sequence[SwingPoint],
    rng: TradingRange,
    current_bar: int,
) -> SwingPoint | None:
    """Spec 5.6 internal Creek: the most recent confirmed and usable swing
    high after the range origin with price in [M, R+epsilon]; None if absent."""
    candidates = [
        sp
        for sp in swing_points
        if sp.kind == SwingKind.HIGH
        and sp.index > rng.origin_bar
        and sp.usable_from_index <= current_bar
        and rng.M <= sp.price <= rng.R + rng.epsilon
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda sp: (sp.confirmed_at_index, sp.index))


def internal_ice(
    swing_points: Sequence[SwingPoint],
    rng: TradingRange,
    current_bar: int,
) -> SwingPoint | None:
    """Spec 5.6 internal Ice: the most recent confirmed and usable swing low
    after the range origin with price in [S-epsilon, M]; None if absent."""
    candidates = [
        sp
        for sp in swing_points
        if sp.kind == SwingKind.LOW
        and sp.index > rng.origin_bar
        and sp.usable_from_index <= current_bar
        and rng.S - rng.epsilon <= sp.price <= rng.M
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda sp: (sp.confirmed_at_index, sp.index))


def checked_internal_creek(
    swing_points: Sequence[SwingPoint],
    rng: TradingRange,
    current_bar: int,
    creek_mode: CreekMode,
) -> SwingPoint | NotImplementedMarker | None:
    """Internal Creek guarded by creek_mode: RANGE_ONLY => not_implemented."""
    if creek_mode == CreekMode.RANGE_ONLY:
        return NotImplementedMarker("internal_creek")
    return internal_creek(swing_points, rng, current_bar)


def checked_internal_ice(
    swing_points: Sequence[SwingPoint],
    rng: TradingRange,
    current_bar: int,
    creek_mode: CreekMode,
) -> SwingPoint | NotImplementedMarker | None:
    """Internal Ice guarded by creek_mode: RANGE_ONLY => not_implemented."""
    if creek_mode == CreekMode.RANGE_ONLY:
        return NotImplementedMarker("internal_ice")
    return internal_ice(swing_points, rng, current_bar)

"""Availability-aware OHLC evaluation, independent of recognition (§11.4)."""
from dataclasses import dataclass
from datetime import datetime
import math

from .contract import BarV4, check_bar
from .output import dec


@dataclass(frozen=True)
class EvaluationBar:
    bar: BarV4
    atr: float | None
    atr_available_at: datetime | None


@dataclass(frozen=True)
class EvaluationResult:
    outcome: str
    reference_bar_id: tuple | None = None
    reference_price: float | None = None
    reference_available_at: datetime | None = None
    evaluation_atr: float | None = None
    evaluation_atr_available_at: datetime | None = None
    evaluation_start_at: datetime | None = None
    evaluation_wait_seconds: float | None = None
    upper_barrier: float | None = None
    lower_barrier: float | None = None
    hit_bar_id: tuple | None = None
    reasons: tuple[str, ...] = ()
    excluded_overlap_ids: tuple = ()
    data_version: str | None = None
    signal_reference_bar_id: tuple | None = None
    pre_crossed: bool | None = None
    pre_crossed_reason: str | None = None
    pre_crossed_bar_ids: tuple = ()
    direction: int | None = None
    evaluation_horizon: int = 20
    path_bar_count: int = 0
    metrics_complete: bool = False
    metrics_reasons: tuple[str, ...] = ()
    directional_net_displacement: float | None = None
    directional_net_displacement_atr: float | None = None
    max_drawdown: float | None = None
    max_drawdown_atr: float | None = None
    mfe: float | None = None
    mfe_atr: float | None = None
    mae: float | None = None
    mae_atr: float | None = None


def _pre_crossed(tau, records, intervals, reference, barrier_atr):
    """Past crossings are descriptive; unknown intervals never become a false."""
    base = {"signal_reference_bar_id": reference.bar.key if reference else None}
    if reference is None:
        return dict(base, pre_crossed_reason="signal_reference_missing")
    b = reference.bar
    if (not b.is_closed or check_bar(b) or b.received_at is None or
            max(b.bar_end, b.received_at) > tau or "data_gap" in b.quality_flags or
            reference.atr is None or not math.isfinite(reference.atr) or reference.atr <= 0 or
            reference.atr_available_at is None or reference.atr_available_at > tau):
        return dict(base, pre_crossed_reason="signal_reference_unavailable")
    upper = dec(b.close) + dec(barrier_atr) * dec(reference.atr)
    lower = dec(b.close) - dec(barrier_atr) * dec(reference.atr)
    by_interval = {(r.bar.bar_start, r.bar.bar_end): r for r in records}
    crossed, unknown = [], False
    for pair in intervals:
        if pair[0] < b.bar_end or pair[1] > tau:
            continue
        r = by_interval.get(pair)
        if (r is None or not r.bar.is_closed or check_bar(r.bar) or
                "data_gap" in r.bar.quality_flags or r.bar.received_at is None or r.bar.received_at > tau):
            unknown = True
            continue
        if dec(r.bar.high) >= upper or dec(r.bar.low) <= lower:
            crossed.append(r.bar.key)
    if crossed:
        return dict(base, pre_crossed=True, pre_crossed_bar_ids=tuple(crossed))
    overlaps = any(start >= b.bar_end and start < tau < end for start, end in intervals)
    if overlaps or unknown:
        return dict(base, pre_crossed_reason="pre_crossed_unknown")
    # The caller supplies the expected calendar for the reference-to-tau period
    # as well as the future path. A nonempty period with no calendar is unknown.
    if tau > b.bar_end and not any(start >= b.bar_end and start < tau for start, _ in intervals):
        return dict(base, pre_crossed_reason="pre_crossed_unknown")
    return dict(base, pre_crossed=False)


def _path_metrics(path, reference_price, atr, direction, horizon, stop_reason):
    complete = len(path) == horizon
    reasons = [] if complete else [stop_reason or "calendar_end"]
    if direction is None:
        reasons.append("direction_unknown")
    base = dict(path_bar_count=len(path), metrics_complete=complete, metrics_reasons=tuple(reasons))
    if not path or direction is None:
        return base
    price, scale = dec(reference_price), dec(atr)
    closes = [dec(direction) * (dec(b.close) - price) for b in path]
    favorable = [dec(direction) * (dec(b.high if direction == 1 else b.low) - price) for b in path]
    adverse = [-dec(direction) * (dec(b.low if direction == 1 else b.high) - price) for b in path]
    peak, drawdown = dec(0), dec(0)
    for close in closes:
        peak = max(peak, close)
        drawdown = max(drawdown, peak - close)
    values = {"directional_net_displacement": closes[-1], "max_drawdown": drawdown,
              "mfe": max(dec(0), *favorable), "mae": max(dec(0), *adverse)}
    for name, value in values.items():
        base[name] = float(value)
        base[name + "_atr"] = float(value / scale)
    return base


def evaluate_ohlc(signal_available_at: datetime, records: list[EvaluationBar],
                  expected_intervals: list[tuple[datetime, datetime]], *, horizon: int = 20,
                  barrier_atr: float = 2.0, direction: int | None = None,
                  signal_reference: EvaluationBar | None = None,
                  data_version: str | None = None) -> EvaluationResult:
    """First-hit label and full-horizon metrics share one registered calendar path.

    signal_reference is the fact's pattern-end bar or a sequence's confirmation
    bar, with its then-available ATR. Omit direction/reference to report explicitly
    unknown supplementary metrics without changing the symmetric barrier label.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1 or not math.isfinite(barrier_atr) or barrier_atr <= 0:
        raise ValueError("evaluation horizon and barrier must be positive")
    if direction is not None and (isinstance(direction, bool) or not isinstance(direction, int) or direction not in (-1, 1)):
        raise ValueError("evaluation direction must be -1 or 1")
    previous_end = None
    for start, end in expected_intervals:
        if start >= end or previous_end is not None and start < previous_end:
            raise ValueError("evaluation_calendar_invalid")
        previous_end = end
    if len({(r.bar.bar_start, r.bar.bar_end) for r in records}) != len(records):
        raise ValueError("duplicate_evaluation_bar")
    tau = signal_available_at
    supplementary = dict(data_version=data_version, direction=direction, evaluation_horizon=horizon,
                         **_pre_crossed(tau, records, expected_intervals, signal_reference, barrier_atr))
    eligible = [r for r in records if r.bar.is_closed and r.bar.bar_end <= tau and
                r.bar.received_at is not None and r.bar.received_at <= tau and
                "data_gap" not in r.bar.quality_flags]
    if not eligible:
        return EvaluationResult("not_evaluable", **supplementary, reasons=("anchor_unavailable",),
                                metrics_reasons=("anchor_unavailable",))
    anchor = max(eligible, key=lambda r: r.bar.bar_end)
    if check_bar(anchor.bar):
        return EvaluationResult("not_evaluable", **supplementary, reference_bar_id=anchor.bar.key,
                                reasons=("anchor_data_invalid",), metrics_reasons=("anchor_data_invalid",))
    if anchor.atr is None or not math.isfinite(anchor.atr) or anchor.atr <= 0 or anchor.atr_available_at is None or anchor.atr_available_at > tau:
        return EvaluationResult("not_evaluable", **supplementary, reference_bar_id=anchor.bar.key,
                                reasons=("anchor_atr_unavailable",), metrics_reasons=("anchor_atr_unavailable",))
    intervals = [pair for pair in expected_intervals if pair[0] >= tau][:horizon]
    overlaps = tuple(r.bar.key for r in records if r.bar.bar_start < tau < r.bar.bar_end)
    reasons = ["availability_overlap"] if overlaps or any(start < tau < end for start, end in expected_intervals) else []
    base = dict(**supplementary, reference_bar_id=anchor.bar.key, reference_price=anchor.bar.close,
                reference_available_at=max(anchor.bar.bar_end, anchor.bar.received_at),
                evaluation_atr=anchor.atr, evaluation_atr_available_at=anchor.atr_available_at,
                upper_barrier=float(dec(anchor.bar.close) + dec(barrier_atr) * dec(anchor.atr)),
                lower_barrier=float(dec(anchor.bar.close) - dec(barrier_atr) * dec(anchor.atr)),
                excluded_overlap_ids=overlaps)
    if not intervals:
        return EvaluationResult("censored", **base, reasons=tuple([*reasons, "calendar_end"]), metrics_reasons=("calendar_end",))
    base.update(evaluation_start_at=intervals[0][0],
                evaluation_wait_seconds=(intervals[0][0] - tau).total_seconds())
    by_interval = {(r.bar.bar_start, r.bar.bar_end): r for r in records}
    upper, lower = dec(base["upper_barrier"]), dec(base["lower_barrier"])
    path, outcome, hit, stop_reason = [], None, None, None
    for i, pair in enumerate(intervals):
        record = by_interval.get(pair)
        if record is None or not record.bar.is_closed or "data_gap" in record.bar.quality_flags:
            stop_reason = "data_gap"
            break
        b = record.bar
        if check_bar(b) or b.received_at is None:
            stop_reason = "evaluation_data_invalid"
            break
        path.append(b)
        if outcome is not None:
            continue
        if dec(b.open) >= upper or dec(b.open) <= lower:
            outcome, hit = "upper" if dec(b.open) >= upper else "lower", b.key
            reasons.append("gap_at_evaluation_start" if i == 0 else "gap_at_bar_open")
            continue
        up, down = dec(b.high) >= upper, dec(b.low) <= lower
        if up or down:
            outcome, hit = "ambiguous_intrabar" if up and down else "upper" if up else "lower", b.key
    metrics = _path_metrics(path, anchor.bar.close, anchor.atr, direction, horizon, stop_reason)
    if outcome is None:
        outcome = "neither" if metrics["metrics_complete"] else "censored"
        if not metrics["metrics_complete"]:
            reasons.append(stop_reason or "calendar_end")
    return EvaluationResult(outcome, **base, **metrics, hit_bar_id=hit, reasons=tuple(reasons))

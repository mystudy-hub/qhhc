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


def evaluate_ohlc(signal_available_at: datetime, records: list[EvaluationBar],
                  expected_intervals: list[tuple[datetime, datetime]], *, horizon: int = 20,
                  barrier_atr: float = 2.0) -> EvaluationResult:
    """The expected trading calendar selects the start; missing bars cannot be skipped."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1 or not math.isfinite(barrier_atr) or barrier_atr <= 0:
        raise ValueError("evaluation horizon and barrier must be positive")
    previous_end = None
    for start, end in expected_intervals:
        if start >= end or previous_end is not None and start < previous_end:
            raise ValueError("evaluation_calendar_invalid")
        previous_end = end
    if len({(r.bar.bar_start, r.bar.bar_end) for r in records}) != len(records):
        raise ValueError("duplicate_evaluation_bar")
    tau = signal_available_at
    eligible = [r for r in records if r.bar.is_closed and r.bar.bar_end <= tau and
                r.bar.received_at is not None and r.bar.received_at <= tau and
                "data_gap" not in r.bar.quality_flags]
    if not eligible:
        return EvaluationResult("not_evaluable", reasons=("anchor_unavailable",))
    anchor = max(eligible, key=lambda r: r.bar.bar_end)
    if check_bar(anchor.bar):
        return EvaluationResult("not_evaluable", reference_bar_id=anchor.bar.key,
                                reasons=("anchor_data_invalid",))
    if anchor.atr is None or not math.isfinite(anchor.atr) or anchor.atr <= 0 or anchor.atr_available_at is None or anchor.atr_available_at > tau:
        return EvaluationResult("not_evaluable", reference_bar_id=anchor.bar.key,
                                reasons=("anchor_atr_unavailable",))
    intervals = [pair for pair in expected_intervals if pair[0] >= tau][:horizon]
    overlaps = tuple(r.bar.key for r in records if r.bar.bar_start < tau < r.bar.bar_end)
    base = dict(reference_bar_id=anchor.bar.key, reference_price=anchor.bar.close,
                reference_available_at=max(anchor.bar.bar_end, anchor.bar.received_at),
                evaluation_atr=anchor.atr, evaluation_atr_available_at=anchor.atr_available_at,
                upper_barrier=float(dec(anchor.bar.close) + dec(barrier_atr) * dec(anchor.atr)),
                lower_barrier=float(dec(anchor.bar.close) - dec(barrier_atr) * dec(anchor.atr)),
                excluded_overlap_ids=overlaps)
    if not intervals:
        return EvaluationResult("censored", **base, reasons=("calendar_end",))
    base.update(evaluation_start_at=intervals[0][0],
                evaluation_wait_seconds=(intervals[0][0] - tau).total_seconds())
    by_interval = {(r.bar.bar_start, r.bar.bar_end): r for r in records}
    reasons = ["availability_overlap"] if overlaps else []
    upper, lower = dec(base["upper_barrier"]), dec(base["lower_barrier"])
    for i, pair in enumerate(intervals):
        record = by_interval.get(pair)
        if record is None or not record.bar.is_closed or "data_gap" in record.bar.quality_flags:
            return EvaluationResult("censored", **base, reasons=tuple([*reasons, "data_gap"]))
        b = record.bar
        if check_bar(b) or b.received_at is None:
            return EvaluationResult("censored", **base, reasons=tuple([*reasons, "evaluation_data_invalid"]))
        if dec(b.open) >= upper or dec(b.open) <= lower:
            return EvaluationResult("upper" if dec(b.open) >= upper else "lower", **base,
                                    hit_bar_id=b.key, reasons=tuple([*reasons, "gap_at_evaluation_start" if i == 0 else "gap_at_bar_open"]))
        up, down = dec(b.high) >= upper, dec(b.low) <= lower
        if up or down:
            return EvaluationResult("ambiguous_intrabar" if up and down else "upper" if up else "lower",
                                    **base, hit_bar_id=b.key, reasons=tuple(reasons))
    return EvaluationResult("neither" if len(intervals) == horizon else "censored", **base,
                            reasons=tuple(reasons if len(intervals) == horizon else [*reasons, "calendar_end"]))

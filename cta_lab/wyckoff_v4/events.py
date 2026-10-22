"""Causal factual detectors and onset snapshot ring (spec section 6)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable
import statistics

from .indicators import BarFeatures, SwingPoint
from .output import ContextSnapshot, Occurrence, RuleEvaluation, dec, outside
from .params import CreekMode, WyckoffV4Params
from .range import TradingRange, internal_creek, internal_ice
from .types import EventCode as E, TrendState, TriState, and_, or_


class SnapshotRing:
    def __init__(self, params: WyckoffV4Params):
        self._items: deque[ContextSnapshot] = deque(maxlen=params.snapshot_ring_length)

    def append(self, snapshot: ContextSnapshot) -> None:
        if self._items and snapshot.asof_bar <= self._items[-1].asof_bar:
            raise ValueError("snapshot order must increase")
        self._items.append(snapshot)

    def get(self, index: int) -> ContextSnapshot | None:
        return next((s for s in self._items if s.asof_bar == index), None)


@dataclass
class ReclaimWatch:
    direction: int
    start: int
    atr: float
    onset: ContextSnapshot
    extreme: float
    deep: bool


class EventDetector:
    def __init__(self, params: WyckoffV4Params):
        self.params = params
        self.ring = SnapshotRing(params)
        self.watches: dict[tuple[str, int, int], ReclaimWatch] = {}
        self.events: list[Occurrence] = []
        self.evaluations: list[RuleEvaluation] = []

    def detect(self, features: list[BarFeatures], rng: TradingRange | None,
               swings: tuple[SwingPoint, ...], available_at: datetime) -> list[Occurrence]:
        self._features = features
        self._t = len(features) - 1
        self._now = available_at
        self._new: list[Occurrence] = []
        self._rng = rng
        f = features[-1]
        if not f.valid:
            self.evaluations.append(RuleEvaluation("facts", self._t, TriState.NOT_EVALUABLE,
                                                   ("data_gap",)))
            return []
        self._climaxes()
        self._absorption()
        for direction in (1, -1):
            self._reclaim(direction)
            self._strength(direction, swings)
        # Preserve associations without mutating already published occurrences.
        up = next((e for e in self._new if e.event_type is E.BREAKOUT_UP), None)
        down = next((e for e in self._new if e.event_type is E.BREAKOUT_DOWN), None)
        for i, event in enumerate(self._new):
            linked = None
            if event.event_type in (E.UNDERCUT_RECLAIM_BREAKOUT, E.SOS):
                linked = up
            if event.event_type in (E.UPTHRUST_REVERSAL_BREAKDOWN, E.SOW):
                linked = down
            if linked:
                self._new[i] = replace(event, linked_event_ids=(linked.event_id,))
        self.events.extend(self._new)
        return self._new

    def _check(self, rule: str, conditions: list[tuple[str, bool | None]],
               margins: dict[str, float] | None = None) -> bool:
        result = and_(*(TriState.NOT_EVALUABLE if v is None else
                        TriState.TRUE if v else TriState.FALSE for _, v in conditions))
        reasons = tuple(k for k, v in conditions if v is None or not v)
        self.evaluations.append(RuleEvaluation(rule, self._t, result, reasons,
                                               tuple((margins or {}).items())))
        return result is TriState.TRUE

    def _num(self, name: str, comparison: Callable[[float], bool], index: int | None = None) -> bool | None:
        value = getattr(self._features[self._t if index is None else index], name).value
        return None if value is None else comparison(value)

    def _window(self, start: int) -> bool:
        return start >= 0 and all(f.valid for f in self._features[start:self._t + 1])

    def _effort(self, start: int) -> float | None:
        window = self._features[start:self._t + 1]
        if not self._window(start) or any(f.bv_baseline.value is None or f.bv_baseline.value <= 0 for f in window):
            return None
        return sum(f.bar.volume for f in window) / sum(f.bv_baseline.value for f in window)

    def _emit(self, code: E, start: int | None = None, *, onset: ContextSnapshot | None = None,
              metrics: dict | None = None, **kwargs) -> Occurrence:
        t = self._t
        start = t if start is None else start
        onset = onset or self.ring.get(start - 1)
        f = self._features[t]
        oi = f.oi.realtime_view()
        values = {k: getattr(f, k).value for k in ("rvol", "ns", "cp", "lw", "uw", "net_move", "gap")}
        values.update({"atr": self._features[start].atr_baseline.value,
                       "effort": self._effort(start),
                       "cum_effort": (sum(g.rvol.value for g in self._features[start:t + 1])
                                      if all(g.rvol.value is not None for g in self._features[start:t + 1]) else None),
                       "oiz": oi.oi_z.value, "oi_quadrant": oi.oi_quadrant,
                       "oi_reason": oi.oi_z.reason})
        values.update(metrics or {})
        if start < t:
            records = self._features[start:t + 1]
            prior = self._features[max(0, start - self.params.oi_window):start]
            doi = [g.oi.realtime_view().d_oi.value for g in records]
            scale_values = [g.oi.realtime_view().d_oi.value for g in prior]
            reason = None
            if any(v is None for v in doi):
                reason = "oi_missing_or_not_yet_available"
            elif len(prior) < self.params.oi_window or any(v is None for v in scale_values):
                reason = "oi_history_insufficient"
            else:
                scale = statistics.median(abs(v) for v in scale_values)
                if scale <= 0:
                    reason = "oi_zero_scale"
            values["oiz"] = sum(doi) / scale if reason is None else None
            values["oi_reason"] = reason
            contributing = [g.oi.d_oi_available_at for g in [*prior, *records] if g.oi.d_oi_available_at is not None]
            values["oiz_feature_available_at"] = max(contributing) if reason is None and contributing else None
        standard = {"atr": f.atr_baseline_standard, "bv": f.bv_baseline_standard.value,
                    "rvol": f.bar.volume / f.bv_baseline_standard.value
                    if f.bv_baseline_standard.value else None,
                    "ns": f.spread / f.atr_baseline_standard if f.atr_baseline_standard else None}
        margins = {}
        thresholds = {
            E.SC_FEATURE: {"rvol": self.params.climax_min_rvol, "ns": self.params.climax_min_ns, "cp": self.params.sc_min_cp, "lw": self.params.climax_min_rejection_wick},
            E.BC_FEATURE: {"rvol": self.params.climax_min_rvol, "ns": self.params.climax_min_ns, "cp": self.params.bc_max_cp, "uw": self.params.climax_min_rejection_wick},
            E.SOS: {"rvol": self.params.sos_sow_min_rvol, "ns": self.params.sos_sow_min_ns, "cp": self.params.sos_min_cp, "net_move": self.params.strong_bar_min_move_atr},
            E.SOW: {"rvol": self.params.sos_sow_min_rvol, "ns": self.params.sos_sow_min_ns, "cp": self.params.sow_max_cp, "net_move": -self.params.strong_bar_min_move_atr},
            E.EFFORT_RESULT_NARROW_BAR: {"rvol": self.params.absorption_min_rvol, "ns": self.params.absorption_max_ns},
            E.SC_CLUSTER_FEATURE: {"effort": self.params.cluster_min_effort},
            E.BC_CLUSTER_FEATURE: {"effort": self.params.cluster_min_effort},
        }
        for name, threshold in thresholds.get(code, {}).items():
            if values.get(name) is not None:
                margins[name] = values[name] - threshold
        event = Occurrence(f"E:{code.value}:{start}:{t}", code, start, t,
                           self._features[start].bar.bar_end, f.bar.bar_end,
                           max([self._now] + [max(g.bar.bar_end, g.bar.received_at or g.bar.bar_end)
                                             for g in self._features[:t + 1]]),
                           onset_snapshot=onset,
                           onset_snapshot_id=onset.snapshot_id if onset else None,
                           reference_range_id=onset.range_id if onset else None,
                           reference_range_version=onset.range_version if onset else None,
                           frozen_S=onset.S if onset else None,
                           frozen_R=onset.R if onset else None,
                           frozen_epsilon=onset.epsilon if onset else None,
                           observed_metrics=tuple(values.items()),
                           standard_baseline_metrics=tuple(standard.items()),
                           threshold_margins=tuple(margins.items()),
                           quality_flags=tuple(sorted(set().union(*(g.bar.quality_flags for g in self._features[start:t + 1])))),
                           **kwargs)
        self._new.append(event)
        return event

    def _climaxes(self) -> None:
        p = self.params
        t = self._t
        f = self._features[t]
        for d in (1, -1):
            code = E.SC_FEATURE if d == 1 else E.BC_FEATURE
            onset = self.ring.get(t - 1)
            prior = self._features[max(0, t - p.climax_new_extreme_window):t]
            extreme = (f.bar.low < min(g.bar.low for g in prior) if d == 1 else
                       f.bar.high > max(g.bar.high for g in prior)) if len(prior) == p.climax_new_extreme_window else None
            conditions = [("trend_context", None if onset is None or onset.context_for_next_bar is TrendState.UNKNOWN else
                           onset.context_for_next_bar is (TrendState.DOWN if d == 1 else TrendState.UP)),
                          ("new_extreme", extreme), ("continuous", self._window(t - p.climax_new_extreme_window)),
                          ("rvol", self._num("rvol", lambda v: v >= p.climax_min_rvol)),
                          ("ns", self._num("ns", lambda v: v >= p.climax_min_ns)),
                          ("cp", self._num("cp", lambda v: v >= p.sc_min_cp if d == 1 else v <= p.bc_max_cp)),
                          ("wick", self._num("lw" if d == 1 else "uw", lambda v: v >= p.climax_min_rejection_wick)),
                          ("positive_volume", f.bar.volume > 0)]
            single = self._check(code.value, conditions)
            event = self._emit(code, metrics={"extreme": f.bar.low if d == 1 else f.bar.high,
                                             "ns_ref": f.ns.value, "direction": d}) if single else None
            for m in (2, 3):
                start = t - m + 1
                if start < p.climax_new_extreme_window:
                    continue
                context = self.ring.get(start - 1)
                window = self._features[start:t + 1]
                old = self._features[start - p.climax_new_extreme_window:start]
                a = window[0].atr_baseline.value
                effort = self._effort(start)
                wick = self._num("lw" if d == 1 else "uw", lambda v: v >= p.climax_min_rejection_wick)
                cp = self._num("cp", lambda v: v >= p.sc_min_cp if d == 1 else v <= p.bc_max_cp)
                stop = or_(TriState.NOT_EVALUABLE if cp is None else TriState.TRUE if cp else TriState.FALSE,
                           TriState.NOT_EVALUABLE if wick is None else TriState.TRUE if wick else TriState.FALSE)
                low, high = min(g.bar.low for g in window), max(g.bar.high for g in window)
                cluster_code = E.SC_CLUSTER_FEATURE if d == 1 else E.BC_CLUSTER_FEATURE
                if self._check(cluster_code.value, [
                    ("onset_trend", None if context is None or context.context_for_next_bar is TrendState.UNKNOWN else
                     context.context_for_next_bar is (TrendState.DOWN if d == 1 else TrendState.UP)),
                    ("new_extreme", low < min(g.bar.low for g in old) if d == 1 else high > max(g.bar.high for g in old)),
                    ("effort", None if effort is None else effort >= p.cluster_min_effort),
                    ("span", None if a is None or a <= 0 else (high - low) / a >= p.cluster_min_span_atr),
                    ("stop", None if stop is TriState.NOT_EVALUABLE else stop is TriState.TRUE),
                    ("stop_rvol", self._num("rvol", lambda v: v >= p.cluster_stop_min_rvol)),
                    ("positive_volume", all(g.bar.volume > 0 for g in window)),
                    ("continuous", self._window(start - p.climax_new_extreme_window))]):
                    if event:
                        i = self._new.index(event)
                        self._new[i] = replace(event, aliases=(cluster_code,))
                    else:
                        self._emit(cluster_code, start, onset=context,
                                   metrics={"extreme": low if d == 1 else high,
                                            "ns_ref": max(g.ns.value for g in window) if all(g.ns.value is not None for g in window) else None,
                                            "direction": d})
                    break

    def _absorption(self) -> None:
        p = self.params
        f = self._features[-1]
        if self._check("narrow_bar", [("rvol", self._num("rvol", lambda v: v >= p.absorption_min_rvol)),
                                     ("ns", self._num("ns", lambda v: v <= p.absorption_max_ns)),
                                     ("volume", f.bar.volume > 0)]):
            self._emit(E.EFFORT_RESULT_NARROW_BAR)
        start = self._t - 2
        if start <= 0:
            return
        a = self._features[start].atr_baseline.value
        effort = self._effort(start)
        move = None if a is None or a <= 0 else abs(f.bar.close - self._features[start - 1].bar.close) / a
        if self._check("low_progress", [("effort", None if effort is None else effort >= p.absorption_min_rvol),
                                        ("move", None if move is None else move <= p.low_progress_max_move_atr),
                                        ("positive_volume", all(g.bar.volume > 0 for g in self._features[start:])),
                                        ("continuous", self._window(start - 1))]):
            distance = sum(abs(self._features[i].bar.close - self._features[i - 1].bar.close) for i in range(start, self._t + 1)) / a
            self._emit(E.EFFORT_RESULT_LOW_PROGRESS, start, metrics={"path_distance": distance})

    def _reclaim(self, d: int) -> None:
        t, p = self._t, self.params
        f = self._features[t]
        context = self.ring.get(t - 1)
        if context and self._rng and (context.range_id, context.range_version) == (self._rng.range_id, self._rng.version) and context.range_age >= p.event_min_range_age and t > 0 and self._features[t - 1].valid:
            level = context.S if d == 1 else context.R
            extreme = f.bar.low if d == 1 else f.bar.high
            key = (context.range_id, context.range_version, d)
            a = f.atr_baseline.value
            if a and outside(extreme, level, context.epsilon, -d) and not outside(self._features[t - 1].bar.close, level, context.epsilon, -d):
                if key not in self.watches:
                    depth = float(-d * (dec(extreme) - dec(level))) / a
                    self._emit(E.SUPPORT_UNDERCUT if d == 1 else E.RESISTANCE_OVERSHOOT, metrics={"depth": depth})
                    deep = depth > p.spring_max_depth_atr
                    if deep:
                        self._emit(E.DEEP_UNDERCUT if d == 1 else E.DEEP_OVERSHOOT, metrics={"depth": depth})
                    self.watches[key] = ReclaimWatch(d, t, a, context, extreme, deep)
        for key, watch in list(self.watches.items()):
            if watch.direction != d:
                continue
            watch.extreme = min(watch.extreme, f.bar.low) if d == 1 else max(watch.extreme, f.bar.high)
            ctx = watch.onset
            level = ctx.S if d == 1 else ctx.R
            depth = float(-d * (dec(watch.extreme) - dec(level))) / watch.atr
            if not watch.deep and depth > p.spring_max_depth_atr:
                watch.deep = True
                self._emit(E.DEEP_UNDERCUT if d == 1 else E.DEEP_OVERSHOOT, watch.start,
                           onset=ctx, metrics={"depth": depth})
            deadline = watch.start + (p.shakeout_max_pattern_bars if watch.deep else p.spring_max_pattern_bars) - 1
            if t > deadline:
                del self.watches[key]
                continue
            window = self._features[watch.start:t + 1]
            cp = self._num("cp", lambda v: v >= p.spring_min_cp if d == 1 else v <= p.ut_max_cp)
            reclaim = outside(f.bar.close, level, ctx.epsilon, d)
            valid = self._window(watch.start) and all(g.bar.volume > 0 for g in window)
            rvols = [g.rvol.value for g in window]
            if reclaim and cp and valid and all(v is not None for v in rvols):
                max_rvol = max(rvols)
                composite = outside(f.bar.close, ctx.R if d == 1 else ctx.S, ctx.epsilon, d)
                if d == 1:
                    code = E.UNDERCUT_RECLAIM_BREAKOUT if composite else E.SHAKEOUT_RECLAIM if watch.deep else E.SPRING_LOW_VOLUME if max_rvol < p.spring_low_volume_max_rvol else E.SPRING_HIGH_VOLUME if max_rvol > p.spring_high_volume_min_rvol else E.SPRING_OTHER
                else:
                    code = E.UPTHRUST_REVERSAL_BREAKDOWN if composite else E.UPTHRUST_DEEP_REJECTION if watch.deep else E.UT_FEATURE
                self._emit(code, watch.start, onset=ctx, metrics={"extreme": watch.extreme, "depth": depth,
                           "max_rvol": max_rvol, "direction": d,
                           "shakeout_high_volume": watch.deep and max_rvol > p.spring_high_volume_min_rvol})
                del self.watches[key]
            elif t == deadline:
                self._emit(E.UNDERCUT_NOT_RECLAIMED if d == 1 else E.OVERSHOOT_NOT_REJECTED,
                           watch.start, onset=ctx, metrics={"depth": depth})
                del self.watches[key]

    def _strength(self, d: int, swings: tuple[SwingPoint, ...]) -> None:
        p, t, rng = self.params, self._t, self._rng
        f = self._features[t]
        eligible = rng is not None and t > rng.established_bar and rng.age(t - 1) >= p.event_min_range_age
        common = [("body", d * (f.bar.close - f.bar.open) > 0),
                  ("move", self._num("net_move", lambda v: d * v >= p.strong_bar_min_move_atr)),
                  ("cp", self._num("cp", lambda v: v >= p.sos_min_cp if d == 1 else v <= p.sow_max_cp)),
                  ("ns", self._num("ns", lambda v: v >= p.sos_sow_min_ns)),
                  ("rvol", self._num("rvol", lambda v: v >= p.sos_sow_min_rvol))]
        strong = self._check("SOS" if d == 1 else "SOW", common)
        if strong:
            self._emit((E.SOS if d == 1 else E.SOW) if eligible else (E.STRONG_UP_BAR if d == 1 else E.STRONG_DOWN_BAR))
        if not eligible or t == 0 or not self._features[t - 1].valid:
            return
        level = rng.R if d == 1 else rng.S
        if outside(f.bar.close, level, rng.epsilon, d) and not outside(self._features[t - 1].bar.close, level, rng.epsilon, d):
            joc = self._check("JOC" if d == 1 else "FTI", [
                ("body", d * (f.bar.close - f.bar.open) > 0),
                ("move", self._num("net_move", lambda v: d * v >= p.strong_bar_min_move_atr)),
                ("cp", self._num("cp", lambda v: v >= p.joc_min_cp if d == 1 else v <= p.fti_max_cp)),
                ("rvol", self._num("rvol", lambda v: v >= p.strong_break_min_rvol)),
                ("ns", self._num("ns", lambda v: v >= p.strong_break_min_ns))])
            self._emit(E.BREAKOUT_UP if d == 1 else E.BREAKOUT_DOWN,
                       strength=("joc" if d == 1 else "fti") if joc else "ordinary",
                       metrics={"gap_breakout": f.gap.value is not None and f.gap.value >= p.gap_flag_min_atr})
        if strong and p.creek_mode is CreekMode.RANGE_AND_INTERNAL:
            pivot = internal_creek(swings, rng, t) if d == 1 else internal_ice(swings, rng, t)
            if pivot and d * (pivot.price - level) < -rng.epsilon and not outside(self._features[t - 1].bar.close, pivot.price, rng.epsilon, d) and outside(f.bar.close, pivot.price, rng.epsilon, d) and not outside(f.bar.close, level, rng.epsilon, d):
                self._emit(E.INTERNAL_JOC if d == 1 else E.INTERNAL_FTI, frozen_internal_level=pivot.price)

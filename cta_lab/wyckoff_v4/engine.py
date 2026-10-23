"""Atomic closed-bar recognizer with the eleven-step schedule (§9.4)."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta

from .contract import (BarV4, ContractViolation, SeriesContract, check_bar,
                       check_series_contract, check_weight_timing, to_ticks)
from .events import EventDetector
from .evidence import (Dominance, SideGroup, SequenceFilterContext, TriggerEvent,
                       compute_direction_evidence, compute_wave_groups,
                       evaluate_bias_group, filter_current_sequences, larger_context,
                       structure_hypotheses)
from .indicators import IndicatorState
from .lifecycle import Lifecycle
from .output import ContextSnapshot, Occurrence, OiAnnotation, Publication, dumps, outside
from .params import CreekMode, WyckoffV4Params
from .range import (RangeBar, RangeRejection, TradingRange, establish_path_b,
                    ols_slope, should_expire, should_supersede, trend_at_detailed)
from .types import EventCode as E, HypothesisStatus as H, MarketRegime as M, RangeState as R, TrendState
from .waves import WaveState


class WyckoffV4Engine:
    def __init__(self, contract: SeriesContract, params: WyckoffV4Params | None = None,
                 *, timeframe: str = "1d", index_available: bool = True,
                 actual_index_source: str | None = None, compute_delay: timedelta = timedelta(0)):
        self.contract, self.params, self.timeframe = contract, params or WyckoffV4Params(), timeframe
        self.params.validate()
        self.eligibility = check_series_contract(contract, index_available)
        self.source_matches = actual_index_source is None or actual_index_source == contract.index_source
        if compute_delay < timedelta(0):
            raise ValueError("compute_delay must be nonnegative")
        self.compute_delay = compute_delay
        self.indicators = IndicatorState(self.params, intraday=timeframe not in ("1d", "D", "daily"), oi_method=contract.oi_method)
        self.detector = EventDetector(self.params)
        self.lifecycle = Lifecycle(self.params, contract.price_resolution)
        self.waves = WaveState(self.params)
        self.front_range: TradingRange | None = None
        self.range_history: list[TradingRange] = []
        self.publications: list[Publication] = []
        self._bars: list[BarV4] = []
        self._features = []
        self._keys: set = set()
        self._closes: list[float] = []
        self._warmup_count = 0
        self._last_gap = -1
        self._last_detection_gap = -1
        self._last_touch: int | None = None
        self._regime, self._pending, self._pending_count = M.UNKNOWN, None, 0
        self._phase_e: dict | None = None
        self.phase_history: list[dict] = []
        self.oi_annotations: list[OiAnnotation] = []
        self._larger_context: dict[tuple, TrendState] = {}
        self._closed_ranges: set[tuple] = set()

    def process_bar(self, bar: BarV4, *, decision_time: datetime | None = None,
                    weight_asof_at: datetime | None = None) -> Publication | None:
        """Unclosed bars do not enter the formal stream. Errors roll back state."""
        if not bar.is_closed:
            return None
        problems = check_bar(bar)
        if problems and "data_gap" not in bar.quality_flags:
            raise ContractViolation(str(problems))
        if bar.received_at is None:
            raise ContractViolation("received_at_required_for_realtime")
        for price in (bar.open, bar.high, bar.low, bar.close):
            to_ticks(price, self.contract.price_resolution)
        if bar.key in self._keys:
            raise ContractViolation("duplicate_bar_key")
        if self._bars and bar.bar_start <= self._bars[-1].bar_start:
            raise ContractViolation("bar_start_out_of_order")
        arrival = max(bar.bar_end, bar.received_at or bar.bar_end)
        if decision_time is not None and decision_time < arrival:
            raise ContractViolation("bar_not_yet_available")
        now = max(arrival, decision_time or arrival) + self.compute_delay
        if self.publications:
            previous = datetime.fromisoformat(self.publications[-1].snapshot["available_at"])
            now = max(now, previous)
        backup = copy.deepcopy({k: v for k, v in self.__dict__.items() if k != "publications"})
        backup["publications"] = self.publications.copy()
        try:
            return self._process(bar, now, weight_asof_at)
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(backup)
            raise

    def _process(self, bar: BarV4, now: datetime, weight_asof_at: datetime | None) -> Publication:
        t, p = len(self._bars), self.params
        tx = f"T:{self.contract.index_id}:{self.timeframe}:{t}"
        order = [1]
        reasons = []
        if not self.eligibility.eligible:
            reasons.append(self.eligibility.reason.value)
        if not self.source_matches:
            reasons.append("index_source_mismatch")
        if weight_asof_at is not None and check_weight_timing(bar, weight_asof_at):
            reasons.append("ex_post_weights")
        effective_bar = bar
        if reasons:
            effective_bar = replace(bar, quality_flags=bar.quality_flags | {"data_gap"})
        f = self.indicators.advance(effective_bar, defer_commit=True)
        self._features.append(f)
        order.append(2)
        if f.valid:
            self._warmup_count += 1
        else:
            self._last_gap = t
            self._closes.clear()
            reasons.append("data_gap")
            self.lifecycle.pause_gap()
            # A reclaim pattern crossing missing OHLC is permanently unobservable
            # in this realtime version; later repairs require a distinct data_version.
            self.detector.watches.clear()
        if self._warmup_count < p.warmup_bars:
            reasons.append("warmup_incomplete")
        if f.atr_baseline.value is None or f.atr_baseline.value <= 0:
            reasons.append("atr_unavailable")
        if f.bv_baseline.value is None or f.bv_baseline.value <= 0:
            reasons.append("bv_unavailable")
        prior_range = self.front_range
        if self.front_range and t > self.front_range.established_bar:
            if f.valid:
                up, down = self.front_range.update_outside_streaks(bar.close)
                self.front_range = self.front_range.with_outside_streaks(up, down)
                if self.front_range.touches_any_boundary(bar.high, bar.low):
                    self._last_touch = t
            else:
                self.front_range = self.front_range.with_outside_streaks(0, 0)
                self.front_range = replace(self.front_range,
                                           invalid_bar_indices=(*self.front_range.invalid_bar_indices, t))
        self.lifecycle.begin(self._features, self.front_range, now, tx)
        order.append(3)
        self.lifecycle.invalidate_prices()
        self.front_range = self.lifecycle.rng
        eval_start = len(self.detector.evaluations)
        order.append(4)
        facts = []
        if not reasons:
            detection_features = [*self.indicators.features, f]
            facts = self.detector.detect(detection_features, self.front_range, self.indicators.swing_points, now)
            facts = [self._stamp(e, now) for e in facts]
            self.lifecycle.ingest(facts, self.indicators.swing_points)
        else:
            self._last_detection_gap = t
        for reason in dict.fromkeys(reasons):
            code = next((code for code in E if code.value == reason), None)
            if code:
                facts.append(Occurrence(f"E:{reason}:{t}", code, t, t, bar.bar_end, bar.bar_end, now,
                                        rejection_reasons=(reason,)))
        if f.valid and "constituent_change" in bar.quality_flags:
            facts.append(Occurrence(f"E:constituent_change:{t}", E.CONSTITUENT_CHANGE, t, t,
                                    bar.bar_end, bar.bar_end, now, quality_flags=("constituent_change",)))
        order.append(5)
        if not reasons:
            self.lifecycle.confirm()
        order.append(6)
        # Missing bars suspend windows; this transaction remains in the audit log.
        if f.valid:
            self.lifecycle.expire_candidates()
        self.front_range = self.lifecycle.rng
        order.append(7)
        if f.valid:
            self.indicators.commit_prepared()
            self._closes.append(bar.close)
        if p.wave_module_enabled:
            self.waves.feed(f)
        f = replace(f, oi=f.oi.realtime_view(now))
        self._features[-1] = f
        trend = trend_at_detailed(self._closes, self.indicators.committed_atr, p)
        order.append(8)
        evidence, filtered, structure = self._evidence(now)
        self._structures(structure, evidence, now)
        phase_candidates, phase_supported = self._phases(trend.state, evidence, now)
        order.append(9)
        self._range_commit(now, trend.state)
        if self.front_range != self.lifecycle.rng:
            self.lifecycle.rng = self.front_range
        current_ref = (self.front_range.range_id, self.front_range.version) if self.front_range else None
        previous_ref = (prior_range.range_id, prior_range.version) if prior_range else None
        if current_ref != previous_ref:
            evidence, filtered, structure = self._evidence(now)
            phase_candidates, phase_supported = self._phases(trend.state, evidence, now)
        self._regime_update(trend.state, reasons)
        order.append(10)
        self.lifecycle.validate_state()
        rng = self.front_range
        acceptance = self.lifecycle.active_acceptance()
        context = ContextSnapshot(f"S:{t}", t, now, trend.state,
                                  rng.range_id if rng else None, rng.version if rng else None,
                                  rng.S if rng else None, rng.R if rng else None,
                                  rng.epsilon if rng else None, rng.age(t) if rng else 0,
                                  evidence.dominance.value, tuple(s.sequence_id for s in filtered.counted) if filtered else ())
        self.detector.ring.append(context)
        snapshot = {"snapshot_id": context.snapshot_id, "asof_bar": t, "bar_end": bar.bar_end,
                    "available_at": now, "transaction_id": tx,
                    "index_id": self.contract.index_id, "timeframe": self.timeframe,
                    "data_profile": self.contract.data_profile, "index_source": self.contract.index_source,
                    "index_method_version": self.contract.index_method_version,
                    "rule_version": p.rule_version, "parameter_version": p.parameter_version,
                    "data_version": self.contract.data_version, "baseline_mode": p.baseline_mode,
                    "market_regime": self._regime, "pending_regime": self._pending,
                    "trend_now_asof_bar": trend.state, "context_for_next_bar": trend.state,
                    "context_available_at": now, "range": rng,
                    "acceptance_id": acceptance.acceptance_id if acceptance else None,
                    "outside_up_streak": rng.outside_up_streak if rng else 0,
                    "outside_down_streak": rng.outside_down_streak if rng else 0,
                    "history_confirmed_sequences": filtered.history_confirmed if filtered else (),
                    "current_valid_sequences": filtered.current_valid if filtered else (),
                    "counted_sequences": filtered.counted if filtered else (),
                    "evidence_groups": evidence.evidence_groups, "path_states": evidence.path_states,
                    "dominance": evidence.dominance, "dominance_paths": evidence.dominance_paths,
                    "evaluation_reasons": tuple(dict.fromkeys([*reasons, *evidence.evaluation_reasons])),
                    "structure": structure, "phase_candidate": phase_candidates,
                    "phase_supported_by_rules": phase_supported,
                    "phase_history": [*self.lifecycle.phase_history, *self.phase_history],
                    "hypotheses": sorted(self.lifecycle.hypotheses.values(), key=lambda h: h.hypothesis_id),
                    "acceptances": sorted(self.lifecycle.acceptances.values(), key=lambda a: a.acceptance_id),
                    "indicators": f, "atr_now": self.indicators.committed_atr,
                    "feature_status": {"internal_levels": "not_implemented" if p.creek_mode is CreekMode.RANGE_ONLY else "implemented",
                                       "phase_c_without_spring_ut": "not_implemented"},
                    "quality": "not_evaluable" if reasons else "ok", "probability": None}
        order.append(11)
        publication = Publication(dumps(snapshot), tuple(self._stamp(e, now) for e in [*facts, *self.lifecycle.new_events]),
                                  tuple(self.lifecycle.new_transitions),
                                  tuple(self.detector.evaluations[eval_start:]), tuple(order))
        self.publications.append(publication)
        self._bars.append(bar)
        self._keys.add(bar.key)
        return publication

    def _stamp(self, event: Occurrence, now: datetime) -> Occurrence:
        return replace(event, index_id=self.contract.index_id, data_profile=self.contract.data_profile,
                       index_source=self.contract.index_source, index_method_version=self.contract.index_method_version,
                       timeframe=self.timeframe, baseline_mode=self.params.baseline_mode.value,
                       rule_version=self.params.rule_version, parameter_version=self.params.parameter_version,
                       data_version=self.contract.data_version, data_asof=now)

    def _evidence(self, now: datetime):
        t, p, rng = len(self._features) - 1, self.params, self.front_range
        if not rng:
            return compute_direction_evidence(None, SideGroup.NOT_EVALUABLE, (), p), None, None
        groups = compute_wave_groups(self.waves.completed_waves, rng, p, t, decision_time=now)
        filtered = filter_current_sequences(self.lifecycle.current_sequences(),
                                           SequenceFilterContext(t, rng.range_id, rng.version, now, p,
                                                                 tuple(i for i, f in enumerate(self._features) if not f.valid)))
        coverage = t - self._last_detection_gap >= p.bias_sequence_recent_bars and self._warmup_count >= p.warmup_bars
        b = evaluate_bias_group(filtered.counted) if coverage else SideGroup.NOT_EVALUABLE
        evidence = compute_direction_evidence(groups, b, filtered.counted, p)
        triggers = [TriggerEvent(e.event_type, e.pattern_end, e.primitive_event_ids or (e.event_id,))
                    for d in (1, -1) for e in self._structure_triggers(d, now)]
        current = [h for h in self.lifecycle.hypotheses.values() if self.lifecycle._matching(h) and h.status in (H.CANDIDATE, H.CONFIRMED)]
        structure = structure_hypotheses(rng, groups, filtered.counted, evidence, triggers, p, t,
                                        unfailed_spring_shakeout=sum(h.hypothesis_type == "SPRING" for h in current),
                                        unfailed_ut_test=sum(h.hypothesis_type == "UT" for h in current))
        key = (rng.range_id, rng.version)
        context = self._larger_context.get(key, TrendState.UNKNOWN)
        structure = replace(structure, larger_context=context,
                            reaccumulation_candidate=structure.accumulation_candidate and context is TrendState.UP,
                            redistribution_candidate=structure.distribution_candidate and context is TrendState.DOWN)
        return evidence, filtered, structure

    def _structure_triggers(self, direction: int, now: datetime) -> tuple[Occurrence, ...]:
        """Use the same currently available triggers for entry and persistence."""
        rng, lc, t = self.front_range, self.lifecycle, len(self._features) - 1
        if rng is None:
            return ()
        codes = (E.SOS, E.INTERNAL_JOC, E.LPS) if direction == 1 else (E.SOW, E.INTERNAL_FTI, E.LPSY)
        eligible = []
        for event in lc.events:
            if event.event_type not in codes or event.available_at > now or not (
                    0 <= t - event.pattern_end < self.params.bias_signal_recent_bars):
                continue
            if (event.reference_range_id, event.reference_range_version) != (rng.range_id, rng.version):
                continue
            if event.event_type in (E.LPS, E.LPSY):
                owner = lc.hypotheses.get(event.evidence_owner_id)
                if owner is None or owner.status is not H.CONFIRMED or owner.direction != direction or (
                        owner.reference_range_id, owner.reference_range_version) != (rng.range_id, rng.version) or owner.status_available_at > now:
                    continue
            eligible.append(event)
        return tuple(sorted(eligible, key=lambda event: (event.pattern_end, event.available_at, event.event_id)))

    def _structures(self, structure, evidence, now):
        if not structure or not self.front_range:
            return
        t, lc, p = len(self._features) - 1, self.lifecycle, self.params
        a = lc.active_acceptance()
        triggers = {d: self._structure_triggers(d, now) for d in (1, -1)}
        for h in list(lc.hypotheses.values()):
            if h.hypothesis_type != "STRUCTURE" or not lc._active(h) or not lc._matching(h):
                continue
            if triggers[h.direction]:
                h.data["last_trigger"] = max(h.data["last_trigger"], triggers[h.direction][-1].pattern_end)
            opposite = evidence.dominance is (Dominance.SUPPLY if h.direction == 1 else Dominance.DEMAND)
            if evidence.dominance is not Dominance.NOT_EVALUABLE and t > h.created_bar:
                h.failure_inside_streak = h.failure_inside_streak + 1 if opposite else 0
            if h.failure_inside_streak >= p.structural_opposite_persistence_bars or a and a.direction != h.direction:
                lc._change(h, H.INVALIDATED, "opposite_direction")
            elif not triggers[h.direction] and evidence.dominance is not Dominance.NOT_EVALUABLE and evidence.dominance is not (Dominance.DEMAND if h.direction == 1 else Dominance.SUPPLY):
                lc._change(h, H.EXPIRED, "signal_stale")
        for d, entry in ((1, structure.accumulation_candidate), (-1, structure.distribution_candidate)):
            if not entry:
                continue
            active = next((h for h in lc.hypotheses.values() if h.hypothesis_type == "STRUCTURE" and h.direction == d and lc._matching(h) and lc._active(h)), None)
            if active is None and triggers[d]:
                source = triggers[d][-1]
                lc._new("STRUCTURE", d, source, None, last_trigger=source.pattern_end)

    def _has_standalone_phase_d(self, now: datetime) -> bool:
        rng, lc = self.front_range, self.lifecycle
        if rng is None:
            return False
        for event in lc.events:
            if event.available_at > now or (event.reference_range_id, event.reference_range_version) != (rng.range_id, rng.version):
                continue
            internal = event.event_type in (E.INTERNAL_JOC, E.INTERNAL_FTI)
            external = event.event_type in (E.BREAKOUT_UP, E.BREAKOUT_DOWN) and event.strength in ("joc", "fti")
            if not internal and not external:
                continue
            d = 1 if event.event_type in (E.INTERNAL_JOC, E.BREAKOUT_UP) else -1
            owners = [h for h in lc.hypotheses.values() if lc._matching(h) and h.direction == d
                      and h.status_available_at <= now and
                      (h.hypothesis_id == event.evidence_owner_id or event.event_id in h.source_event_ids or
                       event.event_id in h.supporting_evidence_ids)]
            if internal:
                if any(h.hypothesis_type == "LPS" and h.source_kind == "internal_break" and h.status is H.CANDIDATE
                       for h in owners):
                    return True
            else:
                if any(h.hypothesis_type == "BREAKOUT" and lc._active(h) for h in owners):
                    return True
                acceptance = lc.active_acceptance()
                if acceptance and acceptance.direction == d and acceptance.status_available_at <= now and (
                        event.acceptance_id == acceptance.acceptance_id or event.event_id in acceptance.source_event_ids or
                        any(h.hypothesis_id == acceptance.source_hypothesis_id for h in owners)):
                    return True
        return False

    def _phases(self, trend: TrendState, evidence, now):
        t, p, lc, rng = len(self._features) - 1, self.params, self.lifecycle, self.front_range
        candidates, supported = [], []
        if rng and rng.age(t) >= p.mature_range_age:
            candidates.append("B")
        current = [h for h in lc.hypotheses.values() if lc._active(h) and
                   (lc._matching(h) or h.hypothesis_type == "CLIMAX" and
                    (h.data.get("path_a_range_id") is None or rng is not None and h.data.get("path_a_range_id") == rng.range_id))]
        if any(h.hypothesis_type in ("SPRING", "UT", "UTAD") for h in current):
            candidates.append("C")
        for h in current:
            if h.hypothesis_type == "CLIMAX" and h.status is H.CONFIRMED and "B" not in candidates:
                supported.append({"phase": "A", "evidence_id": h.hypothesis_id, "available_at": h.status_available_at})
        for key, phase in list(lc.phase_d.items()):
            owner = lc.hypotheses[phase["lps_id"]]
            if owner.status is not H.CONFIRMED or not lc._matching(owner):
                lc._end_phase_d(key, "lps_terminated")
                continue
            if owner.status_available_at > now:
                continue
            candidates.append("D")
            elapsed = lc._elapsed(owner.confirmed_bar, t)
            d = owner.direction
            push = self._features[-1].valid and d * (self._features[-1].bar.close - owner.data["pre_pullback_extreme"]) > 0
            direction = evidence.dominance is (Dominance.DEMAND if d == 1 else Dominance.SUPPLY)
            if phase["status"] == "candidate" and 1 <= elapsed <= p.phase_d_followthrough_bars and (push or direction):
                phase.update(status="confirmed", confirmed_bar=t, available_at=now)
            if phase["status"] == "confirmed":
                if self._features[-1].valid:
                    supported.append({"phase": "D", **phase})
        if self._has_standalone_phase_d(now):
            candidates.append("D")
        a = lc.active_acceptance()
        e_ok = False
        if self._features[-1].valid and rng and a and len(self._closes) > p.trend_window:
            d = a.direction
            streak = rng.outside_up_streak if d == 1 else rng.outside_down_streak
            delta = self._closes[-1] - self._closes[-p.trend_window - 1]
            path = sum(abs(b - a_) for a_, b in zip(self._closes[-p.trend_window - 1:], self._closes[-p.trend_window:]))
            slope = ols_slope(self._closes[-p.trend_window:])
            er = abs(delta) / path if path else 0
            e_ok = streak >= p.phase_e_min_outside_closes and d * (self._closes[-1] - (rng.R if d == 1 else rng.S)) / rng.A_ref >= p.phase_e_min_distance_atr and d * delta > 0 and d * slope > 0 and er >= p.phase_e_min_er
        same_acceptance = self._phase_e is None or a is not None and self._phase_e["acceptance_id"] == a.acceptance_id
        if self._phase_e and (not e_ok or not same_acceptance):
            reason = "conditions_no_longer_met" if a and same_acceptance else "acceptance_terminated"
            self.phase_history.append({**self._phase_e, "ended_bar": t, "reason": reason})
            self._phase_e = None
        if e_ok:
            if self._phase_e is None or self._phase_e["acceptance_id"] != a.acceptance_id:
                self._phase_e = {"phase": "E", "phase_id": f"PE:{a.acceptance_id}:{t}",
                                 "acceptance_id": a.acceptance_id, "confirmed_bar": t,
                                 "reference_range_id": rng.range_id, "reference_range_version": rng.version,
                                 "direction": a.direction, "available_at": now}
            supported.append(self._phase_e.copy())
        return tuple(dict.fromkeys(candidates)), supported

    def _acceptance_has_phase_e(self, now: datetime) -> bool:
        rng, a = self.front_range, self.lifecycle.active_acceptance()
        if rng is None or a is None or a.status_available_at > now:
            return False
        return any(phase.get("acceptance_id") == a.acceptance_id and phase.get("direction") == a.direction and
                   (phase.get("reference_range_id"), phase.get("reference_range_version")) == (rng.range_id, rng.version) and
                   phase["available_at"] <= now
                   for phase in (*self.phase_history, *([self._phase_e] if self._phase_e else [])))

    def _range_commit(self, now, trend=TrendState.UNKNOWN):
        t, p, lc = len(self._features) - 1, self.params, self.lifecycle
        rng = self.front_range
        f = self._features[-1]
        candidate = None
        if f.valid and f.atr_baseline.value and self._warmup_count >= p.warmup_bars and len(self._features) >= p.generic_range_window + 1:
            bars = [RangeBar(i, g.bar.high, g.bar.low, g.bar.close, not g.valid) for i, g in enumerate(self._features)]
            candidate = establish_path_b(bars, p, f.atr_baseline.value, self.contract.price_resolution)
            if isinstance(candidate, RangeRejection):
                candidate = None
        pending_a = next((r for r in lc.path_a_ranges if r.established_bar == t), None)
        close_reason = None
        if rng:
            exterior_trend = (rng.state is R.EXIT_UP_ACCEPTED and trend is TrendState.UP or
                              rng.state is R.EXIT_DOWN_ACCEPTED and trend is TrendState.DOWN)
            if should_expire(rng, t, p, exterior_trend):
                close_reason = R.EXPIRED
            elif candidate and self._acceptance_has_phase_e(now) and (
                    lc.active_acceptance().direction == 1 and candidate.S > rng.R + rng.epsilon or
                    lc.active_acceptance().direction == -1 and candidate.R < rng.S - rng.epsilon):
                close_reason = R.RETIRED
            elif candidate and candidate.S >= rng.S and candidate.R <= rng.R and should_supersede(rng, t, p, self._last_touch, True):
                close_reason = R.SUPERSEDED
            if close_reason:
                closed = replace(rng, state=close_reason)
                self.range_history.append(closed)
                self._closed_ranges.add((rng.range_id, rng.version))
                lc.close_range(rng)
                self.front_range = None
                if candidate:
                    candidate = replace(candidate, parent_range_id=rng.range_id)
            else:
                candidate = None
                pending_a = None
        if self.front_range is None:
            self.front_range = pending_a or candidate
            if self.front_range:
                self._last_touch = t
                key = (self.front_range.range_id, self.front_range.version)
                self._larger_context[key] = larger_context(self._closes[:-1], f.atr_baseline.value, p)
        lc.rng = self.front_range

    def _regime_update(self, trend, reasons):
        if reasons or trend is TrendState.UNKNOWN:
            self._regime, self._pending, self._pending_count = M.UNKNOWN, None, 0
            return
        rng, c = self.front_range, self._features[-1].bar.close
        if rng and not outside(c, rng.S, rng.epsilon, -1) and not outside(c, rng.R, rng.epsilon, 1):
            target = M.RANGE
        elif rng:
            target = M.UPTREND if rng.state is R.EXIT_UP_ACCEPTED and trend is TrendState.UP else M.DOWNTREND if rng.state is R.EXIT_DOWN_ACCEPTED and trend is TrendState.DOWN else M.TRANSITION
        else:
            target = M.UPTREND if trend is TrendState.UP else M.DOWNTREND if trend is TrendState.DOWN else M.TRANSITION
        if target is self._regime:
            self._pending, self._pending_count = None, 0
        else:
            self._pending_count = self._pending_count + 1 if target is self._pending else 1
            self._pending = target
            if self._pending_count >= self.params.regime_persistence_bars:
                self._regime, self._pending, self._pending_count = target, None, 0

    def replay(self, bars):
        return tuple(result for bar in bars if (result := self.process_bar(bar)) is not None)

    def append_available_oi(self, asof: datetime) -> tuple[OiAnnotation, ...]:
        """Append newly arrived descriptive OI; previously published snapshots stay frozen."""
        recorded = {a.bar_index for a in self.oi_annotations}
        added = []
        for feature in self.indicators.features:
            oi = feature.oi
            if feature.index in recorded or oi.oi_z.value is None or oi.oi_z_available_at is None or oi.oi_z_available_at > asof:
                continue
            annotation = OiAnnotation(feature.index, oi.oi_z_available_at, asof, oi.oi_z.value,
                                      oi.oi_quadrant.value if oi.oi_quadrant else None,
                                      self.contract.data_version)
            self.oi_annotations.append(annotation)
            added.append(annotation)
        return tuple(added)


def assert_prefix_replay(bars: list[BarV4], factory) -> None:
    """Compare every publication byte-for-byte, including append-only logs."""
    full = factory().replay(bars)
    for size in range(1, len(bars) + 1):
        prefix = factory().replay(bars[:size])
        if tuple(p.to_json() for p in prefix) != tuple(p.to_json() for p in full[:size]):
            raise AssertionError(f"prefix replay diverged at {size}")

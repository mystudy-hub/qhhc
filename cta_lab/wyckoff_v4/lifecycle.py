"""Dependency ordered hypotheses, acceptance and pullback transactions (§7)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import statistics

from .evidence import SequenceRecord
from .indicators import BarFeatures, SwingKind, SwingPoint
from .output import Occurrence, Transition, at_least_step, dec, outside
from .params import WyckoffV4Params
from .range import TradingRange, RangeRejection, establish_path_a, ols_slope
from .types import AcceptanceMode, CountingOrigin, EventCode as E, HypothesisStatus as H, RangeState as R


@dataclass
class Hypothesis:
    hypothesis_id: str
    hypothesis_type: str
    direction: int
    source_kind: str
    source_event_ids: tuple[str, ...]
    reference_range_id: str | None
    reference_range_version: int | None
    created_bar: int
    confirmation_deadline_bar: int
    created_at: datetime
    status_available_at: datetime
    frozen_S: float | None = None
    frozen_R: float | None = None
    frozen_epsilon: float = 0
    lifecycle_parent_ref: tuple[str, str] | None = None
    status: H = H.CANDIDATE
    confirmed_bar: int | None = None
    confirmed_at: datetime | None = None
    invalidated_at: datetime | None = None
    expires_at: datetime | None = None
    valid_through_bar: int | None = None
    pending_observation: dict | None = None
    failure_inside_streak: int = 0
    counting_origin: CountingOrigin = CountingOrigin.POST
    counting_origin_bar: int = 0
    supporting_evidence_ids: list[str] = field(default_factory=list)
    state_transition_history: list[Transition] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    confirmation_conditions: tuple[dict, ...] = ()
    invalidation_conditions: tuple[dict, ...] = ()


@dataclass
class Acceptance:
    acceptance_id: str
    direction: int
    reference_range_id: str
    reference_range_version: int
    accepted_bar: int
    accepted_at: datetime
    available_at: datetime
    status_available_at: datetime
    mode: AcceptanceMode
    source_hypothesis_id: str | None
    source_event_ids: tuple[str, ...]
    prior_attempt_ids: tuple[str, ...]
    frozen_S: float
    frozen_R: float
    frozen_epsilon: float
    primary_basis: str
    confirmation_bases: list[str]
    failure_inside_streak: int = 0
    status: H = H.CONFIRMED
    invalidated_at: datetime | None = None
    expires_at: datetime | None = None
    valid_through_bar: int | None = None
    state_transition_history: list[Transition] = field(default_factory=list)
    counting_origin: CountingOrigin = CountingOrigin.POST
    counting_origin_bar: int = 0


class Lifecycle:
    def __init__(self, params: WyckoffV4Params, price_resolution: float):
        self.params = params
        self.resolution = price_resolution
        self.hypotheses: dict[str, Hypothesis] = {}
        self.acceptances: dict[str, Acceptance] = {}
        self.transitions: list[Transition] = []
        self.events: list[Occurrence] = []
        self.sequences: list[SequenceRecord] = []
        self.path_a_ranges: list[TradingRange] = []
        self.current_lps: dict[tuple[str, int, int], str] = {}
        self.phase_d: dict[tuple[str, int, int], dict] = {}
        self.phase_history: list[dict] = []

    def begin(self, features: list[BarFeatures], rng: TradingRange | None,
              available_at: datetime, transaction_id: str) -> None:
        self.features, self.rng = features, rng
        self.t, self.now, self.tx = len(features) - 1, available_at, transaction_id
        self.new_events: list[Occurrence] = []
        self.new_transitions: list[Transition] = []
        self._facts: list[Occurrence] = []
        self._new_breakouts: dict[int, Hypothesis] = {}

    def _active(self, h: Hypothesis | Acceptance) -> bool:
        return h.status in (H.CANDIDATE, H.CONFIRMED)

    def _matching(self, h: Hypothesis | Acceptance) -> bool:
        return self.rng is not None and (h.reference_range_id, h.reference_range_version) == (self.rng.range_id, self.rng.version)

    def _change(self, obj: Hypothesis | Acceptance, status: H, reason: str) -> None:
        if not self._active(obj) or obj.status is status:
            return
        kind = "hypothesis" if isinstance(obj, Hypothesis) else "acceptance"
        identifier = obj.hypothesis_id if isinstance(obj, Hypothesis) else obj.acceptance_id
        previous = obj.status.value
        obj.status = status
        obj.status_available_at = self.now
        if status is H.CONFIRMED:
            obj.confirmed_bar, obj.confirmed_at = self.t, self.features[-1].bar.bar_end
        elif status is H.INVALIDATED:
            obj.invalidated_at = self.now
        elif status is H.EXPIRED:
            obj.expires_at = self.now
        tr = Transition(kind, identifier, previous, status.value, reason, self.t, self.now, self.tx)
        obj.state_transition_history.append(tr)
        self.transitions.append(tr)
        self.new_transitions.append(tr)
        if status in (H.INVALIDATED, H.EXPIRED):
            for child in sorted(self.hypotheses.values(), key=lambda h: h.hypothesis_id):
                if child.lifecycle_parent_ref == (kind, identifier):
                    self._change(child, status, "parent_" + reason)
            for key, phase in list(self.phase_d.items()):
                if phase["lps_id"] == identifier:
                    self._end_phase_d(key, reason)

    def _created(self, obj: Hypothesis | Acceptance) -> None:
        kind = "hypothesis" if isinstance(obj, Hypothesis) else "acceptance"
        identifier = obj.hypothesis_id if isinstance(obj, Hypothesis) else obj.acceptance_id
        tr = Transition(kind, identifier, None, obj.status.value, "created", self.t, self.now, self.tx)
        obj.state_transition_history.append(tr)
        self.transitions.append(tr)
        self.new_transitions.append(tr)

    def _new(self, kind: str, d: int, event: Occurrence, deadline: int,
             *, source_kind: str = "fact", parent: tuple[str, str] | None = None,
             origin: CountingOrigin = CountingOrigin.POST, **data) -> Hypothesis | None:
        existing = next((h for h in self.hypotheses.values() if h.status is H.CANDIDATE and
                         h.hypothesis_type == kind and h.direction == d and h.source_kind == source_kind and
                         (kind == "CLIMAX" or (h.reference_range_id, h.reference_range_version) ==
                          (event.reference_range_id, event.reference_range_version))), None)
        if existing:
            existing.supporting_evidence_ids.append(event.event_id)
            return None
        # A source can support a later interpretation after an earlier one ended.
        # Every episode must get a new ID; never overwrite a terminal object.
        identifier = f"H:{kind}:{d}:{event.event_id}:{self.t}"
        if identifier in self.hypotheses:
            raise ValueError("hypothesis_id_already_registered")
        h = Hypothesis(identifier, kind, d, source_kind, (event.event_id,), event.reference_range_id,
                       event.reference_range_version, self.t, deadline, self.features[-1].bar.bar_end, self.now,
                       event.frozen_S, event.frozen_R, event.frozen_epsilon or 0,
                       counting_origin=origin,
                       counting_origin_bar=self.t, data={**event.metrics, **data, "source": event})
        h.confirmation_conditions, h.invalidation_conditions = self._conditions(h)
        self.hypotheses[identifier] = h
        self.bind_parent(h, parent)
        self._created(h)
        return h

    def _conditions(self, h: Hypothesis) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
        """Save rule references, frozen prices and counting origins for audit."""
        p, kind = self.params, h.hypothesis_type
        common = {"direction": h.direction, "counting_origin": h.counting_origin.value,
                  "counting_origin_bar": h.counting_origin_bar, "frozen_S": h.frozen_S,
                  "frozen_R": h.frozen_R, "frozen_epsilon": h.frozen_epsilon}
        rules = {
            "BREAKOUT": (({"rule": "7.5.1", "basis": "outside_hold", "required_closes": p.breakout_hold_closes},
                          {"rule": "7.6/7.7", "basis": "dependent_pullback_response"}),
                         ({"rule": "7.5.1", "basis": "consecutive_inside_closes", "required_closes": p.breakout_fail_inside_closes},
                          {"rule": "7.5.1", "basis": "deep_return", "fraction": p.breakout_fail_deep_fraction})),
            "LPS": (({"rule": "7.6/7.7", "basis": "complete_pullback_and_response",
                      "minimum_bars": p.lps_min_pullback_bars, "max_effort": p.lps_max_pullback_effort,
                      "max_mean_ns": p.lps_max_pullback_ns, "min_retrace_atr": p.lps_min_retrace_atr,
                      "min_response_move_atr": p.lps_response_min_move_atr,
                      "confirm_step_units": p.confirm_step_units},),
                    ({"rule": "7.6/7.7", "basis": "candidate_reference_wick_loss", "reference": h.data.get("level")},
                     {"rule": "7.6/7.7", "basis": "confirmed_pullback_close_loss", "counting_origin": "post", "counting_origin_bar": None},
                     {"rule": "7.1", "basis": "parent_termination"})),
            "CLIMAX": (({"rule": "7.2", "basis": "AR_then_ST_response", "minimum_ar_atr": p.range_min_width_atr},),
                       ({"rule": "7.2/7.10", "basis": "climax_extreme_loss", "reference": h.data.get("extreme"),
                         "candidate_closes": 1, "confirmed_closes": 2},)),
            "ABSORPTION": (({"rule": "6.3", "basis": "SOS_SOW_or_price_reaction", "maximum_bars": p.absorption_response_max_bars},),
                           ({"rule": "6.3", "basis": "consecutive_boundary_close_loss", "required_closes": 2},)),
            "SPRING": (({"rule": "7.3", "basis": "test_response_or_permitted_SOS", "branch": h.data.get("branch")},),
                       ({"rule": "7.3", "basis": "pattern_extreme_loss", "reference": h.data.get("extreme")},
                        {"rule": "7.3", "basis": "consecutive_boundary_close_loss", "required_closes": p.spring_fail_consecutive_closes})),
            "UT": (({"rule": "7.4", "basis": "test_response_or_SOW"},),
                   ({"rule": "7.4", "basis": "pattern_extreme_loss", "reference": h.data.get("extreme")},
                    {"rule": "7.4", "basis": "consecutive_boundary_close_loss", "required_closes": p.spring_fail_consecutive_closes})),
            "UTAD": (({"rule": "7.8", "basis": "SOW_or_confirmed_LPSY"},),
                     ({"rule": "7.8", "basis": "pattern_extreme_loss", "reference": h.data.get("extreme")},
                      {"rule": "7.8", "basis": "consecutive_boundary_close_loss", "required_closes": p.breakout_fail_inside_closes})),
            "STRUCTURE": (({"rule": "8.4", "basis": "mature_range_direction_and_recent_trigger"},),
                          ({"rule": "7.10", "basis": "opposite_dominance", "required_closes": p.structural_opposite_persistence_bars},
                           {"rule": "7.10", "basis": "opposite_acceptance_or_stale_trigger"})),
        }
        confirmation, invalidation = rules[kind]
        return tuple(confirmation), tuple({**common, **rule} for rule in invalidation)

    def _failure(self, obj: Hypothesis | Acceptance) -> bool:
        d, c = obj.direction, self.features[-1].bar.close
        s, r, eps = obj.frozen_S, obj.frozen_R, obj.frozen_epsilon
        inside = outside(c, r if d == 1 else s, eps, -d)
        obj.failure_inside_streak = obj.failure_inside_streak + 1 if inside else 0
        threshold = dec(s) + dec(self.params.breakout_fail_deep_fraction) * (dec(r) - dec(s)) if d == 1 else dec(r) - dec(self.params.breakout_fail_deep_fraction) * (dec(r) - dec(s))
        deep = dec(c) < threshold if d == 1 else dec(c) > threshold
        return deep or obj.failure_inside_streak >= self.params.breakout_fail_inside_closes

    def invalidate_prices(self) -> None:
        if not self.features[-1].valid:
            return
        t, c = self.t, self.features[-1].bar.close
        for a in sorted(self.acceptances.values(), key=lambda a: a.acceptance_id):
            if a.status is H.CONFIRMED and t > a.accepted_bar and self._failure(a):
                self._change(a, H.INVALIDATED, "acceptance_price_failure")
                self._emit(E.BREAKOUT_ACCEPTANCE_INVALIDATED, acceptance=a)
                if a.source_hypothesis_id:
                    self._emit(E.FAILED_UP_BREAKOUT if a.direction == 1 else E.FAILED_DOWN_BREAKOUT,
                               owner=self.hypotheses[a.source_hypothesis_id], acceptance=a)
                if self._matching(a):
                    self.rng = replace(self.rng, state=R.ACTIVE)
        for h in sorted(self.hypotheses.values(), key=lambda h: h.hypothesis_id):
            if not self._active(h):
                continue
            if h.status is H.CONFIRMED and h.valid_through_bar is not None and t > h.valid_through_bar:
                self._change(h, H.EXPIRED, "current_validity_ended")
                continue
            if h.lifecycle_parent_ref and not self.parent_valid(h):
                parent = self.parent(h)
                if parent is None or parent.status in (H.INVALIDATED, H.EXPIRED):
                    self._change(h, parent.status if parent else H.EXPIRED, "parent_terminated")
                continue
            if t <= h.counting_origin_bar and h.counting_origin is CountingOrigin.POST:
                continue
            d, kind, eps = h.direction, h.hypothesis_type, h.frozen_epsilon
            failed = False
            if kind == "BREAKOUT" and h.status is H.CANDIDATE:
                failed = self._failure(h)
                if failed:
                    self._emit(E.FAILED_UP_BREAKOUT if d == 1 else E.FAILED_DOWN_BREAKOUT, owner=h)
                    if self._matching(h):
                        self.rng = replace(self.rng, state=R.ACTIVE)
            elif kind == "LPS":
                if h.status is H.CANDIDATE:
                    extreme = self.features[-1].bar.low if d == 1 else self.features[-1].bar.high
                    failed = outside(extreme, h.data["level"], eps, -d)
                else:
                    failed = outside(c, h.data["pullback_extreme"], eps, -d)
            elif kind in ("SPRING", "UT", "UTAD", "ABSORPTION", "CLIMAX"):
                level = h.frozen_S if d == 1 else h.frozen_R
                if kind == "CLIMAX":
                    level = h.data["extreme"]
                if level is not None:
                    breached = outside(c, level, eps, -d)
                    h.failure_inside_streak = h.failure_inside_streak + 1 if breached else 0
                if kind in ("SPRING", "UT", "UTAD"):
                    threshold = self.params.breakout_fail_inside_closes if kind == "UTAD" else self.params.spring_fail_consecutive_closes
                    failed = outside(c, h.data["extreme"], eps, -d) or h.failure_inside_streak >= threshold
                else:
                    failed = h.failure_inside_streak >= (1 if kind == "CLIMAX" and h.status is H.CANDIDATE else 2)
            if failed:
                self._change(h, H.INVALIDATED, "support_lost" if kind == "LPS" else "price_failure")

    def pause_gap(self) -> None:
        """Missing bars suspend confirmation opportunities and clear consecutive counts."""
        for h in self.hypotheses.values():
            if not self._active(h):
                continue
            if h.status is H.CANDIDATE:
                h.confirmation_deadline_bar += 1
            if h.valid_through_bar is not None:
                h.valid_through_bar += 1
            h.failure_inside_streak = 0
            h.pending_observation = None
            if h.hypothesis_type == "LPS" and h.status is H.CANDIDATE:
                h.data.update(b=None, search_from=len(self.features) + 1 if hasattr(self, "features") else h.created_bar + 1,
                              observed=False)
        for a in self.acceptances.values():
            a.failure_inside_streak = 0

    def _elapsed(self, start: int, end: int) -> int:
        return sum(f.valid for f in self.features[start + 1:end + 1])

    def parent(self, h: Hypothesis) -> Hypothesis | Acceptance | None:
        if h.lifecycle_parent_ref is None:
            return None
        kind, identifier = h.lifecycle_parent_ref
        return (self.hypotheses if kind == "hypothesis" else self.acceptances).get(identifier)

    def parent_valid(self, h: Hypothesis) -> bool:
        if not h.lifecycle_parent_ref:
            return True
        parent = self.parent(h)
        return parent is not None and self._active(parent) and parent.status_available_at <= self.now

    def bind_parent(self, h: Hypothesis, parent_ref: tuple[str, str] | None) -> None:
        """Register a dependency only if the target exists and the graph stays acyclic."""
        visited = {("hypothesis", h.hypothesis_id)}
        reference = parent_ref
        while reference is not None:
            if reference in visited:
                raise ValueError("lifecycle_dependency_cycle")
            visited.add(reference)
            kind, identifier = reference
            if kind not in ("hypothesis", "acceptance"):
                raise ValueError("lifecycle_parent_type_invalid")
            obj = (self.hypotheses if kind == "hypothesis" else self.acceptances).get(identifier)
            if obj is None:
                raise ValueError("lifecycle_parent_missing")
            if (obj.reference_range_id, obj.reference_range_version) != (h.reference_range_id, h.reference_range_version):
                raise ValueError("lifecycle_parent_range_mismatch")
            reference = obj.lifecycle_parent_ref if isinstance(obj, Hypothesis) else None
        h.lifecycle_parent_ref = parent_ref

    def validate_state(self) -> None:
        """Check §7.1 dependency and uniqueness contracts before publication."""
        for h in self.hypotheses.values():
            self.bind_parent(h, h.lifecycle_parent_ref)
            if h.status is H.CONFIRMED and h.lifecycle_parent_ref:
                parent = self.parent(h)
                if parent.status is not H.CONFIRMED:
                    raise ValueError("confirmed_child_requires_confirmed_parent")
        active_acceptances = set()
        breakout_keys = set()
        candidate_keys = set()
        for a in self.acceptances.values():
            if a.status is not H.CONFIRMED:
                continue
            key = (a.reference_range_id, a.reference_range_version)
            if key in active_acceptances:
                raise ValueError("duplicate_active_acceptance")
            active_acceptances.add(key)
        for h in self.hypotheses.values():
            key = (h.reference_range_id, h.reference_range_version, h.direction)
            if h.hypothesis_type == "BREAKOUT" and self._active(h):
                if key in breakout_keys:
                    raise ValueError("duplicate_active_breakout")
                breakout_keys.add(key)
            if h.status is H.CANDIDATE:
                candidate_key = (*key, h.hypothesis_type, h.source_kind)
                if candidate_key in candidate_keys:
                    raise ValueError("duplicate_candidate_source")
                candidate_keys.add(candidate_key)
        if self.rng and self.rng.state in (R.EXIT_UP_ACCEPTED, R.EXIT_DOWN_ACCEPTED):
            a = self.active_acceptance()
            expected = 1 if self.rng.state is R.EXIT_UP_ACCEPTED else -1
            if a is None or a.direction != expected:
                raise ValueError("accepted_range_requires_matching_acceptance")

    def ingest(self, facts: list[Occurrence], swings: tuple[SwingPoint, ...] = ()) -> None:
        self._facts = facts
        p, t = self.params, self.t
        for position, event in enumerate(facts):
            code, data = event.event_type, event.metrics
            if event.reference_range_id is not None and (self.rng is None or
                    (event.reference_range_id, event.reference_range_version) != (self.rng.range_id, self.rng.version)):
                # Old reclaim watches may finish factual observations, but cannot
                # create hypotheses against a retired foreground range.
                continue
            if code in (E.SC_FEATURE, E.SC_CLUSTER_FEATURE, E.BC_FEATURE, E.BC_CLUSTER_FEATURE):
                d = data["direction"]
                a = data["atr"]
                if a and data.get("ns_ref") is not None and data.get("effort") is not None:
                    h = self._new("CLIMAX", d, event, t + p.ar_window_max, origin=CountingOrigin.INCL)
                    if h:
                        h.frozen_epsilon = max(p.boundary_resolution_units * self.resolution, p.boundary_atr_fraction * a)
                        h.confirmation_conditions, h.invalidation_conditions = self._conditions(h)
            elif code in (E.SPRING_LOW_VOLUME, E.SPRING_HIGH_VOLUME, E.SPRING_OTHER, E.SHAKEOUT_RECLAIM):
                self._new("SPRING", 1, event, t + p.test_window_max + p.test_response_max_bars,
                          branch=code.value, last_evidence=t)
            elif code in (E.UT_FEATURE, E.UPTHRUST_DEEP_REJECTION):
                self._new("UT", -1, event, t + p.test_window_max + p.test_response_max_bars, last_evidence=t)
                onset = event.onset_snapshot
                if onset and onset.range_age >= p.mature_range_age and onset.dominance == "supply":
                    self._new("UTAD", -1, event, t + p.utad_response_window_max, last_evidence=t)
            elif code in (E.BREAKOUT_UP, E.BREAKOUT_DOWN) and self.rng is not None:
                d = 1 if code is E.BREAKOUT_UP else -1
                if self.active_acceptance() is not None and self.active_acceptance().direction != d:
                    a = self.active_acceptance()
                    self._change(a, H.INVALIDATED, "opposite_breakout")
                for old in list(self.hypotheses.values()):
                    if old.hypothesis_type == "BREAKOUT" and self._matching(old) and old.direction != d and self._active(old):
                        self._change(old, H.INVALIDATED, "opposite_breakout")
                existing = next((h for h in self.hypotheses.values() if self._matching(h) and h.direction == d and h.hypothesis_type == "BREAKOUT" and self._active(h)), None)
                a = self.active_acceptance()
                if not existing and not a:
                    h = self._new("BREAKOUT", d, event, t + p.breakout_validity_window,
                                  strength=event.strength)
                    self._new_breakouts[d] = h
                    self.rng = replace(self.rng, state=R.EXIT_UP_CANDIDATE if d == 1 else R.EXIT_DOWN_CANDIDATE)
                elif existing:
                    existing.supporting_evidence_ids.append(event.event_id)
                    facts[position] = replace(event, acceptance_id=a.acceptance_id if a else None,
                                              evidence_owner_id=existing.hypothesis_id)
                elif a:
                    facts[position] = replace(event, acceptance_id=a.acceptance_id)
                    a.source_event_ids = tuple(dict.fromkeys((*a.source_event_ids, event.event_id)))
            elif code in (E.EFFORT_RESULT_NARROW_BAR, E.EFFORT_RESULT_LOW_PROGRESS) and self.rng:
                f = self.features[-1]
                for d in (1, -1):
                    touches = self.rng.touches_support(f.bar.high, f.bar.low) if d == 1 else self.rng.touches_resistance(f.bar.high, f.bar.low)
                    if touches:
                        self._new("ABSORPTION", d, event, t + p.absorption_response_max_bars,
                                  origin=CountingOrigin.INCL)
        # One source per side per close, in the declared priority order.
        for d in (1, -1):
            if self.rng is None:
                break
            parent = self._new_breakouts.get(d)
            internal = next((e for e in facts if e.event_type is (E.INTERNAL_JOC if d == 1 else E.INTERNAL_FTI)), None)
            strong = next((e for e in facts if e.event_type is (E.SOS if d == 1 else E.SOW)), None)
            if parent:
                event, source, level = parent.data["source"], "external_breakout", self.rng.R if d == 1 else self.rng.S
            elif internal:
                event, source, level = internal, "internal_break", internal.frozen_internal_level
            elif strong and not outside(self.features[-1].bar.close, self.rng.R if d == 1 else self.rng.S, self.rng.epsilon, d):
                event, source = strong, "internal_sos"
                eligible = [s for s in swings if s.usable_from_index <= t and s.kind is (SwingKind.LOW if d == 1 else SwingKind.HIGH) and
                            (s.price >= self.rng.S if d == 1 else s.price <= self.rng.R)]
                level = max(eligible, key=lambda s: s.confirmed_at_index).price if eligible else self.rng.S if d == 1 else self.rng.R
            else:
                continue
            preceding = any(e.event_type is (E.SOS if d == 1 else E.SOW) and
                            (e.reference_range_id, e.reference_range_version) == (self.rng.range_id, self.rng.version) and
                            1 <= t - e.pattern_end <= p.bias_signal_recent_bars for e in self.events)
            related_sos = source != "external_breakout" or event.strength in ("joc", "fti") or preceding
            self._new("LPS", d, event, t + p.lps_window_max, source_kind=source,
                      parent=("hypothesis", parent.hypothesis_id) if parent else None,
                      level=level, search_from=t + 1, b=None, observed=False,
                      related_sos=related_sos, source_hypothesis_id=parent.hypothesis_id if parent else None)
        # INCL candidates are checked on their creation bar exactly once.
        c = self.features[-1].bar.close
        for h in self.hypotheses.values():
            if h.created_bar != t or h.counting_origin is not CountingOrigin.INCL or h.status is not H.CANDIDATE:
                continue
            level = h.data["extreme"] if h.hypothesis_type == "CLIMAX" else h.frozen_S if h.direction == 1 else h.frozen_R
            breached = outside(c, level, h.frozen_epsilon, -h.direction)
            h.failure_inside_streak = int(breached)
            if h.hypothesis_type == "CLIMAX" and breached:
                self._change(h, H.INVALIDATED, "price_failure")
        self.events.extend(facts)

    def active_acceptance(self) -> Acceptance | None:
        return next((a for a in self.acceptances.values() if a.status is H.CONFIRMED and self._matching(a)), None)

    def _accept(self, d: int, basis: str, source: Hypothesis | None = None) -> Acceptance:
        a = self.active_acceptance()
        if a:
            if a.direction != d:
                raise ValueError("opposite acceptance must terminate before creation")
            if basis not in a.confirmation_bases:
                a.confirmation_bases.append(basis)
            if source and source.status is H.CANDIDATE:
                self._change(source, H.CONFIRMED, basis)
                self.bind_parent(source, ("acceptance", a.acceptance_id))
            return a
        rng = self.rng
        if rng is None or not outside(self.features[-1].bar.close, rng.R if d == 1 else rng.S, rng.epsilon, d):
            raise ValueError("acceptance requires an exterior close")
        prior = tuple(sorted(h.hypothesis_id for h in self.hypotheses.values() if h.hypothesis_type == "BREAKOUT" and h.direction == d and self._matching(h) and h.status is H.EXPIRED))
        identifier = f"A:{rng.range_id}:{rng.version}:{d}:{self.t}"
        mode = AcceptanceMode.LATE_HOLD if source is None else AcceptanceMode.HOLD if basis == "hold" else AcceptanceMode.PULLBACK
        a = Acceptance(identifier, d, rng.range_id, rng.version, self.t, self.features[-1].bar.bar_end,
                       self.now, self.now, mode, source.hypothesis_id if source else None,
                       source.source_event_ids if source else (), prior, rng.S, rng.R, rng.epsilon, basis, [basis])
        a.counting_origin_bar = self.t
        self.acceptances[identifier] = a
        self._created(a)
        if source:
            self._change(source, H.CONFIRMED, basis)
            self.bind_parent(source, ("acceptance", identifier))
        self.rng = replace(rng, state=R.EXIT_UP_ACCEPTED if d == 1 else R.EXIT_DOWN_ACCEPTED)
        # Opposite acceptance invalidates structural owners before child confirmations.
        for h in list(self.hypotheses.values()):
            matching = self._matching(h) or h.data.get("path_a_range_id") == rng.range_id
            if h.direction == -d and matching and h.hypothesis_type in ("CLIMAX", "STRUCTURE"):
                self._change(h, H.INVALIDATED, "opposite_acceptance")
        self._emit(E.BREAKOUT_HOLD_CONFIRMED, owner=source, acceptance=a,
                   metrics={"late_acceptance": source is None, "primary_basis": basis})
        return a

    def confirm(self) -> None:
        if not self.features[-1].valid:
            return
        p, t = self.params, self.t
        for h in self.hypotheses.values():
            if h.hypothesis_type == "BREAKOUT" and h.status in (H.CANDIDATE, H.CONFIRMED) and h.data.get("strength") == "joc" and t > h.created_bar:
                source_bar = self.features[h.created_bar].bar
                body = source_bar.close - source_bar.open
                lows = [g.bar.low for g in self.features[h.created_bar + 1:t + 1] if g.valid]
                if body > 0 and lows:
                    ratio = max(0, source_bar.close - min(lows)) / body
                    h.data["shallow_body_retrace"] = ratio <= p.body_retrace_fraction
                    h.data["body_retrace_ratio"] = ratio
                else:
                    h.data["shallow_body_retrace"] = None
        if self.rng:
            for d in (1, -1):
                streak = self.rng.outside_up_streak if d == 1 else self.rng.outside_down_streak
                parent = next((h for h in self.hypotheses.values() if h.hypothesis_type == "BREAKOUT" and h.direction == d and self._matching(h) and h.status is H.CANDIDATE), None)
                if parent and t <= parent.confirmation_deadline_bar and streak >= p.breakout_hold_closes:
                    self._accept(d, "hold", parent)
                elif parent is None and self.active_acceptance() is None and streak >= p.breakout_hold_closes and self.rng.state is (R.EXIT_UP_CANDIDATE if d == 1 else R.EXIT_DOWN_CANDIDATE):
                    prior = any(h.hypothesis_type == "BREAKOUT" and h.direction == d and self._matching(h) and h.status is H.EXPIRED and h.confirmation_deadline_bar < t for h in self.hypotheses.values())
                    if prior:
                        self._accept(d, "hold")
        ordered = sorted(self.hypotheses.values(), key=lambda h: h.hypothesis_id)
        for h in ordered:
            if self._active(h) and h.hypothesis_type == "CLIMAX":
                if h.status is H.CANDIDATE:
                    self._climax(h)
                elif "ar_bar" in h.data:
                    self._test(h)
            elif h.status is H.CANDIDATE and h.hypothesis_type == "ABSORPTION":
                f = self.features[-1]
                d = h.direction
                strong = any(e.event_type is (E.SOS if d == 1 else E.SOW) for e in self._facts)
                reaction = f.net_move.value is not None and d * f.net_move.value >= p.strong_bar_min_move_atr and d * (f.bar.close - f.bar.open) > 0 and f.cp.value is not None and (f.cp.value >= p.sos_min_cp if d == 1 else f.cp.value <= p.sow_max_cp)
                if 1 <= self._elapsed(h.created_bar, t) <= p.absorption_response_max_bars and (strong or reaction):
                    trigger = next((e.event_id for e in self._facts if e.event_type is (E.SOS if d == 1 else E.SOW)), f"reaction:{t}")
                    self._confirm_sequence(h, E.SUPPORT_RESPONSE_SEQUENCE if d == 1 else E.RESISTANCE_RESPONSE_SEQUENCE, trigger)
                    h.valid_through_bar = t + p.absorption_current_evidence_max_age
        for h in ordered:
            if self._active(h) and h.hypothesis_type in ("SPRING", "UT"):
                self._test(h)
        successful = []
        for h in sorted(self.hypotheses.values(), key=lambda h: ({"external_breakout": 0, "internal_break": 1, "internal_sos": 2}.get(h.source_kind, 3), h.hypothesis_id)):
            if h.status is H.CANDIDATE and h.hypothesis_type == "LPS" and self.parent_valid(h):
                selected = not any(old.direction == h.direction and old.reference_range_id == h.reference_range_id and old.reference_range_version == h.reference_range_version for old in successful)
                if self._lps(h, selected=selected) and selected:
                    successful.append(h)
        for h in ordered:
            if h.hypothesis_type not in ("SPRING", "UT", "UTAD") or not self._active(h):
                continue
            d = h.direction
            strong = next((e for e in self._facts if e.event_type is (E.SOS if d == 1 else E.SOW)), None)
            if h.status is H.CONFIRMED:
                if strong and self._matching(h) and h.hypothesis_type != "UTAD":
                    h.data["last_evidence"] = t
                    h.valid_through_bar = t + p.bias_sequence_recent_bars
                continue
            elapsed = self._elapsed(h.created_bar, t)
            allowed = h.hypothesis_type == "UT" or h.hypothesis_type == "UTAD" or h.data.get("branch") in (E.SPRING_LOW_VOLUME.value, E.SPRING_OTHER.value)
            lower = p.utad_response_window_min if h.hypothesis_type == "UTAD" else p.spring_sos_window_min
            upper = p.utad_response_window_max if h.hypothesis_type == "UTAD" else p.spring_sos_window_max
            lpsy = next((e for e in self.new_events if e.event_type is E.LPSY and
                         (e.reference_range_id, e.reference_range_version) == (h.reference_range_id, h.reference_range_version)
                         and self.hypotheses[e.evidence_owner_id].status is H.CONFIRMED), None) if h.hypothesis_type == "UTAD" else None
            if allowed and lower <= elapsed <= upper and (strong or lpsy):
                trigger = (strong or lpsy).event_id
                code = E.UTAD_WEAKNESS_SEQUENCE if h.hypothesis_type == "UTAD" else E.UT_SOW_SEQUENCE if d == -1 else E.SPRING_SOS_SEQUENCE
                self._confirm_sequence(h, code, trigger)
                h.valid_through_bar = t + p.bias_sequence_recent_bars

    def _climax(self, h: Hypothesis) -> None:
        p, t, d = self.params, self.t, h.direction
        t0, a = h.created_bar, h.data["atr"]
        if "ar_bar" not in h.data:
            if not p.ar_window_min <= self._elapsed(t0, t) <= p.ar_window_max:
                return
            span = self.features[t0 + 1:t + 1]
            if not all(f.valid for f in span):
                return
            j = max(range(len(span)), key=lambda i: span[i].bar.high) if d == 1 else min(range(len(span)), key=lambda i: span[i].bar.low)
            extreme = span[j].bar.high if d == 1 else span[j].bar.low
            closes = [f.bar.close for f in self.features[t - p.ar_reversal_close_steps:t + 1]]
            if d * (extreme - h.data["extreme"]) < p.range_min_width_atr * a or not all(d * (b - a_) < 0 for a_, b in zip(closes, closes[1:])) or d * (extreme - self.features[t].bar.close) < p.ar_reversal_atr * a:
                return
            h.data.update(ar_bar=t, ar_extreme=extreme)
            h.frozen_S = h.data["extreme"] if d == 1 else extreme
            h.frozen_R = extreme if d == 1 else h.data["extreme"]
            h.confirmation_conditions, h.invalidation_conditions = self._conditions(h)
            h.confirmation_deadline_bar = t + p.st_window_max + p.st_response_max_bars
            # AR is a supporting record: §6.8 defines no AR event code.
            h.data["ar_record"] = {"type": "AR_RALLY" if d == 1 else "AR_REACTION", "confirmed_bar": t,
                                   "available_at": self.now, "extreme_bar": t0 + 1 + j}
            if h.frozen_R - h.frozen_S > p.range_max_width_atr * a:
                self._change(h, H.EXPIRED, "width_too_large")
            return
        self._test(h)

    def _test_shape(self, h: Hypothesis) -> bool:
        p, t, d, f = self.params, self.t, h.direction, self.features[-1]
        extreme = f.bar.low if d == 1 else f.bar.high
        level = h.frozen_S if d == 1 else h.frozen_R
        eps = h.frozen_epsilon
        if f.bar.volume <= 0 or f.ns.value is None or f.rvol.value is None:
            return False
        if h.hypothesis_type == "CLIMAX":
            elapsed = self._elapsed(h.data["ar_bar"], t)
            distance = d * (dec(extreme) - dec(level))
            return p.st_window_min <= elapsed <= p.st_window_max and -dec(eps) <= distance <= dec(p.st_near_support_atr * h.data["atr"]) and not outside(f.bar.close, level, eps, -d) and f.rvol.value <= p.st_max_effort_vs_climax * h.data["effort"] and f.ns.value <= p.st_max_ns_vs_climax * h.data["ns_ref"]
        a = f.atr_baseline.value
        elapsed = self._elapsed(h.created_bar, t)
        relative = h.hypothesis_type == "UT" and h.data["max_rvol"] >= p.spring_low_volume_max_rvol or h.hypothesis_type == "SPRING" and h.data["branch"] != E.SPRING_LOW_VOLUME.value
        return a is not None and p.test_window_min <= elapsed <= p.test_window_max and d * (dec(extreme) - dec(h.data["extreme"])) >= dec(eps) and -dec(eps) <= d * (dec(extreme) - dec(level)) <= dec(p.test_near_support_atr * a) and f.rvol.value <= p.test_max_rvol and f.ns.value <= p.test_max_ns and (not relative or f.rvol.value <= p.test_max_effort_vs_spring * h.data["effort"])

    def _response(self, h: Hypothesis, q: int) -> bool:
        t, d, f = self.t, h.direction, self.features[-1]
        level = h.frozen_S if d == 1 else h.frozen_R
        if q == 0 or not all(f.valid for f in self.features[q - 1:t + 1]):
            return False
        if q == t:
            prior = self.features[q - 1].bar.high if d == 1 else self.features[q - 1].bar.low
            position = d * (dec(f.bar.close) - dec(level)) >= dec(h.frozen_epsilon)
            return d * (f.bar.close - f.bar.open) > 0 and position and at_least_step(f.bar.close, prior, self.resolution, self.params.confirm_step_units, d)
        window = self.features[q:t]
        reference = max(g.bar.high for g in window) if d == 1 else min(g.bar.low for g in window)
        return outside(f.bar.close, level, h.frozen_epsilon, d) and at_least_step(f.bar.close, reference, self.resolution, self.params.confirm_step_units, d)

    def _test(self, h: Hypothesis) -> None:
        t, p = self.t, self.params
        if not self.parent_valid(h) or h.status is H.CANDIDATE and t > h.confirmation_deadline_bar:
            return
        if h.status is H.CONFIRMED:
            if self._test_shape(h):
                code = E.ST_OBSERVED if h.hypothesis_type == "CLIMAX" else E.TEST_OBSERVED if h.direction == 1 else E.UT_TEST_OBSERVED
                added = self._emit(code, owner=h, pattern_start=t,
                                   metrics={"q": t, "not_adopted": "closed_loop_already_confirmed"})
                h.supporting_evidence_ids.append(added.event_id)
                if h.hypothesis_type in ("SPRING", "UT"):
                    h.data["last_evidence"] = t
                    h.valid_through_bar = t + self.params.bias_sequence_recent_bars
            return
        old = h.pending_observation
        confirmed = False
        if old and t <= old["response_deadline_bar"] and self._response(h, old["q"]):
            self._test_confirm(h, old["q"])
            confirmed = True
        shape = self._test_shape(h)
        if shape:
            code = E.ST_OBSERVED if h.hypothesis_type == "CLIMAX" else E.TEST_OBSERVED if h.direction == 1 else E.UT_TEST_OBSERVED
            self._emit(code, owner=h, pattern_start=t,
                       metrics={"q": t, "not_adopted": "closed_loop_already_confirmed" if confirmed else None})
        if confirmed:
            return
        if shape:
            timeout = p.st_response_max_bars if h.hypothesis_type == "CLIMAX" else p.test_response_max_bars
            h.pending_observation = {"q": t, "response_deadline_bar": min(t + timeout, h.confirmation_deadline_bar)}
            if self._response(h, t):
                self._test_confirm(h, t)
        elif old and t >= old["response_deadline_bar"]:
            h.pending_observation = None

    def _test_confirm(self, h: Hypothesis, q: int) -> None:
        trigger = f"test_response:{h.hypothesis_id}:{q}:{self.t}"
        if h.hypothesis_type == "CLIMAX":
            self._confirm_sequence(h, E.SC_STOPPING_SEQUENCE if h.direction == 1 else E.BC_STOPPING_SEQUENCE, trigger)
            candidate = establish_path_a(h.data["source"].pattern_start, self.t, h.frozen_S, h.frozen_R,
                                        h.data["atr"], self.params, self.resolution, epsilon=h.frozen_epsilon)
            if not isinstance(candidate, RangeRejection):
                self.path_a_ranges.append(candidate)
                h.data["path_a_range_id"] = candidate.range_id
        else:
            self._confirm_sequence(h, E.SPRING_TEST_CONFIRMED if h.direction == 1 else E.UT_TEST_CONFIRMED, trigger)
            if h.direction == 1:
                self._emit(E.TEST_RESPONSE_CONFIRMED, owner=h, trigger=trigger, metrics={"same_bar": q == self.t})
            h.valid_through_bar = self.t + self.params.bias_sequence_recent_bars
        h.data["test_confirmed_q"] = q
        h.data["same_bar"] = q == self.t
        h.pending_observation = None

    def _lps(self, h: Hypothesis, *, selected: bool = True) -> bool:
        t, p, d, f = self.t, self.params, h.direction, self.features[-1]
        if t <= h.created_bar or t > h.confirmation_deadline_bar:
            return False
        b = h.data["b"]
        if b is None:
            if t >= h.data["search_from"] and d * (f.bar.close - self.features[t - 1].bar.close) < 0:
                h.data["b"] = t
            return False
        deadline = min(b + p.lps_max_pullback_bars, h.confirmation_deadline_bar)
        if t > deadline:
            h.data.update(b=None, search_from=t, observed=False)
            return False
        segment = self.features[b:t]
        before = self.features[h.created_bar:b]
        q = min(range(b, t), key=lambda i: self.features[i].bar.low) if d == 1 else max(range(b, t), key=lambda i: self.features[i].bar.high)
        extreme = self.features[q].bar.low if d == 1 else self.features[q].bar.high
        pre_extreme = max(g.bar.high for g in before) if d == 1 else min(g.bar.low for g in before)
        a = self.features[b].atr_baseline.value
        bvs = [g.bv_baseline.value for g in segment]
        effort = sum(g.bar.volume for g in segment) / sum(bvs) if all(v is not None and v > 0 for v in bvs) else None
        ns = statistics.mean(g.ns.value for g in segment) if all(g.ns.value is not None for g in segment) else None
        evidence = t - b >= p.lps_min_pullback_bars and effort is not None and effort <= p.lps_max_pullback_effort and ns is not None and ns <= p.lps_max_pullback_ns and a is not None and a > 0 and d * (pre_extreme - extreme) / a >= p.lps_min_retrace_atr
        if evidence and not h.data["observed"]:
            self._emit(E.LPS_PULLBACK_OBSERVED if d == 1 else E.LPSY_RALLY_OBSERVED, owner=h, pattern_start=b,
                       metrics={"b": b, "q": q, "pullback_bars": t - b})
            h.data["observed"] = True
        prior = self.features[t - 1].bar.high if d == 1 else self.features[t - 1].bar.low
        reaction = at_least_step(f.bar.close, prior, self.resolution, p.confirm_step_units, d) and outside(f.bar.close, h.data["level"], h.frozen_epsilon, d) and f.net_move.value is not None and d * f.net_move.value >= p.lps_response_min_move_atr
        if evidence and reaction and self.parent_valid(h):
            if not selected:
                subtype = "LPS_INTERNAL" if d == 1 else "LPSY_INTERNAL"
                if h.source_kind == "external_breakout":
                    subtype = ("LPS_AT_CREEK" if dec(extreme) <= dec(h.frozen_R) + dec(h.frozen_epsilon) else "LPS_ABOVE_CREEK") if d == 1 else ("LPSY_AT_ICE" if dec(extreme) >= dec(h.frozen_S) - dec(h.frozen_epsilon) else "LPSY_BELOW_ICE")
                self._emit(E.LPS if d == 1 else E.LPSY, owner=h, sub_type=subtype,
                           metrics={"q": q, "b": b, "pullback_extreme": extreme,
                                    "pre_pullback_extreme": pre_extreme, "not_selected_same_bar": True})
                self._change(h, H.EXPIRED, "not_selected_same_bar")
                return True
            acceptance = None
            if h.source_kind == "external_breakout":
                source = self.hypotheses[h.data["source_hypothesis_id"]]
                if not self._active(source) or not self._matching(h):
                    return False
                acceptance = self._accept(d, "pullback", source)
                self.bind_parent(h, ("acceptance", acceptance.acceptance_id))
            if h.source_kind != "external_breakout":
                subtype = "LPS_INTERNAL" if d == 1 else "LPSY_INTERNAL"
            elif d == 1:
                subtype = "LPS_AT_CREEK" if dec(extreme) <= dec(h.frozen_R) + dec(h.frozen_epsilon) else "LPS_ABOVE_CREEK"
            else:
                subtype = "LPSY_AT_ICE" if dec(extreme) >= dec(h.frozen_S) - dec(h.frozen_epsilon) else "LPSY_BELOW_ICE"
            rvols = [g.rvol.value for g in segment]
            prior_bar = self.features[t - 1].bar
            strict = h.source_kind == "external_breakout" and subtype in ("LPS_AT_CREEK", "LPSY_AT_ICE") and t - b >= p.strict_backup_min_pullback_bars and all(v is not None for v in rvols) and statistics.mean(rvols) < p.strict_backup_max_mean_rvol and ns <= p.strict_backup_max_mean_ns and ols_slope(rvols) < 0 and d * (prior_bar.close - prior_bar.open) < 0 and d * (f.bar.close - f.bar.open) > 0 and d * (f.bar.open - prior_bar.close) <= 0 and d * (f.bar.close - prior_bar.open) >= 0 and f.rvol.value is not None and f.rvol.value >= p.backup_turn_min_rvol
            h.data.update(q=q, b=b, pullback_extreme=extreme, pre_pullback_extreme=pre_extreme,
                          sub_type=subtype, pullback_effort=effort, mean_ns=ns, strict_v1=strict)
            h.invalidation_conditions = tuple({**rule, "counting_origin_bar": t,
                                               "reference": extreme} if rule["basis"] == "confirmed_pullback_close_loss" else rule
                                              for rule in h.invalidation_conditions)
            self._change(h, H.CONFIRMED, "pullback_response")
            h.valid_through_bar = t + p.lps_current_max_age
            self._emit(E.LPS if d == 1 else E.LPSY, owner=h, acceptance=acceptance,
                       sub_type=subtype, metrics={"q": q, "b": b, "pullback_extreme": extreme, "pre_pullback_extreme": pre_extreme, "strict_v1": strict})
            key = (h.reference_range_id, h.reference_range_version, d)
            old_id = self.current_lps.get(key)
            old = self.hypotheses.get(old_id)
            if old and old.status is H.CONFIRMED:
                phase = self.phase_d.get(key)
                if phase and h.data["related_sos"]:
                    phase["lps_id"] = h.hypothesis_id
                    phase["handoffs"].append({"bar": t, "from": old_id, "to": h.hypothesis_id})
                elif phase:
                    self._end_phase_d(key, "replacement_source_not_qualified")
                self._change(old, H.EXPIRED, "replaced")
            self.current_lps[key] = h.hypothesis_id
            if h.data["related_sos"] and key not in self.phase_d:
                self.phase_d[key] = {"phase_id": f"PD:{h.hypothesis_id}", "lps_id": h.hypothesis_id,
                                     "status": "candidate", "created_bar": t, "available_at": self.now,
                                     "direction": d, "handoffs": []}
            return True
        # Confirmation precedes source-close extreme reset (§7.6 B10).
        closes = [g.bar.close for g in self.features[h.created_bar:t]]
        new_extreme = f.bar.close > max(closes) if d == 1 else f.bar.close < min(closes)
        if new_extreme or t == deadline:
            h.data.update(b=None, search_from=t + 1, observed=False)
        return False

    def _end_phase_d(self, key: tuple, reason: str) -> None:
        phase = self.phase_d.pop(key, None)
        if phase:
            self.phase_history.append({**phase, "status": "expired", "ended_bar": self.t, "reason": reason})

    def expire_candidates(self) -> None:
        for h in sorted(self.hypotheses.values(), key=lambda h: h.hypothesis_id):
            if h.status is H.CANDIDATE and self.t >= h.confirmation_deadline_bar:
                self._change(h, H.EXPIRED, "confirmation_deadline")
                if h.hypothesis_type == "BREAKOUT" and self._matching(h):
                    c = self.features[-1].bar.close
                    if not outside(c, self.rng.R, self.rng.epsilon, 1) and not outside(c, self.rng.S, self.rng.epsilon, -1):
                        self.rng = replace(self.rng, state=R.ACTIVE)
        if self.rng and self.active_acceptance() is None and self.rng.state in (R.EXIT_UP_CANDIDATE, R.EXIT_DOWN_CANDIDATE):
            active = any(h.hypothesis_type == "BREAKOUT" and h.status is H.CANDIDATE and self._matching(h) for h in self.hypotheses.values())
            c = self.features[-1].bar.close
            if not active and not outside(c, self.rng.R, self.rng.epsilon, 1) and not outside(c, self.rng.S, self.rng.epsilon, -1):
                self.rng = replace(self.rng, state=R.ACTIVE)

    def close_range(self, rng: TradingRange) -> None:
        for a in self.acceptances.values():
            if (a.reference_range_id, a.reference_range_version) == (rng.range_id, rng.version):
                self._change(a, H.EXPIRED, "range_closed")
        for h in self.hypotheses.values():
            if (h.reference_range_id, h.reference_range_version) == (rng.range_id, rng.version):
                self._change(h, H.EXPIRED, "range_closed")
            elif h.data.get("path_a_range_id") == rng.range_id:
                self._change(h, H.EXPIRED, "range_closed")

    def _confirm_sequence(self, h: Hypothesis, code: E, trigger: str) -> None:
        if h.status is not H.CANDIDATE or not self.parent_valid(h):
            return
        self._change(h, H.CONFIRMED, code.value)
        self._emit(code, owner=h, trigger=trigger)

    def _emit(self, code: E, *, owner: Hypothesis | None = None,
              acceptance: Acceptance | None = None, trigger: str | None = None,
              sub_type: str | None = None, metrics: dict | None = None,
              pattern_start: int | None = None) -> Occurrence:
        source = owner.data.get("source") if owner else None
        identifier = f"E:{code.value}:{owner.hypothesis_id if owner else acceptance.acceptance_id}:{self.t}"
        f = self.features[-1]
        start = pattern_start if pattern_start is not None else owner.created_bar if owner else self.t
        event_time = self.features[start].bar.bar_end if pattern_start is not None else source.event_time if source else f.bar.bar_end
        event = Occurrence(identifier, code, start, self.t,
                           event_time, f.bar.bar_end, self.now,
                           onset_snapshot=source.onset_snapshot if source else None,
                           onset_snapshot_id=source.onset_snapshot_id if source else None,
                           reference_range_id=owner.reference_range_id if owner else acceptance.reference_range_id,
                           reference_range_version=owner.reference_range_version if owner else acceptance.reference_range_version,
                           frozen_S=owner.frozen_S if owner else acceptance.frozen_S,
                           frozen_R=owner.frozen_R if owner else acceptance.frozen_R,
                           frozen_epsilon=owner.frozen_epsilon if owner else acceptance.frozen_epsilon,
                           frozen_internal_level=owner.data.get("level") if owner and owner.source_kind != "external_breakout" and owner.hypothesis_type == "LPS" else source.frozen_internal_level if source else None,
                           sub_type=sub_type, observed_metrics=tuple((metrics or {}).items()),
                           quality_flags=tuple(sorted(set().union(*(g.bar.quality_flags for g in self.features[start:self.t + 1])))),
                           acceptance_id=acceptance.acceptance_id if acceptance else None,
                           evidence_owner_id=owner.hypothesis_id if owner else None,
                           confirmation_trigger_id=trigger,
                           primitive_event_ids=(*owner.source_event_ids, trigger) if owner and trigger else owner.source_event_ids if owner else ())
        self.events.append(event)
        self.new_events.append(event)
        if trigger and owner and code in (E.SUPPORT_RESPONSE_SEQUENCE, E.RESISTANCE_RESPONSE_SEQUENCE,
                                         E.SPRING_TEST_CONFIRMED, E.SPRING_SOS_SEQUENCE, E.UT_TEST_CONFIRMED,
                                         E.UT_SOW_SEQUENCE, E.UTAD_WEAKNESS_SEQUENCE):
            self.sequences.append(SequenceRecord(identifier, code, owner.hypothesis_id, trigger,
                                                  event.primitive_event_ids, owner.reference_range_id,
                                                  owner.reference_range_version, self.t, self.now,
                                                  H.CONFIRMED, True))
        return event

    def current_sequences(self) -> tuple[SequenceRecord, ...]:
        return tuple(replace(s, owner_status=self.hypotheses[s.evidence_owner_id].status,
                             owner_current_valid=self.hypotheses[s.evidence_owner_id].status is H.CONFIRMED)
                     for s in self.sequences)

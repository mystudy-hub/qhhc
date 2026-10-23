"""Review regressions for structure triggers, phase references and retirement."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from cta_lab.wyckoff_v4.engine import WyckoffV4Engine
from cta_lab.wyckoff_v4.evidence import Dominance
from cta_lab.wyckoff_v4.range import RangeBar, RangeRejection, establish_path_b
from cta_lab.wyckoff_v4.types import EventCode as E, HypothesisStatus as H, RangeState as R, TrendState
from .scenarios import bar, contract, event, feature, rng


def controlled_engine(t=0):
    engine = WyckoffV4Engine(contract())
    engine.front_range = rng()
    engine._features = [feature(i, 95) for i in range(t + 1)]
    engine.lifecycle.begin(engine._features, engine.front_range, engine._features[-1].bar.bar_end, f"T:{t}")
    return engine


def structures(engine, dominance, *, entry=True):
    engine._structures(SimpleNamespace(accumulation_candidate=entry, distribution_candidate=False),
                       SimpleNamespace(dominance=dominance), engine._features[-1].bar.bar_end)


def advance_controlled(engine, t):
    engine._features.append(feature(t, 95))
    engine.lifecycle.begin(engine._features, engine.front_range, engine._features[-1].bar.bar_end, f"T:{t}")
    engine.lifecycle.expire_candidates()


def test_structure_survives_generic_deadline_and_same_bar_new_trigger():
    engine = controlled_engine()
    engine.lifecycle.events.append(event(E.SOS, 0, engine.front_range))
    structures(engine, Dominance.DEMAND)
    original = next(h for h in engine.lifecycle.hypotheses.values() if h.hypothesis_type == "STRUCTURE")
    assert original.confirmation_deadline_bar is None
    for t in range(1, 11):
        advance_controlled(engine, t)
        if t == 10:
            engine.lifecycle.events.append(event(E.SOS, t, engine.front_range))
        structures(engine, Dominance.DEMAND)
    assert original.status is H.CANDIDATE
    assert original.data["last_trigger"] == 10
    assert len([h for h in engine.lifecycle.hypotheses.values() if h.hypothesis_type == "STRUCTURE"]) == 1


@pytest.mark.parametrize("dominance, expected", [(Dominance.DEMAND, H.CANDIDATE),
                                                  (Dominance.NOT_EVALUABLE, H.CANDIDATE),
                                                  (Dominance.NEUTRAL, H.EXPIRED)])
def test_structure_stale_exit_requires_known_loss_of_direction(dominance, expected):
    engine = controlled_engine()
    engine.lifecycle.events.append(event(E.SOS, 0, engine.front_range))
    structures(engine, Dominance.DEMAND)
    owner = next(iter(engine.lifecycle.hypotheses.values()))
    for t in range(1, 11):
        advance_controlled(engine, t)
        structures(engine, dominance, entry=False)
    assert owner.status is expected
    if expected is H.EXPIRED:
        assert owner.state_transition_history[-1].reason == "signal_stale"


def test_same_bar_signal_refresh_precedes_stale_exit_even_without_entry():
    engine = controlled_engine()
    engine.lifecycle.events.append(event(E.SOS, 0, engine.front_range))
    structures(engine, Dominance.DEMAND)
    owner = next(iter(engine.lifecycle.hypotheses.values()))
    for t in range(1, 11):
        advance_controlled(engine, t)
        if t == 10:
            engine.lifecycle.events.append(event(E.SOS, t, engine.front_range))
        structures(engine, Dominance.NEUTRAL, entry=False)
    assert owner.status is H.CANDIDATE and owner.data["last_trigger"] == 10


@pytest.mark.parametrize("invalid_kind", ["invalidated", "expired", "missing_owner", "range_version", "owner_unavailable"])
def test_invalid_lps_cannot_be_structure_source_or_refresh(invalid_kind):
    engine = controlled_engine(t=8)
    lc = engine.lifecycle
    valid = event(E.SOS, 0, engine.front_range)
    source = event(E.SOS, 7, engine.front_range)
    owner = lc._new("LPS", 1, source, 30)
    lc._change(owner, H.CONFIRMED, "pullback_response")
    if invalid_kind in ("invalidated", "expired"):
        lc._change(owner, H.INVALIDATED if invalid_kind == "invalidated" else H.EXPIRED, "support_lost")
    elif invalid_kind == "range_version":
        owner.reference_range_version += 1
    elif invalid_kind == "owner_unavailable":
        owner.status_available_at = lc.now + timedelta(seconds=1)
    lps = replace(event(E.LPS, 7, engine.front_range),
                  evidence_owner_id="missing" if invalid_kind == "missing_owner" else owner.hypothesis_id)
    lc.events.extend([valid, lps])
    structures(engine, Dominance.DEMAND)
    structure = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "STRUCTURE")
    assert structure.data["source"].event_id == valid.event_id
    assert structure.data["last_trigger"] == 0
    structures(engine, Dominance.DEMAND)
    assert structure.data["last_trigger"] == 0


def test_structure_triggers_exclude_old_future_and_other_range_facts():
    engine = controlled_engine(t=10)
    valid = event(E.SOS, 9, engine.front_range)
    old = event(E.SOS, 0, engine.front_range)
    future = event(E.SOS, 11, engine.front_range)
    not_available = replace(event(E.SOS, 10, engine.front_range), available_at=engine.lifecycle.now + timedelta(seconds=1))
    other = replace(event(E.INTERNAL_JOC, 10, engine.front_range), reference_range_id="OTHER")
    engine.lifecycle.events.extend([valid, old, future, not_available, other])
    assert engine._structure_triggers(1, engine.lifecycle.now) == (valid,)
    structures(engine, Dominance.DEMAND)
    owner = next(iter(engine.lifecycle.hypotheses.values()))
    assert owner.data["source"].event_id == valid.event_id and owner.data["last_trigger"] == 9


@pytest.mark.parametrize("dominance, expected", [(Dominance.NEUTRAL, H.EXPIRED),
                                                  (Dominance.DEMAND, H.CANDIDATE),
                                                  (Dominance.NOT_EVALUABLE, H.CANDIDATE)])
def test_lps_terminated_after_being_used_cannot_extend_structure(dominance, expected):
    engine = controlled_engine(t=8)
    lc = engine.lifecycle
    lc.events.append(event(E.SOS, 0, engine.front_range))
    lps_owner = lc._new("LPS", 1, event(E.SOS, 7, engine.front_range), 30)
    lc._change(lps_owner, H.CONFIRMED, "pullback_response")
    lc.events.append(replace(event(E.LPS, 7, engine.front_range), evidence_owner_id=lps_owner.hypothesis_id))
    structures(engine, Dominance.DEMAND)
    owner = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "STRUCTURE")
    assert owner.data["last_trigger"] == 7
    lc._change(lps_owner, H.INVALIDATED, "support_lost")
    for t in (9, 10):
        advance_controlled(engine, t)
        structures(engine, dominance, entry=False)
    assert owner.status is expected
    assert owner.data["last_trigger"] == 7  # Retained historical provenance.
    if expected is H.EXPIRED:
        assert owner.state_transition_history[-1].reason == "signal_stale"


def trending_engine():
    engine = WyckoffV4Engine(contract())
    engine.front_range = replace(rng(r=102.2), established_bar=-1, origin_bar=-1)
    bars = [bar(i, 100 + i % 3, volume=100) for i in range(60)]
    bars += [bar(i, 103 + i - 60, open=102.5 + i - 60,
                 high=103.25 + i - 60, low=102.5 + i - 60, volume=100) for i in range(60, 83)]
    engine.replay(bars)
    return engine


def test_raw_sideways_consolidation_retires_after_historical_phase_e():
    engine = trending_engine()
    old = engine.front_range
    acceptance_id = engine.lifecycle.active_acceptance().acceptance_id
    prices = [121, 122, 123, 124, 125, 124, 123, 122, 121, 120] * 3
    for t, c in enumerate(prices, 83):
        engine.process_bar(bar(t, c, high=c + .5, low=c - .5, volume=100))
    closed = next(r for r in engine.range_history if r.range_id == old.range_id)
    assert closed.state is R.RETIRED
    assert engine.front_range.parent_range_id == old.range_id
    assert engine.front_range.S > old.R + old.epsilon
    assert any(phase["acceptance_id"] == acceptance_id and phase["reason"] == "conditions_no_longer_met"
               for phase in engine.phase_history)
    assert engine.lifecycle.acceptances[acceptance_id].status is H.EXPIRED
    for phase in engine.phase_history:
        assert phase["reference_range_id"] == old.range_id and phase["reference_range_version"] == old.version


@pytest.mark.parametrize("invalid_history", ["old_acceptance", "old_range", "old_version", "wrong_direction", "future"])
def test_phase_e_history_cannot_be_borrowed_by_current_acceptance(invalid_history):
    engine = trending_engine()
    a = engine.lifecycle.active_acceptance()
    phase = engine._phase_e.copy()
    engine._phase_e = None
    if invalid_history == "old_acceptance":
        phase["acceptance_id"] = "OLD-ACCEPTANCE"
    elif invalid_history == "old_range":
        phase["reference_range_id"] = "OLD-RANGE"
    elif invalid_history == "old_version":
        phase["reference_range_version"] += 1
    elif invalid_history == "wrong_direction":
        phase["direction"] = -a.direction
    else:
        phase["available_at"] = engine.lifecycle.now + timedelta(seconds=1)
    engine.phase_history = [phase]
    assert not engine._acceptance_has_phase_e(engine.lifecycle.now)


def test_old_failed_acceptance_history_does_not_retire_new_acceptance():
    engine = trending_engine()
    lc, old_range = engine.lifecycle, engine.front_range
    old_acceptance = lc.active_acceptance()
    phase = engine._phase_e.copy()
    engine.phase_history.append({**phase, "ended_bar": 82, "reason": "acceptance_terminated"})
    engine._phase_e = None
    lc._change(old_acceptance, H.INVALIDATED, "inside_closes")
    # A distinct later episode in the same range is not entitled to the old E.
    new_acceptance = replace(old_acceptance, acceptance_id="NEW-ACCEPTANCE", status=H.CONFIRMED,
                             source_hypothesis_id=None, state_transition_history=[])
    lc.acceptances[new_acceptance.acceptance_id] = new_acceptance
    assert not engine._acceptance_has_phase_e(lc.now)
    prices = [121, 122, 123, 124, 125, 124, 123, 122, 121, 120.5]
    engine._features = [feature(t, prices[t % 10], high=prices[t % 10] + .5,
                                low=prices[t % 10] - .5) for t in range(83)]
    engine._warmup_count = 83
    lc.begin(engine._features, old_range, engine._features[-1].bar.bar_end, "T:82")
    candidate = establish_path_b([RangeBar(f.index, f.bar.high, f.bar.low, f.bar.close) for f in engine._features],
                                 engine.params, 1, .01)
    assert not isinstance(candidate, RangeRejection) and candidate.S > old_range.R + old_range.epsilon
    engine._range_commit(lc.now)
    assert engine.front_range.range_id == old_range.range_id
    assert not any(r.state is R.RETIRED for r in engine.range_history)


def joc_engine():
    engine = WyckoffV4Engine(contract())
    engine.front_range = replace(rng(r=102.2), established_bar=-1, origin_bar=-1)
    bars = [bar(i, 100 + i % 3, open=100 + i % 3 - .5,
                high=100 + i % 3 + .25, low=100 + i % 3 - .5, volume=100) for i in range(60)]
    bars += [bar(60, 105, open=102.5, high=105.3, low=102.4, volume=200),
             bar(61, 104.8, open=105, high=105.1, low=104, volume=30)]
    engine.replay(bars)
    return engine


def test_raw_joc_phase_d_candidate_persists_until_sources_terminate():
    engine = joc_engine()
    assert "D" in engine.publications[60].snapshot["phase_candidate"]
    assert "D" in engine.publications[61].snapshot["phase_candidate"]
    lc = engine.lifecycle
    parent = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    lc._change(parent, H.INVALIDATED, "deep_return")
    candidates, supported = engine._phases(TrendState.UNKNOWN, SimpleNamespace(dominance=Dominance.NOT_EVALUABLE), lc.now)
    assert "D" not in candidates
    assert not any(phase["phase"] == "D" for phase in supported)


def test_external_d_candidate_can_depend_on_matching_acceptance_after_breakout_expires():
    engine = joc_engine()
    lc = engine.lifecycle
    parent = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    acceptance = lc._accept(1, "hold", parent)
    lc._change(parent, H.EXPIRED, "source_current_evidence_ended")
    assert engine._has_standalone_phase_d(lc.now)
    lc._change(acceptance, H.INVALIDATED, "inside_closes")
    assert not engine._has_standalone_phase_d(lc.now)


def test_internal_d_candidate_depends_on_matching_active_internal_break_lps():
    engine = controlled_engine(t=1)
    lc = engine.lifecycle
    source = event(E.INTERNAL_JOC, 0, engine.front_range, level=93)
    lc.events.append(source)
    owner = lc._new("LPS", 1, source, 15, source_kind="internal_break", level=93)
    assert engine._has_standalone_phase_d(lc.now)
    engine.front_range = replace(engine.front_range, version=2)
    lc.rng = engine.front_range
    assert not engine._has_standalone_phase_d(lc.now)
    engine.front_range = replace(engine.front_range, version=1)
    lc.rng = engine.front_range
    lc._change(owner, H.EXPIRED, "confirmation_deadline")
    assert not engine._has_standalone_phase_d(lc.now)


def test_old_strong_break_d_candidate_is_not_revived_by_unrelated_new_acceptance():
    engine = joc_engine()
    lc = engine.lifecycle
    parent = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    acceptance = lc._accept(1, "hold", parent)
    lc._change(acceptance, H.INVALIDATED, "inside_closes")
    new_acceptance = replace(acceptance, acceptance_id="NEW-ACCEPTANCE", status=H.CONFIRMED,
                             source_hypothesis_id=None, source_event_ids=(), state_transition_history=[])
    lc.acceptances[new_acceptance.acceptance_id] = new_acceptance
    assert not engine._has_standalone_phase_d(lc.now)

"""Raw OHLC end-to-end recognition plus dependency-graph invariants."""
from dataclasses import replace
from datetime import timedelta
import pytest

from cta_lab.wyckoff_v4 import WyckoffV4Engine
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.types import EventCode as E, HypothesisStatus as H
from .scenarios import Scenario, bar, contract, event, rng
from .test_group_b import lps_path


def stopping_path():
    bars = [bar(i, 160 - i, open=160 - i + .5, high=161 - i, low=159 - i, volume=100) for i in range(60)]
    bars += [bar(60, 99, open=100, high=103, low=94, volume=300),
             bar(61, 101, open=99, high=103, low=98, volume=100),
             bar(62, 100.5, open=101, high=101.5, low=100, volume=100),
             bar(63, 99.8, open=100.5, high=101, low=99, volume=100)]
    bars += [bar(i, 99, high=99.5, low=98.5, volume=70) for i in range(64, 68)]
    bars += [bar(68, 94.9, open=95.1, high=95.5, low=94.3, volume=40),
             bar(69, 95.6, open=94.9, high=96, low=94.8, volume=100)]
    return bars


def mirror(b):
    return replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close)


@pytest.mark.parametrize("down", [False, True])
def test_raw_climax_ar_old_st_path_and_frozen_range(down):
    bars = stopping_path()
    if down:
        bars = [mirror(b) for b in bars]
    engine = WyckoffV4Engine(contract())
    publications = engine.replay(bars)
    climax = E.BC_FEATURE if down else E.SC_FEATURE
    stopping = E.BC_STOPPING_SEQUENCE if down else E.SC_STOPPING_SEQUENCE
    assert climax in {e.event_type for e in publications[60].events}
    assert E.ST_OBSERVED in {e.event_type for e in publications[68].events}
    assert {stopping, E.ST_OBSERVED} <= {e.event_type for e in publications[69].events}
    owner = next(h for h in engine.lifecycle.hypotheses.values() if h.hypothesis_type == "CLIMAX")
    assert owner.data["ar_bar"] == 63 and owner.data["test_confirmed_q"] == 68
    assert owner.status is H.CONFIRMED
    assert engine.front_range.established_bar == 69
    assert engine.front_range.origin_bar == 60
    assert publications[68].snapshot["range"] is None
    assert publications[69].snapshot["outside_up_streak"] == 0
    assert publications[69].snapshot["outside_down_streak"] == 0
    assert any(s["phase"] == "A" for s in publications[69].snapshot["phase_supported_by_rules"])


def test_absorption_both_boundaries_create_distinct_owners():
    s = Scenario(rng(s=100, r=101, eps=.2))
    s.step(100.5, [event(E.EFFORT_RESULT_NARROW_BAR, 0, s.range)], high=101.2, low=99.8)
    owners = [h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "ABSORPTION"]
    assert len(owners) == 2 and {h.direction for h in owners} == {1, -1}
    assert len({h.hypothesis_id for h in owners}) == 2


def test_parent_cycle_and_range_mismatch_are_rejected():
    s = Scenario()
    lps_path(s)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    child = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    original = parent.lifecycle_parent_ref
    with pytest.raises(ValueError, match="cycle"):
        s.lifecycle.bind_parent(child, ("hypothesis", child.hypothesis_id))
    s.lifecycle.bind_parent(child, ("hypothesis", parent.hypothesis_id))
    with pytest.raises(ValueError, match="cycle"):
        s.lifecycle.bind_parent(parent, ("hypothesis", child.hypothesis_id))
    assert parent.lifecycle_parent_ref == original
    child.reference_range_version = 2
    with pytest.raises(ValueError, match="range_mismatch"):
        s.lifecycle.bind_parent(child, ("hypothesis", parent.hypothesis_id))


def test_parent_not_yet_available_suspends_child_without_reviving_status():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    s.step(105, [event(E.BREAKOUT_UP, 0, s.range)], low=104.9)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    child = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    parent.status_available_at = s.features[-1].bar.bar_end + timedelta(days=1)
    s.step(104.8, high=105, low=104.7)
    assert child.status is H.CANDIDATE
    assert not s.lifecycle.parent_valid(child)
    assert not any(t.status == "confirmed" for t in child.state_transition_history)


def test_unqualified_lower_priority_lps_is_not_expired_by_another_source():
    s = Scenario(rng(s=100, r=110, eps=.2))
    s.step(105, [event(E.INTERNAL_JOC, 0, s.range, level=100)], high=105.1, low=104.9)
    s.step(106, [event(E.SOS, 1, s.range)], high=106.1, low=105.9)
    internal = next(h for h in s.lifecycle.hypotheses.values() if h.source_kind == "internal_break")
    sos = next(h for h in s.lifecycle.hypotheses.values() if h.source_kind == "internal_sos")
    s.step(105.8, high=106, low=104.5, ns=.5, volume=.5)
    s.step(105.8, high=106, low=104.5, ns=.5, volume=.5)
    # Make only the higher-priority source ready: the other source has no round yet.
    sos.data.update(b=None, search_from=99)
    events = s.step(106.2, high=106.3, low=105.7, net=.4)
    assert internal.status is H.CONFIRMED
    assert sos.status is H.CANDIDATE
    assert sum(e.event_type is E.LPS for e in events) == 1


def test_qualified_same_bar_lps_facts_all_preserved_one_current_label():
    s = Scenario(rng(s=100, r=110, eps=.2))
    s.step(105, [event(E.INTERNAL_JOC, 0, s.range, level=100)], high=105.1, low=104.9)
    s.step(106, [event(E.SOS, 1, s.range)], high=106.1, low=105.9)
    s.step(105.8, high=106, low=104.5, ns=.5, volume=.5)
    s.step(105.8, high=106, low=104.5, ns=.5, volume=.5)
    events = s.step(106.2, high=106.3, low=105.7, net=.4)
    facts = [e for e in events if e.event_type is E.LPS]
    assert len(facts) == 2
    owners = [s.lifecycle.hypotheses[e.evidence_owner_id] for e in facts]
    assert {h.status for h in owners} == {H.CONFIRMED, H.EXPIRED}
    selected = next(h for h in owners if h.status is H.CONFIRMED)
    assert selected.source_kind == "internal_break"
    assert len(s.lifecycle.phase_d) == 1
    assert next(iter(s.lifecycle.current_lps.values())) == selected.hypothesis_id


@pytest.mark.parametrize("down", [False, True])
def test_raw_spring_or_ut_followed_by_bearish_or_bullish_test_and_response(down):
    bars = stopping_path()
    bars += [bar(i, 99, high=99.5, low=98.5, volume=70) for i in range(70, 76)]
    bars += [bar(76, 94.95, open=94.5, high=95.05, low=93.5, volume=20),
             bar(77, 95, high=95.2, low=94.7, volume=40),
             bar(78, 94.8, high=95, low=94.5, volume=40),
             bar(79, 94.4, open=94.7, high=94.9, low=94.1, volume=25),
             bar(80, 95.05, open=94.4, high=95.1, low=94.35, volume=80)]
    if down:
        bars = [mirror(b) for b in bars]
    engine = WyckoffV4Engine(contract())
    publications = engine.replay(bars)
    observation = E.UT_TEST_OBSERVED if down else E.TEST_OBSERVED
    confirmation = E.UT_TEST_CONFIRMED if down else E.SPRING_TEST_CONFIRMED
    onset = E.UT_FEATURE if down else E.SPRING_LOW_VOLUME
    assert onset in {e.event_type for e in publications[76].events}
    assert observation in {e.event_type for e in publications[79].events}
    assert confirmation in {e.event_type for e in publications[80].events}
    assert len(publications[80].snapshot["counted_sequences"]) == 1
    assert publications[80].snapshot["evidence_groups"]["bias"] == ("supply" if down else "demand")
    event_ = next(e for e in publications[76].events if e.event_type is onset)
    assert event_.onset_snapshot.asof_bar == 75
    assert event_.frozen_S == publications[75].snapshot["range"]["S"]
    assert event_.data_version == "live-1" and event_.timeframe == "1d"
    assert publications[76].snapshot["hypotheses"][-1]["status"] != "confirmed"


def test_confirmed_loop_keeps_new_test_facts_without_duplicate_confirmation():
    s = Scenario(rng(s=100, r=110, eps=.1))
    s.step(100.5, [event(E.SPRING_LOW_VOLUME, 0, s.range,
                        metrics={"extreme": 99.5, "direction": 1})])
    s.step(101, [event(E.SOS, 1, s.range)])
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "SPRING")
    original_confirmed = (owner.confirmed_bar, owner.confirmed_at)
    sequence = s.lifecycle.sequences[0]
    s.step(100.8)
    events = s.step(100.5, open=100.8, high=100.9, low=100.1, volume=.5)
    test = next(e for e in events if e.event_type is E.TEST_OBSERVED)
    assert test.pattern_start == test.pattern_end == 3
    assert test.event_time == s.features[3].bar.bar_end
    assert test.metrics["not_adopted"] == "closed_loop_already_confirmed"
    assert owner.status is H.CONFIRMED and owner.pending_observation is None
    assert (owner.confirmed_bar, owner.confirmed_at) == original_confirmed
    assert s.lifecycle.sequences == [sequence]


def test_test_higher_low_equal_epsilon_uses_exact_price_comparison():
    s = Scenario(rng(s=100, r=110, eps=.1))
    s.step(100.5, [event(E.SPRING_LOW_VOLUME, 0, s.range,
                        metrics={"extreme": 100.2, "direction": 1})])
    s.step(100.8)
    s.step(100.8)
    events = s.step(100.5, open=100.7, high=100.8, low=100.3, volume=.5)
    assert E.TEST_OBSERVED in {e.event_type for e in events}


@pytest.mark.parametrize("down", [False, True])
def test_raw_joc_fti_pullback_and_phase_d_starts_after_confirmation(down):
    bars = [bar(i, 100 + i % 3, open=100 + i % 3 - .5,
                high=100 + i % 3 + .25, low=100 + i % 3 - .5, volume=100) for i in range(60)]
    bars += [bar(60, 105, open=102.5, high=105.3, low=102.4, volume=200),
             bar(61, 104.8, open=105, high=105.1, low=104, volume=30),
             bar(62, 104.8, high=105.1, low=104, volume=30),
             bar(63, 105.6, open=104.8, high=105.8, low=104.7, volume=100),
             bar(64, 106.2, open=105.6, high=106.4, low=105.5, volume=100)]
    range_ = replace(rng(r=102.2), established_bar=-1, origin_bar=-1)
    if down:
        bars = [mirror(b) for b in bars]
        range_ = replace(range_, S=97.8, R=110, M=103.9)
    engine = WyckoffV4Engine(contract())
    engine.front_range = range_
    publications = engine.replay(bars)
    source = next(e for e in publications[60].events if e.event_type is (E.BREAKOUT_DOWN if down else E.BREAKOUT_UP))
    assert source.strength == ("fti" if down else "joc")
    lps = next(e for e in publications[63].events if e.event_type is (E.LPSY if down else E.LPS))
    assert lps.sub_type == ("LPSY_BELOW_ICE" if down else "LPS_ABOVE_CREEK")
    assert "D" in publications[63].snapshot["phase_candidate"]
    assert not any(p["phase"] == "D" for p in publications[63].snapshot["phase_supported_by_rules"])
    phase = next(p for p in publications[64].snapshot["phase_supported_by_rules"] if p["phase"] == "D")
    assert phase["confirmed_bar"] == 64 and phase["lps_id"] == lps.evidence_owner_id
    owner = engine.lifecycle.hypotheses[lps.evidence_owner_id]
    assert owner.lifecycle_parent_ref == ("acceptance", lps.acceptance_id)
    assert publications[63].snapshot["acceptance_id"] == lps.acceptance_id
    assert len(engine.lifecycle.acceptances) == 1


def test_raw_phase_e_rechecks_direction_and_leaves_history():
    engine = WyckoffV4Engine(contract())
    engine.front_range = replace(rng(r=102.2), established_bar=-1, origin_bar=-1)
    bars = [bar(i, 100 + i % 3, volume=100) for i in range(60)]
    bars += [bar(i, 103 + i - 60, open=102.5 + i - 60,
                 high=103.25 + i - 60, low=102.5 + i - 60, volume=100) for i in range(60, 83)]
    pubs = engine.replay(bars)
    assert any(phase["phase"] == "E" for phase in pubs[-1].snapshot["phase_supported_by_rules"])
    original_id = engine._phase_e["phase_id"]
    # Remain exterior and acceptance-valid while eliminating positive window delta.
    for i in range(83, 105):
        pub = engine.process_bar(bar(i, 125, high=125.1, low=124.9, volume=100))
    assert engine.lifecycle.active_acceptance() is not None
    assert not any(phase["phase"] == "E" for phase in pub.snapshot["phase_supported_by_rules"])
    assert any(phase["phase_id"] == original_id and phase["reason"] == "conditions_no_longer_met"
               for phase in engine.phase_history)


def test_ended_interpretation_id_cannot_be_reused_by_same_fact():
    s = Scenario()
    source = event(E.SOS, 0, s.range)
    s.step(105)
    first = s.lifecycle._new("STRUCTURE", 1, source, 10, last_trigger=0)
    s.lifecycle._change(first, H.INVALIDATED, "opposite_direction")
    s.step(105)
    second = s.lifecycle._new("STRUCTURE", 1, source, 11, last_trigger=0)
    assert first.hypothesis_id != second.hypothesis_id
    assert first.status is H.INVALIDATED and second.status is H.CANDIDATE
    assert s.lifecycle.hypotheses[first.hypothesis_id] is first


def test_old_reference_fact_does_not_create_new_foreground_hypothesis():
    old = rng(s=100, r=110, eps=.1)
    new = replace(rng(s=105, r=115, eps=.1), range_id="NEXT-R", version=2)
    s = Scenario(new)
    completion = event(E.SPRING_LOW_VOLUME, 0, old,
                       metrics={"extreme": 99.5, "direction": 1})
    s.step(106, [completion])
    assert completion in s.lifecycle.events
    assert not any(h.hypothesis_type == "SPRING" for h in s.lifecycle.hypotheses.values())


def test_gap_does_not_modify_confirmed_pullback_reference_history():
    s = Scenario()
    lps_path(s)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    frozen = (owner.data["b"], owner.data["q"], owner.data["pullback_extreme"])
    s.lifecycle.pause_gap()
    assert (owner.data["b"], owner.data["q"], owner.data["pullback_extreme"]) == frozen


def test_recross_fact_links_original_owner_and_accepted_object():
    s = Scenario()
    s.step(100.3, [event(E.BREAKOUT_UP, 0, s.range)], low=100.2)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    s.step(100.2, low=100.2)
    s.step(100.3, [event(E.BREAKOUT_UP, 2, s.range)], low=100.2)
    recross = next(e for e in s.lifecycle.events if e.event_type is E.BREAKOUT_UP and e.pattern_end == 2)
    assert recross.evidence_owner_id == parent.hypothesis_id and recross.acceptance_id is None
    s.step(100.4, low=100.3)
    s.step(100.5, low=100.3)
    a = s.lifecycle.active_acceptance()
    s.step(100.2, low=100.1)
    s.step(100.4, [event(E.BREAKOUT_UP, 6, s.range)], low=100.3)
    linked = next(e for e in s.lifecycle.events if e.event_type is E.BREAKOUT_UP and e.pattern_end == 6)
    assert linked.acceptance_id == a.acceptance_id and linked.evidence_owner_id == parent.hypothesis_id
    assert parent.created_bar == 0 and len(s.lifecycle.acceptances) == 1


def test_invalidation_rules_register_lps_confirmation_bar_and_origin():
    s = Scenario()
    lps_path(s)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    condition = next(c for c in owner.invalidation_conditions if c["basis"] == "confirmed_pullback_close_loss")
    assert condition["counting_origin"] == "post"
    assert condition["counting_origin_bar"] == owner.confirmed_bar
    assert condition["reference"] == owner.data["pullback_extreme"]
    assert owner.confirmation_conditions[0]["confirm_step_units"] == 1


def test_climax_without_range_stays_unique_when_foreground_becomes_available():
    s = Scenario()
    source = event(E.SC_FEATURE, 0, metrics={"extreme": 100, "direction": 1, "effort": 3})
    s.step(100.5, [source])
    s.step(100.4, [event(E.SC_FEATURE, 1, s.range,
                       metrics={"extreme": 99.8, "direction": 1, "effort": 3})])
    owners = [h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "CLIMAX"]
    assert len(owners) == 1 and owners[0].data["extreme"] == 100

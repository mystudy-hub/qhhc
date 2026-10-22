"""Integration/contract acceptance D01–D20 from §11.2."""
from dataclasses import replace
from datetime import timedelta
import pytest

from cta_lab.wyckoff_v4.engine import WyckoffV4Engine
from cta_lab.wyckoff_v4.evaluation import EvaluationBar, evaluate_ohlc
from cta_lab.wyckoff_v4.evidence import (ActivityGroup, Dominance, EvidenceGroups, SideGroup,
                                        compute_direction_evidence, compute_wave_groups,
                                        evaluate_bias_group, filter_current_sequences)
from cta_lab.wyckoff_v4.indicators import IndicatorState, NotEvaluableReason
from cta_lab.wyckoff_v4.output import at_least_step
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.types import EventCode as E, HypothesisStatus as H, RangeState as R, TriState, TrendState
from .scenarios import Scenario, T0, bar, contract, event, feature, rng
from .test_group_b import breakout, lps_path
from .test_group_c import make_context, make_sequence, _absorption_setup


def test_d01_last_deadline_bar_hold_uses_current_streak():
    s = Scenario()
    breakout(s)
    for _ in range(9):
        s.step(100.2, low=100.2)
    s.step(100.3, low=100.2)
    s.step(100.4, low=100.2)
    assert s.range.outside_up_streak == 2
    events = s.step(100.5, low=100.2)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    assert parent.confirmed_bar == parent.confirmation_deadline_bar == 12
    assert parent.status is H.CONFIRMED
    assert len(s.lifecycle.acceptances) == 1 and s.lifecycle.active_acceptance().mode.value == "hold"


def late_acceptance(s):
    breakout(s)
    for _ in range(10):
        s.step(100.2, low=100.2)
    for c in (100.3, 100.4, 100.5):
        s.step(c, low=100.2)
    return s.lifecycle.active_acceptance()


def test_d02_late_acceptance_failure_is_independent():
    s = Scenario()
    a = late_acceptance(s)
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    s.step(99.5)
    assert a.status is H.CONFIRMED and old.status is H.EXPIRED
    s.step(99.4)
    assert a.status is H.INVALIDATED and s.range.state is R.ACTIVE
    assert old.status is H.EXPIRED


@pytest.mark.parametrize("reverse_order", [False, True])
def test_d03_parent_failure_precedes_child_confirmation(reverse_order):
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    breakout(s, 105)
    s.step(104.8, low=100.3, high=105, volume=.5, ns=.5)
    s.step(104.8, low=100.3, high=105, volume=.5, ns=.5)
    if reverse_order:
        s.lifecycle.hypotheses = dict(reversed(list(s.lifecycle.hypotheses.items())))
    # Simulate an already determined parent termination before this close.
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    parent.status = H.INVALIDATED
    events = s.step(105.2, high=105.3, low=104.7, net=.4)
    child = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    assert child.status is H.INVALIDATED and E.LPS not in {e.event_type for e in events}


def test_d04_internal_creek_is_not_external_buec():
    s = Scenario(rng(s=100, r=110, eps=.2))
    events = lps_path(s, E.INTERNAL_JOC, 105.5, 105.1)
    lps = next(e for e in events if e.event_type is E.LPS)
    assert lps.sub_type == "LPS_INTERNAL"
    assert s.lifecycle.hypotheses[lps.evidence_owner_id].data["level"] == 105.1


def climax_with_ar(s):
    s.step(100.5, [event(E.SC_FEATURE, 0, metrics={"extreme": 100, "direction": 1, "effort": 3, "ns_ref": 4})])
    s.step(103.8, high=104)
    s.step(103.5, high=103.6)
    s.step(103, high=103.1)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "CLIMAX")
    assert owner.data["ar_bar"] == 3
    for _ in range(4):
        s.step(102)
    return owner


def test_d05_old_st_response_precedes_new_bearish_observation():
    s = Scenario()
    h = climax_with_ar(s)
    events = s.step(100.8, open=100.9, high=101, low=100.2, volume=.5, ns=.8)
    assert h.pending_observation["q"] == 8
    events = s.step(101.3, open=101.5, high=101.6, low=100.3, volume=.5, ns=1.3)
    assert h.status is H.CONFIRMED and h.data["test_confirmed_q"] == 8
    assert {E.SC_STOPPING_SEQUENCE, E.ST_OBSERVED} <= {e.event_type for e in events}
    assert sum(e.event_type is E.SC_STOPPING_SEQUENCE for e in s.lifecycle.events) == 1


def test_d06_disabled_or_insufficient_distinct_from_neutral():
    groups, counted, original = _absorption_setup()
    disabled = compute_direction_evidence(groups, SideGroup.DEMAND, counted,
                                          replace(WyckoffV4Params(), wave_module_enabled=False))
    assert disabled.dominance is Dominance.NOT_EVALUABLE
    assert "wave_module_disabled" in disabled.evaluation_reasons
    neutral_groups = replace(groups, activity=ActivityGroup.BALANCED, price_outcome=SideGroup.NEUTRAL,
                             up_waning=TriState.FALSE, down_waning=TriState.FALSE)
    result = compute_direction_evidence(neutral_groups, SideGroup.NEUTRAL, (), WyckoffV4Params())
    assert result.dominance is Dominance.NEUTRAL


def test_d07_excludes_entire_availability_overlap_bar():
    anchor = bar(0, 100)
    tau = anchor.bar_end + timedelta(seconds=2)
    overlap = bar(1, 100, high=103, low=99)
    first = bar(2, 100, high=101, low=99)
    records = [EvaluationBar(anchor, 1, anchor.received_at), EvaluationBar(overlap, 1, overlap.received_at),
               EvaluationBar(first, 1, first.received_at)]
    result = evaluate_ohlc(tau, records, [(overlap.bar_start, overlap.bar_end), (first.bar_start, first.bar_end)], horizon=1)
    assert result.outcome == "neither" and "availability_overlap" in result.reasons
    assert result.evaluation_start_at == first.bar_start and result.evaluation_wait_seconds == 298


def test_d08_confirm_step_equality_works_both_directions():
    assert at_least_step(100.01, 100, .01, 1)
    assert at_least_step(98.99, 99, .01, 1, -1)
    assert not at_least_step(100, 100, .01, 1)
    assert not at_least_step(99, 99, .01, 1, -1)


def test_d09_context_for_next_bar_has_no_extra_delay():
    engine = WyckoffV4Engine(contract())
    for i in range(65):
        publication = engine.process_bar(bar(i, 150 - i, volume=100))
        snapshot = engine.detector.ring.get(i)
        assert snapshot.context_for_next_bar.value == publication.snapshot["trend_now_asof_bar"]
    assert snapshot.context_for_next_bar is TrendState.DOWN


def test_d10_owner_invalidated_sequence_remains_history_but_loses_vote():
    s = Scenario(rng(s=100, r=110, eps=.1))
    s.step(100.5, [event(E.SPRING_LOW_VOLUME, 0, s.range, metrics={"extreme": 99.5, "direction": 1})])
    s.step(101, [event(E.SOS, 1, s.range)], net=.5)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "SPRING")
    assert owner.status is H.CONFIRMED and len(s.lifecycle.sequences) == 1
    s.step(99.3)
    assert owner.status is H.INVALIDATED
    context = replace(make_context(), current_bar=2, range_id=s.range.range_id, range_version=1,
                      decision_time=s.features[-1].bar.bar_end)
    result = filter_current_sequences(s.lifecycle.current_sequences(), context)
    assert len(result.history_confirmed) == 1 and not result.counted
    assert evaluate_bias_group(result.counted) is SideGroup.NEUTRAL


def test_d11_all_three_acceptance_modes_share_failure_exit():
    failure_offsets = []
    for mode in ("hold", "pullback", "late_hold"):
        s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100) if mode == "pullback" else None)
        if mode == "pullback":
            lps_path(s)
        elif mode == "late_hold":
            late_acceptance(s)
        else:
            breakout(s)
            s.step(100.4, low=100.3)
            s.step(100.5, low=100.3)
        a = s.lifecycle.active_acceptance()
        assert a.mode.value == mode
        s.step(99.5)
        assert a.status is H.CONFIRMED
        s.step(99.4)
        assert a.status is H.INVALIDATED
        failure_offsets.append(s.lifecycle.t - a.accepted_bar)
    assert failure_offsets == [2, 2, 2]


def test_d12_mirror_hold_deadline_lpsy_and_confirm_step():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    s.step(85, [event(E.BREAKOUT_DOWN, 0, s.range, strength="fti")], high=85.1, low=84.9)
    s.step(85.2, high=89.8, low=85, volume=.5, ns=.5)
    s.step(85.2, high=89.8, low=85, volume=.5, ns=.5)
    events = s.step(84.8, high=85.3, low=84.7, net=-.4)
    lpsy = next(e for e in events if e.event_type is E.LPSY)
    assert lpsy.sub_type == "LPSY_AT_ICE" and s.range.state is R.EXIT_DOWN_ACCEPTED
    assert at_least_step(84.99, 85, .01, 1, -1)


def test_d13_lps_and_source_confirm_on_shared_last_deadline():
    p = replace(WyckoffV4Params(), breakout_hold_closes=100, breakout_validity_window=3, lps_window_max=3)
    s = Scenario(params=p)
    events = lps_path(s)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    lps = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    assert parent.status is lps.status is H.CONFIRMED
    assert parent.confirmed_bar == lps.confirmed_bar == 3
    assert len(s.lifecycle.acceptances) == 1
    a = s.lifecycle.active_acceptance()
    assert lps.lifecycle_parent_ref == parent.lifecycle_parent_ref == ("acceptance", a.acceptance_id)


def test_d14_lps_valid_through_inclusive_then_expires_at_start():
    s = Scenario()
    lps_path(s)
    lps = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS")
    for _ in range(20):
        s.step(105.2, high=105.3, low=105.1)
    assert s.lifecycle.t == lps.valid_through_bar and lps.status is H.CONFIRMED
    s.step(105.2, high=105.3, low=105.1)
    assert lps.status is H.EXPIRED and not s.lifecycle.phase_d


def test_d15_atomic_handoff_only_preserves_live_phase_id():
    s = Scenario(rng(s=100, r=110, eps=.2))
    lps_path(s, E.INTERNAL_JOC, 105.5, 105.1)
    key = next(iter(s.lifecycle.phase_d))
    old_id = s.lifecycle.phase_d[key]["lps_id"]
    phase_id = s.lifecycle.phase_d[key]["phase_id"]
    s.lifecycle.phase_d[key]["status"] = "confirmed"
    t = len(s.features)
    s.step(106, [event(E.INTERNAL_JOC, t, s.range, level=105.1)], high=106.1, low=105.9)
    s.step(105.8, high=106, low=105.2, ns=.5, volume=.5)
    s.step(105.8, high=106, low=105.2, ns=.5, volume=.5)
    s.step(106.2, high=106.3, low=105.7, net=.4)
    assert s.lifecycle.hypotheses[old_id].status is H.EXPIRED
    assert s.lifecycle.phase_d[key]["phase_id"] == phase_id
    assert s.lifecycle.phase_d[key]["lps_id"] != old_id
    assert s.lifecycle.phase_d[key]["handoffs"]
    # Once price loss terminates the old phase, a fresh source cannot revive it.
    s.step(104.9, high=105, low=104.8)
    assert key not in s.lifecycle.phase_d
    t = len(s.features)
    s.step(106, [event(E.INTERNAL_JOC, t, s.range, level=105.1)], high=106.1, low=105.9)
    s.step(105.8, high=106, low=105.2, ns=.5, volume=.5)
    s.step(105.8, high=106, low=105.2, ns=.5, volume=.5)
    s.step(106.2, high=106.3, low=105.7, net=.4)
    assert s.lifecycle.phase_d[key]["phase_id"] != phase_id


def test_d16_same_bar_hold_and_lps_create_one_acceptance():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=4))
    events = lps_path(s)
    a = s.lifecycle.active_acceptance()
    assert len(s.lifecycle.acceptances) == 1
    assert a.primary_basis == "hold" and a.confirmation_bases == ["hold", "pullback"]
    lps = next(e for e in events if e.event_type is E.LPS)
    assert lps.acceptance_id == a.acceptance_id


def test_d17_missing_zero_insufficient_late_oi_does_not_block_price():
    p = replace(WyckoffV4Params(), oi_window=2)
    state = IndicatorState(p, oi_method="sum_raw")
    fs = []
    for i in range(5):
        b = replace(bar(i, 100 + i), open_interest=100, oi_available_at=bar(i, 0).bar_end)
        fs.append(state.advance(b))
    assert fs[-1].oi.realtime_view().oi_z.reason is NotEvaluableReason.OI_ZERO_SCALE
    assert fs[-1].oi.realtime_view().oi_quadrant is None
    state = IndicatorState(p, oi_method="sum_raw")
    for i in range(4):
        b = replace(bar(i, 100 + i), open_interest=100 + i,
                    oi_available_at=bar(i, 0).bar_end + (timedelta(hours=1) if i == 3 else timedelta(0)))
        f = state.advance(b)
    assert f.oi.realtime_view().oi_z.reason is NotEvaluableReason.OI_NOT_YET_AVAILABLE
    assert f.oi.realtime_view().oi_quadrant is None and f.cp.is_available
    assert fs[0].oi.realtime_view().oi_z.reason is NotEvaluableReason.OI_MISSING
    assert fs[1].oi.realtime_view().oi_z.reason is NotEvaluableReason.OI_HISTORY_INSUFFICIENT


def test_d18_evaluation_anchor_must_have_really_arrived():
    earlier = bar(0, 100)
    later = bar(1, 120, received=bar(1, 0).bar_end + timedelta(hours=1))
    first = bar(3, 100)
    tau = later.bar_end + timedelta(seconds=2)
    records = [EvaluationBar(earlier, 1, earlier.received_at), EvaluationBar(later, 10, later.received_at),
               EvaluationBar(first, 1, first.received_at)]
    result = evaluate_ohlc(tau, records, [(first.bar_start, first.bar_end)], horizon=1)
    assert result.reference_bar_id == earlier.key and result.reference_price == 100
    assert result.evaluation_atr == 1


def test_d19_boundary_true_survives_unrelated_group_missing():
    groups, counted, _ = _absorption_setup()
    groups = replace(groups, activity=ActivityGroup.NOT_EVALUABLE, up_waning=TriState.NOT_EVALUABLE,
                     down_waning=TriState.NOT_EVALUABLE)
    result = compute_direction_evidence(groups, SideGroup.DEMAND, counted, WyckoffV4Params())
    assert result.dominance is Dominance.DEMAND


def test_d20_sow_aliases_share_one_vote_after_one_owner_fails():
    first = replace(make_sequence(event_code=E.UT_SOW_SEQUENCE), sequence_id="UT",
                    evidence_owner_id="ut", confirmation_trigger_id="sow")
    second = replace(make_sequence(event_code=E.UTAD_WEAKNESS_SEQUENCE), sequence_id="UTAD",
                     evidence_owner_id="utad", confirmation_trigger_id="sow")
    both = filter_current_sequences((first, second), make_context())
    assert len(both.counted) == 1
    failed = replace(first, owner_status=H.INVALIDATED, owner_current_valid=False)
    remaining = filter_current_sequences((failed, second), make_context())
    assert [s.sequence_id for s in remaining.counted] == ["UTAD"]

"""C-group acceptance tests (spec 11.2 C01-C05) plus unit tests for the
8.3.2 B-sequence filter, the 8.3.3 tri-state dominance outcomes and the
8.4 structure hypotheses with duplicate-vote control (evidence.py).

Fixtures are constructed WaveRecord / SequenceRecord / TriggerEvent values:
the C group is specified to work from constructed completed wave records.
"""

from datetime import datetime

import pytest

from cta_lab.wyckoff_v4.evidence import (
    ActivityGroup,
    ConfigurationError,
    Dominance,
    PathName,
    SideGroup,
    SequenceFilterContext,
    SequenceRecord,
    TriggerEvent,
    compute_direction_evidence,
    compute_wave_groups,
    evaluate_bias_group,
    filter_current_sequences,
    finalize_dominance,
    larger_context,
    structure_hypotheses,
)
from cta_lab.wyckoff_v4.indicators import (
    NumFeature,
    OiZWindow,
    cumulative_effort,
    effort_ratio,
)
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.range import TradingRange
from cta_lab.wyckoff_v4.types import (
    EventCode,
    HypothesisStatus,
    RangeState,
    TrendState,
    TriState,
)
from cta_lab.wyckoff_v4.waves import WaveRecord, WaveStatus

T0 = datetime(2024, 1, 1, 9, 0)
PARAMS = WyckoffV4Params()
PARAMS.validate()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def make_range(origin_bar=0, established_bar=20):
    return TradingRange(
        range_id="rng-c01",
        version=1,
        state=RangeState.ACTIVE,
        S=90.0,
        R=110.0,
        M=100.0,
        W=20.0,
        A_ref=2.0,
        epsilon=0.2,
        origin_bar=origin_bar,
        established_bar=established_bar,
    )


RNG = make_range()
CURRENT_BAR = 30  # age 31 >= mature_range_age (25)


_wave_counter = {"n": 0}


def make_wave(
    direction,
    activity,
    peak,
    signed,
    end_index,
    *,
    cum_effort=None,
    bar_count=5,
    quality_limited=False,
    start_reference_close=100.0,
    atr_at_start=2.0,
):
    """Hand-built completed WaveRecord; closes derive from peak/signed."""
    _wave_counter["n"] += 1
    if cum_effort is None:
        cum_effort = activity * bar_count
    extreme_close = start_reference_close + direction * peak * atr_at_start
    end_close = start_reference_close + direction * signed * atr_at_start
    return WaveRecord(
        wave_id=f"w-{_wave_counter['n']:04d}",
        direction=direction,
        status=WaveStatus.COMPLETED,
        start_index=end_index - (bar_count - 1),
        end_index=end_index,
        reference_index=end_index - bar_count,
        confirmed_at_index=end_index + 1,
        start_time=T0,
        end_time=T0,
        confirmed_at=T0,
        available_at=T0,
        start_reference_close=start_reference_close,
        extreme_close=extreme_close,
        end_close=end_close,
        reversal_threshold=1.0,
        atr_at_start=atr_at_start,
        bar_count=bar_count,
        raw_volume_sum=activity * float(bar_count),
        baseline_volume_sum=float(bar_count),
        wave_activity_rate=NumFeature(value=activity),
        wave_cum_effort=NumFeature(value=cum_effort),
        peak_excursion=peak,
        signed_end_displacement=signed,
        giveback=peak - signed,
        path_distance=None,
        directional_efficiency=None,
        wave_speed=peak / bar_count,
        mean_bar_ns=None,
        wave_oiz=OiZWindow(num=NumFeature(value=None), available_at=None),
        quality_limited=quality_limited,
        constituent_change=False,
    )


_seq_counter = {"n": 0}


def make_sequence(
    event_code=EventCode.SUPPORT_RESPONSE_SEQUENCE,
    *,
    confirmed_bar=95,
    available_at=0.0,
    owner_status=HypothesisStatus.CONFIRMED,
    owner_current_valid=True,
    evaluable=True,
    evidence_owner_id=None,
    confirmation_trigger_id=None,
    primitive_event_ids=(),
    reference_range_id="rng-c01",
    reference_range_version=1,
):
    _seq_counter["n"] += 1
    return SequenceRecord(
        sequence_id=f"seq-{_seq_counter['n']:04d}",
        event_code=event_code,
        evidence_owner_id=evidence_owner_id
        or f"own-{_seq_counter['n']:04d}",
        confirmation_trigger_id=confirmation_trigger_id
        or f"trg-{_seq_counter['n']:04d}",
        primitive_event_ids=tuple(primitive_event_ids),
        reference_range_id=reference_range_id,
        reference_range_version=reference_range_version,
        confirmed_bar=confirmed_bar,
        available_at=available_at,
        owner_status=owner_status,
        owner_current_valid=owner_current_valid,
        evaluable=evaluable,
    )


def make_context(current_bar=100, decision_time=10.0):
    return SequenceFilterContext(
        current_bar=current_bar,
        range_id="rng-c01",
        range_version=1,
        decision_time=decision_time,
        params=PARAMS,
    )


# ---------------------------------------------------------------------------
# C01-C05 (11.2)
# ---------------------------------------------------------------------------

def c01_waves():
    # up: activity 0.6, peak 3, net 2.5; down: activity 1.2, peak 1, net 0.5.
    return [
        make_wave(+1, 0.6, 3.0, 2.5, end_index=6),
        make_wave(+1, 0.6, 3.0, 2.5, end_index=8),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=5),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=7),
    ]


def test_c01_demand_dominance_with_boundary_and_absorption():
    groups = compute_wave_groups(c01_waves(), RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.DOWN_MORE_ACTIVE
    assert groups.price_outcome is SideGroup.DEMAND

    counted = (make_sequence(),)  # one valid support-response sequence
    b_group = evaluate_bias_group(counted)
    assert b_group is SideGroup.DEMAND

    evidence = compute_direction_evidence(groups, b_group, counted, PARAMS)
    assert evidence.path_states[PathName.DEMAND_BOUNDARY_SUPPORTED] is TriState.TRUE
    assert evidence.path_states[PathName.ABSORPTION_RESPONSE] is TriState.TRUE
    assert evidence.dominance is Dominance.DEMAND
    assert set(evidence.dominance_paths) == {
        PathName.DEMAND_BOUNDARY_SUPPORTED,
        PathName.ABSORPTION_RESPONSE,
    }


def test_c02_no_support_response_and_no_waning_is_neutral():
    waves = [
        make_wave(+1, 0.6, 3.0, 2.5, end_index=6),
        make_wave(+1, 0.6, 3.0, 2.5, end_index=8),
        # three down waves, latest peak NOT below the prior median (1.0)
        make_wave(-1, 1.2, 1.0, 0.5, end_index=5),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=7),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=9),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.down_waning is TriState.FALSE
    assert groups.activity is ActivityGroup.DOWN_MORE_ACTIVE
    assert groups.price_outcome is SideGroup.DEMAND

    counted = ()  # no support-response sequence in the full window
    b_group = evaluate_bias_group(counted)
    assert b_group is SideGroup.NEUTRAL

    evidence = compute_direction_evidence(groups, b_group, counted, PARAMS)
    assert all(state is TriState.FALSE for state in evidence.path_states.values())
    assert evidence.dominance_paths == ()
    assert evidence.dominance is Dominance.NEUTRAL


def test_c03_supply_exhaustion_path_when_down_waning():
    waves = [
        # balanced activity: medians equal, neither >= k * other
        make_wave(+1, 1.0, 3.0, 2.5, end_index=6),
        make_wave(+1, 1.0, 3.0, 2.5, end_index=8),
        # >= 3 down waves, latest peak (0.8) below prior median (1.0)
        make_wave(-1, 1.0, 1.0, 0.5, end_index=5),
        make_wave(-1, 1.0, 1.0, 0.5, end_index=7),
        make_wave(-1, 1.0, 0.8, 0.5, end_index=9),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.BALANCED
    assert groups.price_outcome is SideGroup.DEMAND
    assert groups.down_waning is TriState.TRUE

    evidence = compute_direction_evidence(groups, SideGroup.NEUTRAL, (), PARAMS)
    # supply_exhaustion is a DEMAND-side path (8.3.3 demand table)
    assert evidence.path_states[PathName.SUPPLY_EXHAUSTION] is TriState.TRUE
    # the mirrored supply-side exhaustion path stays false
    assert evidence.path_states[PathName.DEMAND_EXHAUSTION] is TriState.FALSE
    # demand_result = OR(demand paths) AND NOT(P=supply) AND NOT(B=supply)
    assert evidence.dominance is Dominance.DEMAND
    assert PathName.SUPPLY_EXHAUSTION in evidence.dominance_paths


def test_c04_negative_signed_end_displacement_preserved():
    # Up wave with giveback ending below the start reference: the record
    # keeps signed_end_displacement < 0 (hand-built record, see API notes).
    wave = make_wave(+1, 1.0, peak=0.5, signed=-0.25, end_index=6)
    assert wave.peak_excursion > 0
    assert wave.signed_end_displacement < 0
    assert wave.giveback == pytest.approx(0.75)

    # flows into the P-group median unclamped (no silent fallback to 0)
    waves = [
        make_wave(+1, 1.0, 0.5, -0.25, end_index=6),
        make_wave(+1, 1.0, 0.5, -0.25, end_index=8),
        make_wave(-1, 1.0, 1.0, 0.5, end_index=5),
        make_wave(-1, 1.0, 1.0, 0.5, end_index=7),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.stats.med_signed_up == pytest.approx(-0.25)
    # demand requires med_up(signed) > med_down(signed): fails -> not demand
    assert groups.price_outcome is not SideGroup.DEMAND


def test_c05_activity_rate_vs_cum_effort():
    # up: 10 bars each RVOL 0.8; down: 2 bars each RVOL 1.2.
    assert effort_ratio([0.8] * 10, [1.0] * 10).value == pytest.approx(0.8)
    assert cumulative_effort([NumFeature(value=0.8)] * 10).value == (
        pytest.approx(8.0)
    )
    assert cumulative_effort([NumFeature(value=1.2)] * 2).value == (
        pytest.approx(2.4)
    )

    waves = [
        make_wave(+1, 0.8, 3.0, 2.5, end_index=20, bar_count=10, cum_effort=8.0),
        make_wave(+1, 0.8, 3.0, 2.5, end_index=22, bar_count=10, cum_effort=8.0),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=15, bar_count=2, cum_effort=2.4),
        make_wave(-1, 1.2, 1.0, 0.5, end_index=17, bar_count=2, cum_effort=2.4),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.stats.med_activity_up == pytest.approx(0.8)
    assert groups.stats.med_activity_down == pytest.approx(1.2)
    assert groups.stats.med_cum_effort_up == pytest.approx(8.0)
    assert groups.stats.med_cum_effort_down == pytest.approx(2.4)
    # down activity higher (the path rule uses the activity rate)
    assert groups.activity is ActivityGroup.DOWN_MORE_ACTIVE


# ---------------------------------------------------------------------------
# 8.3.2 B-filter unit tests
# ---------------------------------------------------------------------------

def test_filter_wrong_range_version_excluded():
    seqs = (
        make_sequence(reference_range_version=2),
        make_sequence(reference_range_id="rng-other"),
    )
    result = filter_current_sequences(seqs, make_context())
    assert result.counted == ()
    assert all(
        result.exclusion_reasons[s.sequence_id] == "range_mismatch" for s in seqs
    )
    assert result.current_valid == ()


def test_filter_stale_and_future_confirmed_bar_excluded():
    # bias_sequence_recent_bars = 20 by default
    stale = make_sequence(confirmed_bar=80)  # age 20 >= 20
    fresh = make_sequence(confirmed_bar=95)
    future = make_sequence(confirmed_bar=101)
    result = filter_current_sequences((stale, fresh, future), make_context())
    assert result.counted == (fresh,)
    assert result.exclusion_reasons[stale.sequence_id] == "confirmed_bar_stale"
    assert (
        result.exclusion_reasons[future.sequence_id] == "confirmed_bar_in_future"
    )


def test_filter_not_yet_available_excluded():
    seq = make_sequence(available_at=20.0)
    result = filter_current_sequences((seq,), make_context(decision_time=10.0))
    assert result.counted == ()
    assert result.exclusion_reasons[seq.sequence_id] == "not_yet_available"


def test_filter_owner_not_confirmed_or_invalid_excluded():
    candidate = make_sequence(owner_status=HypothesisStatus.CANDIDATE)
    invalidated = make_sequence(
        owner_status=HypothesisStatus.INVALIDATED, owner_current_valid=False
    )
    not_valid = make_sequence(owner_current_valid=False)
    ok = make_sequence()
    result = filter_current_sequences(
        (candidate, invalidated, not_valid, ok), make_context()
    )
    assert result.counted == (ok,)
    assert result.exclusion_reasons[candidate.sequence_id] == "owner_not_confirmed"
    assert result.exclusion_reasons[invalidated.sequence_id] == "owner_not_confirmed"
    assert result.exclusion_reasons[not_valid.sequence_id] == (
        "owner_not_currently_valid"
    )
    # confirmed-but-invalidated sequences stay in history only
    assert invalidated in result.history_confirmed
    assert candidate not in result.history_confirmed
    assert invalidated not in result.current_valid


def test_filter_not_evaluable_excluded():
    seq = make_sequence(evaluable=False)
    result = filter_current_sequences((seq,), make_context())
    assert result.exclusion_reasons[seq.sequence_id] == "not_evaluable"


def test_filter_dedup_by_owner_and_shared_trigger():
    # two votes from one owner closed-loop -> one vote
    a1 = make_sequence(
        evidence_owner_id="own-a", confirmation_trigger_id="trg-1", confirmed_bar=95
    )
    a2 = make_sequence(
        evidence_owner_id="own-a", confirmation_trigger_id="trg-2", confirmed_bar=96
    )
    # shared confirmation trigger across different owners -> one vote
    b1 = make_sequence(
        evidence_owner_id="own-b", confirmation_trigger_id="trg-3", confirmed_bar=95
    )
    b2 = make_sequence(
        evidence_owner_id="own-c", confirmation_trigger_id="trg-3", confirmed_bar=97
    )
    result = filter_current_sequences((a1, a2, b1, b2), make_context())
    counted_ids = {s.sequence_id for s in result.counted}
    assert counted_ids == {a1.sequence_id, b1.sequence_id}
    assert result.exclusion_reasons[a2.sequence_id] == "duplicate_owner_vote"
    assert (
        result.exclusion_reasons[b2.sequence_id] == "duplicate_confirmation_trigger"
    )
    # both are valid evidence, just deduplicated
    assert a2 in result.current_valid and b2 in result.current_valid


def test_filter_invalidated_alias_does_not_suppress_valid_evidence():
    # same owner closed-loop, one alias invalidated: the valid vote counts
    valid = make_sequence(
        evidence_owner_id="own-a",
        confirmation_trigger_id="trg-1",
        confirmed_bar=96,
    )
    invalidated_alias = make_sequence(
        evidence_owner_id="own-a",
        confirmation_trigger_id="trg-2",
        confirmed_bar=95,
        owner_current_valid=False,
    )
    result = filter_current_sequences((valid, invalidated_alias), make_context())
    assert result.counted == (valid,)
    assert (
        result.exclusion_reasons[invalidated_alias.sequence_id]
        == "owner_not_currently_valid"
    )


def test_filter_appended_evidence_cannot_extend_window():
    # appended evidence cannot rewrite confirmed_bar / available_at to
    # extend a sequence's counting window
    old = make_sequence(confirmed_bar=50)  # far outside 20 bars
    result = filter_current_sequences((old,), make_context())
    assert result.exclusion_reasons[old.sequence_id] == "confirmed_bar_stale"


# ---------------------------------------------------------------------------
# 8.3.3 tri-state dominance outcomes
# ---------------------------------------------------------------------------

def test_ne_groups_give_not_evaluable_dominance():
    groups = compute_wave_groups((), RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.NOT_EVALUABLE
    evidence = compute_direction_evidence(groups, SideGroup.DEMAND, (), PARAMS)
    assert evidence.dominance is Dominance.NOT_EVALUABLE
    assert "wave_min_completed_each_direction_not_met" in evidence.evaluation_reasons


def test_both_false_gives_neutral():
    assert finalize_dominance(TriState.FALSE, TriState.FALSE) is Dominance.NEUTRAL
    assert finalize_dominance(TriState.FALSE, TriState.NOT_EVALUABLE) is (
        Dominance.NOT_EVALUABLE
    )
    assert finalize_dominance(TriState.TRUE, TriState.NOT_EVALUABLE) is Dominance.DEMAND


def test_wave_module_off_gives_not_evaluable_with_reason():
    waves = c01_waves()
    params = WyckoffV4Params(wave_module_enabled=False)
    params.validate()
    groups = compute_wave_groups(waves, RNG, params, CURRENT_BAR)
    assert groups.activity is ActivityGroup.NOT_EVALUABLE
    assert groups.reasons == ("wave_module_disabled",)

    counted = (make_sequence(),)
    evidence = compute_direction_evidence(groups, SideGroup.DEMAND, counted, params)
    assert evidence.dominance is Dominance.NOT_EVALUABLE
    assert "wave_module_disabled" in evidence.evaluation_reasons


def test_mutual_exclusion_error_raised_when_forced():
    with pytest.raises(ConfigurationError):
        finalize_dominance(TriState.TRUE, TriState.TRUE)


def test_wave_groups_ne_when_range_not_mature():
    groups = compute_wave_groups(c01_waves(), RNG, PARAMS, current_bar=10)
    assert groups.activity is ActivityGroup.NOT_EVALUABLE
    assert "range_not_mature" in groups.reasons


def test_quality_limited_waves_excluded_from_window():
    waves = c01_waves() + [
        make_wave(+1, 5.0, 5.0, 5.0, end_index=11, quality_limited=True),
        make_wave(-1, 5.0, 5.0, 5.0, end_index=12, quality_limited=True),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.DOWN_MORE_ACTIVE  # 0.6 vs 1.2 kept


def test_wave_window_capped_at_wave_stats_max_completed():
    waves = [make_wave(-1, 1.2, 1.0, 0.5, end_index=i) for i in range(5, 13)]
    waves += [make_wave(+1, 0.6, 3.0, 2.5, end_index=i) for i in range(13, 19)]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert len(groups.stats.window_wave_ids) == PARAMS.wave_stats_max_completed
    # the latest six (all up waves) won the window
    assert groups.stats.n_down == 0


# ---------------------------------------------------------------------------
# 8.4 structure hypotheses
# ---------------------------------------------------------------------------

def _absorption_setup():
    """A=down_more_active, P=demand, B=demand (support-response counted)."""
    groups = compute_wave_groups(c01_waves(), RNG, PARAMS, CURRENT_BAR)
    counted = (make_sequence(primitive_event_ids=("fact-x",)),)
    b_group = evaluate_bias_group(counted)
    evidence = compute_direction_evidence(groups, b_group, counted, PARAMS)
    assert evidence.dominance is Dominance.DEMAND
    return groups, counted, evidence


def test_structure_phase_b_and_test_candidates():
    groups, counted, evidence = _absorption_setup()
    hyps = structure_hypotheses(
        RNG,
        groups,
        counted,
        evidence,
        (),
        PARAMS,
        CURRENT_BAR,
        unfailed_spring_shakeout=1,
        unfailed_ut_test=0,
    )
    assert hyps.phase_b_range is True
    assert hyps.spring_supply_test_candidate is True
    assert hyps.ut_demand_test_candidate is False
    # no trigger event -> accumulation entry not met
    assert hyps.accumulation_candidate is False
    assert hyps.reaccumulation_candidate is False


def test_structure_accumulation_candidate_with_recent_trigger():
    groups, counted, evidence = _absorption_setup()
    trigger = TriggerEvent(
        event_code=EventCode.SOS,
        bar_index=CURRENT_BAR - 3,
        primitive_event_ids=("trig-1",),  # disjoint from the sequence facts
    )
    hyps = structure_hypotheses(
        RNG, groups, counted, evidence, (trigger,), PARAMS, CURRENT_BAR
    )
    assert hyps.accumulation_candidate is True
    assert hyps.entry_dominance_demand is Dominance.DEMAND
    assert hyps.dedup_removed_sequence_ids == ()


def test_structure_trigger_recency_and_lps_validity():
    groups, counted, evidence = _absorption_setup()
    old_trigger = TriggerEvent(
        event_code=EventCode.SOS, bar_index=CURRENT_BAR - 10
    )  # bias_signal_recent_bars = 10: age 10 not < 10
    lps_invalid = TriggerEvent(
        event_code=EventCode.LPS, bar_index=CURRENT_BAR - 1, currently_valid=False
    )
    lps_valid = TriggerEvent(
        event_code=EventCode.LPS, bar_index=CURRENT_BAR - 1, currently_valid=True
    )
    for triggers, expected in (((old_trigger, lps_invalid), False), ((lps_valid,), True)):
        hyps = structure_hypotheses(
            RNG, groups, counted, evidence, triggers, PARAMS, CURRENT_BAR
        )
        assert hyps.accumulation_candidate is expected


def test_structure_duplicate_vote_control_ne_dominance_never_passes():
    # The SOS trigger shares primitive facts with the ONLY counted B
    # sequence: removing it makes B neutral, the absorption/boundary paths
    # collapse and the recomputed demand dominance is NOT_EVALUABLE ->
    # the entry must not pass (NE never passes).
    groups, counted, evidence = _absorption_setup()
    trigger = TriggerEvent(
        event_code=EventCode.SOS,
        bar_index=CURRENT_BAR - 3,
        primitive_event_ids=("fact-x",),  # shared with the sequence
    )
    hyps = structure_hypotheses(
        RNG, groups, counted, evidence, (trigger,), PARAMS, CURRENT_BAR
    )
    assert hyps.accumulation_candidate is False
    assert hyps.dedup_removed_sequence_ids == (counted[0].sequence_id,)
    assert hyps.entry_dominance_demand is Dominance.NOT_EVALUABLE
    # the original dominance is kept separately (not silently reused)
    assert hyps.dominance is Dominance.DEMAND


def test_structure_duplicate_vote_control_flips_to_neutral():
    # B = demand via two counted demand sequences; the trigger shares
    # facts with BOTH. After removal only the supply sequence is counted,
    # B recomputes to supply and the demand entry fails definitively.
    groups = compute_wave_groups(c01_waves(), RNG, PARAMS, CURRENT_BAR)
    counted = (
        make_sequence(
            event_code=EventCode.SUPPORT_RESPONSE_SEQUENCE,
            evidence_owner_id="own-1",
            primitive_event_ids=("fact-x",),
        ),
        make_sequence(
            event_code=EventCode.SPRING_TEST_CONFIRMED,
            evidence_owner_id="own-2",
            primitive_event_ids=("fact-y",),
        ),
        make_sequence(
            event_code=EventCode.RESISTANCE_RESPONSE_SEQUENCE,
            evidence_owner_id="own-3",
            primitive_event_ids=("fact-z",),
        ),
    )
    b_group = evaluate_bias_group(counted)
    assert b_group is SideGroup.DEMAND
    evidence = compute_direction_evidence(groups, b_group, counted, PARAMS)
    assert evidence.dominance is Dominance.DEMAND

    trigger = TriggerEvent(
        event_code=EventCode.SOS,
        bar_index=CURRENT_BAR - 3,
        primitive_event_ids=("fact-x", "fact-y"),
    )
    hyps = structure_hypotheses(
        RNG, groups, counted, evidence, (trigger,), PARAMS, CURRENT_BAR
    )
    assert hyps.accumulation_candidate is False
    assert set(hyps.dedup_removed_sequence_ids) == {
        counted[0].sequence_id,
        counted[1].sequence_id,
    }
    assert hyps.entry_dominance_demand is Dominance.NEUTRAL


def test_structure_distribution_mirror():
    # supply side mirror: P=supply, A=up_more_active, B=supply
    waves = [
        make_wave(+1, 1.2, 1.0, 0.5, end_index=6),
        make_wave(+1, 1.2, 1.0, 0.5, end_index=8),
        make_wave(-1, 0.6, 3.0, 2.5, end_index=5),
        make_wave(-1, 0.6, 3.0, 2.5, end_index=7),
    ]
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.UP_MORE_ACTIVE
    assert groups.price_outcome is SideGroup.SUPPLY
    counted = (make_sequence(event_code=EventCode.RESISTANCE_RESPONSE_SEQUENCE),)
    b_group = evaluate_bias_group(counted)
    assert b_group is SideGroup.SUPPLY
    evidence = compute_direction_evidence(groups, b_group, counted, PARAMS)
    assert evidence.path_states[PathName.SUPPLY_BOUNDARY_SUPPORTED] is TriState.TRUE
    assert evidence.path_states[PathName.DISTRIBUTION_RESPONSE] is TriState.TRUE
    assert evidence.dominance is Dominance.SUPPLY
    trigger = TriggerEvent(event_code=EventCode.SOW, bar_index=CURRENT_BAR - 2)
    hyps = structure_hypotheses(
        RNG, groups, counted, evidence, (trigger,), PARAMS, CURRENT_BAR
    )
    assert hyps.distribution_candidate is True


def test_structure_larger_context_relabeling():
    groups, counted, evidence = _absorption_setup()
    trigger = TriggerEvent(
        event_code=EventCode.SOS, bar_index=CURRENT_BAR - 3
    )
    # 50 changes require 51 closes.
    closes = [100.0 + 10.0 * i / 50 for i in range(51)]
    hyps = structure_hypotheses(
        RNG,
        groups,
        counted,
        evidence,
        (trigger,),
        PARAMS,
        CURRENT_BAR,
        larger_context_closes=closes,
        larger_context_atr=2.0,
    )
    assert hyps.larger_context is TrendState.UP
    assert hyps.accumulation_candidate is True
    assert hyps.reaccumulation_candidate is True

    # flat closes: neutral context, no relabeling
    hyps_neutral = structure_hypotheses(
        RNG,
        groups,
        counted,
        evidence,
        (trigger,),
        PARAMS,
        CURRENT_BAR,
        larger_context_closes=[100.0] * 51,
        larger_context_atr=2.0,
    )
    assert hyps_neutral.larger_context is TrendState.NEUTRAL
    assert hyps_neutral.accumulation_candidate is True
    assert hyps_neutral.reaccumulation_candidate is False


def test_larger_context_unknown_on_insufficient_data():
    assert larger_context([1.0, 2.0], 2.0, PARAMS) is TrendState.UNKNOWN
    assert larger_context([1.0] * 50, None, PARAMS) is TrendState.UNKNOWN
    assert larger_context([1.0] * 50, 0.0, PARAMS) is TrendState.UNKNOWN


def test_larger_context_up_down_classification():
    up = [100.0 + 10.0 * i / 50 for i in range(51)]
    down = [110.0 - 10.0 * i / 50 for i in range(51)]
    assert larger_context(up, 2.0, PARAMS) is TrendState.UP
    assert larger_context(down, 2.0, PARAMS) is TrendState.DOWN
    # displacement below the ATR threshold -> neutral
    small = [100.0 + 2.0 * i / 50 for i in range(51)]  # 2.0 = 1 ATR < 3
    assert larger_context(small, 2.0, PARAMS) is TrendState.NEUTRAL
    # large displacement but low ER -> neutral
    choppy = [100.0] * 51
    choppy[25] = 130.0
    assert larger_context(choppy, 2.0, PARAMS) is TrendState.NEUTRAL

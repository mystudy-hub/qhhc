"""Regression cases for the V4 report's lifecycle boundary failures."""
from dataclasses import replace

import pytest

from cta_lab.wyckoff_v4.engine import WyckoffV4Engine
from cta_lab.wyckoff_v4.indicators import SwingKind, SwingPoint
from cta_lab.wyckoff_v4.params import CreekMode, WyckoffV4Params
from cta_lab.wyckoff_v4.types import EventCode as E, HypothesisStatus as H
from .scenarios import Scenario, bar, contract, event, feature, rng, T0
from .test_engine_paths import stopping_path, mirror


@pytest.mark.parametrize("down", [False, True])
def test_new_path_b_absorption_binds_completion_range_without_changing_onset(down):
    engine = WyckoffV4Engine(contract())
    for i in range(62):
        high, low = 100.5, 99.5
        if 30 <= i < 59 and i % 10 == 0:
            low = 98
        if 30 <= i < 59 and i % 10 == 5:
            high = 102
        if i >= 59:
            low = 98
        b = bar(i, 100, high=high, low=low, volume=220 if i >= 59 else 100)
        publication = engine.process_bar(mirror(b) if down else b)
        if i == 59:
            assert engine.front_range.established_bar == 59
    fact = next(e for e in publication.events if e.event_type is E.EFFORT_RESULT_LOW_PROGRESS)
    assert fact.onset_snapshot.asof_bar == 58
    assert fact.reference_range_id is fact.onset_snapshot.range_id is None
    owner = next(h for h in engine.lifecycle.hypotheses.values() if h.hypothesis_type == "ABSORPTION")
    assert owner.data["source"] == fact
    assert owner.data["source"].onset_snapshot.range_id is None
    assert (owner.reference_range_id, owner.reference_range_version) == (engine.front_range.range_id, engine.front_range.version)
    assert (owner.frozen_S, owner.frozen_R, owner.frozen_epsilon) == (engine.front_range.S, engine.front_range.R, engine.front_range.epsilon)
    assert owner.direction == (-1 if down else 1)
    assert len(engine.publications) == 62


def test_absorption_fact_with_an_older_onset_range_binds_current_foreground():
    s = Scenario(rng(s=100, r=110, eps=.1))
    old_range = replace(s.range, range_id="OLD", version=2, S=90, R=95)
    fact = event(E.EFFORT_RESULT_LOW_PROGRESS, 0, old_range)
    s.step(100, [fact], high=100.2, low=99.9)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "ABSORPTION")
    assert owner.reference_range_id == s.range.range_id
    assert owner.data["source"].reference_range_id == "OLD"
    assert owner.frozen_S == 100


def test_nullable_structure_deadline_survives_gaps_and_generic_timeout():
    s = Scenario(rng(s=100, r=110, eps=.1))
    s.step(105)
    owner = s.lifecycle._new("STRUCTURE", 1, event(E.SOS, 0, s.range), None, last_trigger=0)
    s.lifecycle.pause_gap()
    assert owner.confirmation_deadline_bar is None
    for _ in range(12):
        s.step(105)
    assert owner.status is H.CANDIDATE
    # A genuine confirmation window is still paused by the same gap.
    candidate = s.lifecycle._new("SPRING", 1, event(E.SPRING_LOW_VOLUME, 12, s.range, metrics={"extreme":99.5}), 20, branch=E.SPRING_LOW_VOLUME.value)
    s.lifecycle.pause_gap()
    assert candidate.confirmation_deadline_bar == 21


def pattern(d, *, deep=False):
    return (E.SHAKEOUT_RECLAIM if deep else E.SPRING_LOW_VOLUME) if d == 1 else (E.UPTHRUST_DEEP_REJECTION if deep else E.UT_FEATURE)


def pattern_step(s, d, code, extreme, *, effort=1):
    i = len(s.features)
    return s.step(100.3 if d == 1 else 109.7,
                  [event(code, i, s.range, metrics={"extreme": extreme, "effort": effort})],
                  high=100.4 if d == 1 else extreme, low=extreme if d == 1 else 109.6)


@pytest.mark.parametrize("d", [1, -1])
@pytest.mark.parametrize("different_branch", [False, True])
def test_new_branch_or_deeper_pattern_replaces_only_pending_episode(d, different_branch):
    s = Scenario(rng(s=100, r=110, eps=.1))
    first_extreme = 99.5 if d == 1 else 110.5
    pattern_step(s, d, pattern(d), first_extreme)
    kind = "SPRING" if d == 1 else "UT"
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == kind)
    new_code = pattern(d, deep=different_branch)
    new_extreme = first_extreme if different_branch else first_extreme - d * .2
    pattern_step(s, d, new_code, new_extreme, effort=2)
    candidates = [h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == kind and h.status is H.CANDIDATE]
    assert old.status is H.EXPIRED
    assert old.state_transition_history[-1].reason == "new_pattern_replaced"
    assert old.data["extreme"] == first_extreme
    assert len(candidates) == 1
    new = candidates[0]
    assert new.hypothesis_id != old.hypothesis_id
    assert new.created_bar == 1 and new.data["extreme"] == new_extreme
    assert new.data["branch"] == new_code.value and new.data["effort"] == 2
    assert new.confirmation_deadline_bar == 14
    s.lifecycle.validate_state()


@pytest.mark.parametrize("d", [1, -1])
def test_same_branch_at_epsilon_only_adds_fact_without_changing_frozen_episode(d):
    s = Scenario(rng(s=100, r=110, eps=.1))
    first_extreme = 99.5 if d == 1 else 110.5
    pattern_step(s, d, pattern(d), first_extreme)
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == ("SPRING" if d == 1 else "UT"))
    deadline = old.confirmation_deadline_bar
    pattern_step(s, d, pattern(d), 99.4 if d == 1 else 110.6, effort=2)
    assert old.status is H.CANDIDATE
    assert old.data["extreme"] == first_extreme and old.data["effort"] == 1
    assert old.confirmation_deadline_bar == deadline
    assert len(old.supporting_evidence_ids) == 1


def test_reclaimed_deep_wick_replaces_pending_low_spring_without_wick_invalidation():
    s = Scenario(rng(s=100, r=110, eps=.1))
    pattern_step(s, 1, E.SPRING_LOW_VOLUME, 99.5)
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "SPRING")
    pattern_step(s, 1, E.SHAKEOUT_RECLAIM, 96)
    new = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "SPRING" and h.status is H.CANDIDATE)
    events = s.step(102, [event(E.SOS, 2, s.range)], high=102.1, low=101.9)
    assert old.status is H.EXPIRED and old.state_transition_history[-1].reason == "new_pattern_replaced"
    assert new.data["extreme"] == 96 and new.status is H.CANDIDATE
    assert E.SPRING_SOS_SEQUENCE not in {e.event_type for e in events}


@pytest.mark.parametrize("d", [1, -1])
def test_confirmed_episode_is_not_replaced_by_new_deep_reclaim(d):
    s = Scenario(rng(s=100, r=110, eps=.1))
    pattern_step(s, d, pattern(d), 99.5 if d == 1 else 110.5)
    kind = "SPRING" if d == 1 else "UT"
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == kind)
    s.step(105, [event(E.SOS if d == 1 else E.SOW, 1, s.range)], high=105.1, low=104.9)
    assert old.status is H.CONFIRMED
    pattern_step(s, d, pattern(d, deep=True), 96 if d == 1 else 114)
    assert old.status is H.CONFIRMED
    assert len([h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == kind and h.status is H.CANDIDATE]) == 1


@pytest.mark.parametrize("d", [1, -1])
def test_terminal_episode_stays_terminal_when_later_pattern_is_created(d):
    s = Scenario(rng(s=100, r=110, eps=.1))
    pattern_step(s, d, pattern(d), 99.5 if d == 1 else 110.5)
    old = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == ("SPRING" if d == 1 else "UT"))
    s.step(99.3 if d == 1 else 110.7)
    assert old.status is H.INVALIDATED
    pattern_step(s, d, pattern(d), 99.5 if d == 1 else 110.5)
    assert old.status is H.INVALIDATED
    assert len([h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == old.hypothesis_type and h.status is H.CANDIDATE]) == 1


@pytest.mark.parametrize("d", [1, -1])
def test_short_test_window_keeps_entire_sos_sow_confirmation_window(d):
    p = replace(WyckoffV4Params(), test_window_max=5)
    s = Scenario(rng(s=100, r=110, eps=.1), p)
    pattern_step(s, d, pattern(d), 99.5 if d == 1 else 110.5)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == ("SPRING" if d == 1 else "UT"))
    assert owner.confirmation_deadline_bar == 10
    for _ in range(8):
        s.step(100.7 if d == 1 else 109.3, high=100.8 if d == 1 else 109.4, low=100.6 if d == 1 else 109.2)
    events = s.step(102 if d == 1 else 108, [event(E.SOS if d == 1 else E.SOW, 9, s.range)], high=102.1 if d == 1 else 108.1, low=101.9 if d == 1 else 107.9)
    assert owner.status is H.CONFIRMED
    assert (E.SPRING_SOS_SEQUENCE if d == 1 else E.UT_SOW_SEQUENCE) in {e.event_type for e in events}


@pytest.mark.parametrize("code", [E.SPRING_HIGH_VOLUME, E.SHAKEOUT_RECLAIM])
def test_test_only_spring_branch_does_not_extend_to_sos_window(code):
    s = Scenario(rng(s=100, r=110, eps=.1), replace(WyckoffV4Params(), test_window_max=5))
    pattern_step(s, 1, code, 99.5)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "SPRING")
    assert owner.confirmation_deadline_bar == 8


@pytest.mark.parametrize("d", [1, -1])
def test_range_only_blocks_all_internal_lps_sources(d):
    s = Scenario(rng(s=100, r=110, eps=.1), replace(WyckoffV4Params(), creek_mode=CreekMode.RANGE_ONLY))
    facts = [event(E.SOS if d == 1 else E.SOW, 0, s.range), event(E.INTERNAL_JOC if d == 1 else E.INTERNAL_FTI, 0, s.range, level=105)]
    s.step(105, facts)
    assert not any(h.hypothesis_type == "LPS" for h in s.lifecycle.hypotheses.values())


@pytest.mark.parametrize("d", [1, -1])
def test_internal_sos_support_uses_only_post_origin_in_range_available_swings(d):
    range_ = replace(rng(s=100, r=110, eps=.1), origin_bar=50)
    s = Scenario(range_)
    lc = s.lifecycle
    fs = [feature(i, 105) for i in range(61)]
    lc.begin(fs, range_, fs[-1].bar.bar_end, "T:60")
    kind = SwingKind.LOW if d == 1 else SwingKind.HIGH
    # Every invalid point is more recently confirmed than the usable one.
    prices = [103 if d == 1 else 107, 104 if d == 1 else 106, 115 if d == 1 else 95, 106 if d == 1 else 104]
    swings = tuple(SwingPoint(kind, index, price, T0, confirmed, usable) for index,price,confirmed,usable in
                   [(51,prices[0],54,55),(5,prices[1],55,56),(52,prices[2],56,57),(53,prices[3],57,58),(54,104 if d == 1 else 106,60,61)])
    lc.ingest([event(E.SOS if d == 1 else E.SOW, 60, range_)], swings)
    owner = next(h for h in lc.hypotheses.values() if h.hypothesis_type == "LPS")
    assert owner.data["level"] == prices[0]


@pytest.mark.parametrize("down", [False, True])
def test_too_wide_ar_preserves_climax_st_response_and_phase_a(down):
    bars = stopping_path()
    bars[61] = replace(bars[61], high=130)
    engine = WyckoffV4Engine(contract())
    publications = engine.replay([mirror(b) for b in bars] if down else bars)
    owner = next(h for h in engine.lifecycle.hypotheses.values() if h.hypothesis_type == "CLIMAX")
    assert owner.status is H.CONFIRMED
    assert owner.data["range_rejected"] == "width_too_large"
    assert owner.data["ar_bar"] == 63 and owner.data["test_confirmed_q"] == 68
    assert E.ST_OBSERVED in {e.event_type for e in publications[68].events}
    assert (E.BC_STOPPING_SEQUENCE if down else E.SC_STOPPING_SEQUENCE) in {e.event_type for e in publications[69].events}
    assert not engine.lifecycle.path_a_ranges
    assert any(p["phase"] == "A" for p in publications[69].snapshot["phase_supported_by_rules"])


def renewal_path(d):
    s = Scenario(rng(s=100, r=110, eps=.1))
    pattern_step(s, d, pattern(d), 99.5 if d == 1 else 110.5)
    s.step(105, [event(E.SOS if d == 1 else E.SOW, 1, s.range)], high=105.1, low=104.9)
    episode = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == ("SPRING" if d == 1 else "UT"))
    return s, episode


def finish_pullback(s, d):
    for _ in range(2):
        s.step(104.8 if d == 1 else 105.2, high=105 if d == 1 else 109, low=101 if d == 1 else 105, ns=.5, volume=.5)
    return s.step(105.2 if d == 1 else 104.8, high=105.3 if d == 1 else 105.3, low=104.7, ns=.6, volume=1, net=.4*d)


@pytest.mark.parametrize("d", [1, -1])
def test_selected_lps_renews_same_direction_confirmed_test_without_new_test(d):
    s, episode = renewal_path(d)
    assert episode.valid_through_bar == 21
    opposite = replace(episode, hypothesis_id="opposite", direction=-d, data=dict(episode.data))
    other_version = replace(episode, hypothesis_id="other-version", reference_range_version=2, data=dict(episode.data))
    s.lifecycle.hypotheses[opposite.hypothesis_id] = opposite
    s.lifecycle.hypotheses[other_version.hypothesis_id] = other_version
    events = finish_pullback(s, d)
    assert (E.LPS if d == 1 else E.LPSY) in {e.event_type for e in events}
    assert not {E.TEST_OBSERVED, E.UT_TEST_OBSERVED} & {e.event_type for e in events}
    assert episode.valid_through_bar == 24 and episode.data["last_evidence"] == 4
    assert opposite.valid_through_bar == other_version.valid_through_bar == 21


@pytest.mark.parametrize("d", [1, -1])
def test_unselected_lps_fact_does_not_renew_confirmed_test(d):
    s, episode = renewal_path(d)
    for _ in range(2):
        s.step(104.8 if d == 1 else 105.2, high=105 if d == 1 else 109, low=101 if d == 1 else 105, ns=.5, volume=.5)
    candidate = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS" and h.direction == d)
    s.features.append(feature(4, 105.2 if d == 1 else 104.8, high=105.3, low=104.7, ns=.6, volume=1, net=.4*d))
    lc = s.lifecycle
    lc.begin(s.features, s.range, s.features[-1].bar.bar_end, "T:4")
    lc.invalidate_prices()
    assert lc._lps(candidate, selected=False)
    assert candidate.status is H.EXPIRED and candidate.state_transition_history[-1].reason == "not_selected_same_bar"
    assert (E.LPS if d == 1 else E.LPSY) in {e.event_type for e in lc.new_events}
    assert episode.valid_through_bar == 21


@pytest.mark.parametrize("d", [1, -1])
def test_new_lps_does_not_revive_a_test_episode_expired_at_transaction_start(d):
    s, episode = renewal_path(d)
    episode.valid_through_bar = 3
    events = finish_pullback(s, d)
    assert (E.LPS if d == 1 else E.LPSY) in {e.event_type for e in events}
    assert episode.status is H.EXPIRED and episode.valid_through_bar == 3
    assert episode.state_transition_history[-1].reason == "current_validity_ended"

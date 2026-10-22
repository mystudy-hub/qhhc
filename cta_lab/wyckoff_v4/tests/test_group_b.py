"""Section 11.2 B01–B18: detector and lifecycle acceptance paths."""
from dataclasses import replace

from cta_lab.wyckoff_v4.engine import WyckoffV4Engine
from cta_lab.wyckoff_v4.events import EventDetector
from cta_lab.wyckoff_v4.output import ContextSnapshot
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.types import EventCode as E, HypothesisStatus as H, RangeState as R, TrendState
from .scenarios import Scenario, bar, contract, event, feature, rng, T0


def breakout(s, close=100.3, strength="ordinary"):
    return s.step(close, [event(E.BREAKOUT_UP, len(s.features), s.range, strength=strength)], low=100.25)


def test_b01_downward_outside_path_has_no_phase_e():
    engine = WyckoffV4Engine(contract())
    for i in range(60):
        engine.process_bar(bar(i, 100, volume=100))
    engine.front_range = rng()
    last = engine.detector.ring.get(59)
    engine.detector.ring._items[-1] = replace(last, range_id=engine.front_range.range_id,
                                             range_version=1, S=90, R=100, epsilon=.2, range_age=90)
    for i in range(20):
        result = engine.process_bar(bar(60 + i, 130 - i, volume=100))
    assert all(phase["phase"] != "E" for phase in result.snapshot["phase_supported_by_rules"])


def test_b02_low_volume_ordinary_breakout_hold():
    s = Scenario()
    breakout(s)
    s.step(100.4, low=100.25)
    s.step(100.5, low=100.25)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    assert parent.status is H.CONFIRMED
    assert s.range.state is R.EXIT_UP_ACCEPTED
    assert s.lifecycle.active_acceptance().primary_basis == "hold"


def test_b03_recross_preserves_origin_and_deadline():
    s = Scenario()
    breakout(s)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    s.step(100.1, low=100)
    s.step(100.4, [event(E.BREAKOUT_UP, 2, s.range)], low=100.25)
    s.step(100.5, low=100.25)
    assert parent.status is H.CANDIDATE
    s.step(100.6, low=100.25)
    assert parent.status is H.CONFIRMED
    assert parent.created_bar == 0 and parent.confirmation_deadline_bar == 12
    assert len([h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT"]) == 1


def detector_context(detector, index, range_, trend=TrendState.NEUTRAL, dominance="neutral"):
    detector.ring.append(ContextSnapshot(f"S:{index}", index, T0, trend, range_.range_id,
                                         range_.version, range_.S, range_.R, range_.epsilon, 30, dominance))


def test_b04_first_three_atr_undercut_is_deep():
    r = rng(s=100, r=110, eps=.1)
    detector = EventDetector(WyckoffV4Params())
    detector_context(detector, 0, r)
    fs = [feature(0, 101), feature(1, 99, open=100, high=101, low=97)]
    facts = detector.detect(fs, r, (), fs[-1].bar.bar_end)
    assert {E.SUPPORT_UNDERCUT, E.DEEP_UNDERCUT} <= {e.event_type for e in facts}
    assert next(iter(detector.watches.values())).deep


def test_b05_supply_created_on_p_does_not_authorize_utad():
    r = rng()
    detector = EventDetector(WyckoffV4Params())
    detector_context(detector, 0, r, dominance="neutral")
    fs = [feature(0, 99), feature(1, 100.5, open=99.8, high=101, low=99.7)]
    detector.detect(fs, r, (), fs[-1].bar.bar_end)
    detector_context(detector, 1, r, dominance="supply")
    fs.append(feature(2, 99.5, open=100.5, high=100.6, low=99.4))
    facts = detector.detect(fs, r, (), fs[-1].bar.bar_end)
    ut = next(e for e in facts if e.event_type is E.UT_FEATURE)
    s = Scenario(r)
    s.step(99.5, [ut])
    assert ut.onset_snapshot.dominance == "neutral"
    assert not any(h.hypothesis_type == "UTAD" for h in s.lifecycle.hypotheses.values())


def test_b06_three_bar_climax_reads_p_minus_one():
    p = WyckoffV4Params()
    detector = EventDetector(p)
    fs = [feature(i, 100, high=101, low=99) for i in range(20)]
    fs.extend([feature(20, 98, open=100, high=101, low=97, volume=2.5),
               feature(21, 97, open=98, high=98.5, low=96.5, volume=2.5),
               feature(22, 98, open=97, high=99, low=96.5, volume=2)])
    for i, trend in ((19, TrendState.DOWN), (20, TrendState.NEUTRAL), (21, TrendState.NEUTRAL)):
        detector.ring.append(ContextSnapshot(f"S:{i}", i, T0, trend))
    facts = detector.detect(fs, None, (), fs[-1].bar.bar_end)
    cluster = next(e for e in facts if e.event_type is E.SC_CLUSTER_FEATURE)
    assert cluster.pattern_start == 20 and cluster.onset_snapshot.asof_bar == 19


def test_b07_absorption_creation_bar_is_included_in_failure_count():
    s = Scenario(rng(s=100, r=110, eps=.1))
    s.step(99.8, [event(E.EFFORT_RESULT_NARROW_BAR, 0, s.range)], high=100.2, low=99.7)
    owner = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "ABSORPTION" and h.direction == 1)
    assert owner.failure_inside_streak == 1
    s.step(99.7, high=99.9, low=99.6)
    assert owner.status is H.INVALIDATED


def spring(s):
    s.step(100.5, [event(E.SPRING_LOW_VOLUME, 0, s.range, metrics={"extreme": 99.5, "direction": 1})])
    s.step(100.6)
    s.step(100.7)


def test_b08_bearish_test_then_response():
    s = Scenario(rng(s=100, r=110, eps=.1))
    spring(s)
    observed = s.step(100.5, open=100.8, high=100.9, low=100.1, volume=.5)
    assert E.TEST_OBSERVED in {e.event_type for e in observed}
    confirmed = s.step(100.91, open=100.5, high=101, low=100.4)
    assert {E.TEST_RESPONSE_CONFIRMED, E.SPRING_TEST_CONFIRMED} <= {e.event_type for e in confirmed}


def test_b09_expired_breakout_cascades_before_late_lps_response():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    breakout(s, 105)
    for _ in range(12):
        s.step(104.8, high=105, low=100.3, ns=.5, volume=.5)
    assert all(h.status is H.EXPIRED for h in s.lifecycle.hypotheses.values())
    events = s.step(105.2, high=105.3, low=104.7, net=.4)
    assert E.LPS not in {e.event_type for e in events}


def lps_path(s, source=E.BREAKOUT_UP, source_close=105, level=None):
    s.step(source_close, [event(source, 0, s.range, level=level, strength="joc")], high=source_close + .1, low=source_close - .1)
    s.step(source_close - .2, high=source_close, low=level if level is not None else 100.3, ns=.5, volume=.5)
    s.step(source_close - .2, high=source_close, low=level if level is not None else 100.3, ns=.5, volume=.5)
    return s.step(source_close + .2, high=source_close + .3, low=source_close - .3, ns=.6, volume=1, net=.4)


def test_b10_lps_confirmation_precedes_close_new_high_reset():
    s = Scenario()
    events = lps_path(s)
    assert E.LPS in {e.event_type for e in events}


def test_b11_complete_pullback_duration_includes_equal_lows():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    breakout(s, 105)
    for _ in range(4):
        s.step(104.8, high=105, low=100.3, ns=.5, volume=.5)
    events = s.step(105.2, high=105.3, low=104.7, net=.4)
    lps = next(e for e in events if e.event_type is E.LPS)
    assert lps.metrics["q"] == 1 and lps.metrics["b"] == 1
    owner = s.lifecycle.hypotheses[lps.evidence_owner_id]
    assert owner.confirmed_bar - owner.data["b"] == 4


def test_b12_lps_feedback_creates_acceptance_before_hold():
    s = Scenario(params=replace(WyckoffV4Params(), breakout_hold_closes=100))
    events = lps_path(s)
    assert E.LPS in {e.event_type for e in events}
    assert s.lifecycle.active_acceptance().primary_basis == "pullback"
    assert s.range.state is R.EXIT_UP_ACCEPTED


def test_b13_internal_lps_price_loss_retains_fact_and_revokes_phase():
    s = Scenario(rng(s=100, r=110, eps=.1))
    events = lps_path(s, source=E.INTERNAL_JOC, source_close=105.5, level=105.1)
    lps = next(e for e in events if e.event_type is E.LPS)
    assert lps.sub_type == "LPS_INTERNAL"
    s.step(104.99, low=104.9, high=105)
    assert s.lifecycle.hypotheses[lps.evidence_owner_id].status is H.INVALIDATED
    assert not s.lifecycle.phase_d
    assert lps in s.lifecycle.events


def test_b14_breakout_and_sos_produce_one_lps_source():
    s = Scenario()
    s.step(105, [event(E.SOS, 0, s.range), event(E.BREAKOUT_UP, 0, s.range)], low=104.9)
    lps = [h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "LPS"]
    assert len(lps) == 1 and lps[0].source_kind == "external_breakout"


def test_b15_ut_lower_high_test_then_down_response():
    s = Scenario()
    s.step(99.5, [event(E.UT_FEATURE, 0, s.range, metrics={"extreme": 101, "direction": -1})])
    s.step(99.4)
    s.step(99.3)
    events = s.step(99.8, open=99.6, high=100, low=99.5, volume=.5)
    assert E.UT_TEST_OBSERVED in {e.event_type for e in events}
    events = s.step(99.49, open=99.8, high=99.9, low=99.4)
    assert E.UT_TEST_CONFIRMED in {e.event_type for e in events}


def test_b16_phase_d_does_not_confirm_without_followthrough():
    s = Scenario()
    lps_path(s)
    for _ in range(10):
        s.step(105.2, high=105.3, low=105.1)
    assert all(phase["status"] == "candidate" for phase in s.lifecycle.phase_d.values())


def test_b17_late_hold_does_not_resurrect_expired_attempt():
    s = Scenario()
    breakout(s)
    for _ in range(10):
        s.step(100.2, low=100.2)
    s.step(100.3, low=100.2)
    s.step(100.4, low=100.2)
    parent = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "BREAKOUT")
    assert parent.status is H.EXPIRED
    s.step(100.5, low=100.2)
    a = s.lifecycle.active_acceptance()
    assert a.mode.value == "late_hold" and a.source_hypothesis_id is None
    assert parent.status is H.EXPIRED and parent.hypothesis_id in a.prior_attempt_ids


def test_b18_ar_2_5_atr_is_insufficient():
    s = Scenario()
    s.step(100, [event(E.SC_FEATURE, 0, metrics={"extreme": 100, "direction": 1, "effort": 3})])
    s.step(102.4, high=102.5)
    s.step(102.2, high=102.3)
    s.step(102, high=102.1)
    h = next(h for h in s.lifecycle.hypotheses.values() if h.hypothesis_type == "CLIMAX")
    assert "ar_bar" not in h.data

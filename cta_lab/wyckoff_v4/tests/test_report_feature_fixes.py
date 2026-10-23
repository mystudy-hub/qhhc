"""Report regressions: calibration invariants and OI availability views."""
from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from cta_lab.wyckoff_v4.engine import WyckoffV4Engine
from cta_lab.wyckoff_v4.events import EventDetector
from cta_lab.wyckoff_v4.indicators import IndicatorState, NotEvaluableReason as N, oi_z_from_features
from cta_lab.wyckoff_v4.params import PARAMETER_VERSION, RULE_VERSION, WyckoffV4Params
from cta_lab.wyckoff_v4.types import EventCode as E
from .scenarios import bar, contract, feature, rng


@pytest.mark.parametrize("strong,ordinary,valid,invalid", [
    ("strong_break_min_rvol", "sos_sow_min_rvol", 1.4, 1.39),
    ("strong_break_min_ns", "sos_sow_min_ns", 1.3, 1.29),
    ("joc_min_cp", "sos_min_cp", .7, .69),
    ("fti_max_cp", "sow_max_cp", .3, .31),
])
def test_strong_break_thresholds_preserve_sos_sow_implication(strong, ordinary, valid, invalid):
    params = WyckoffV4Params()
    replace(params, **{strong: valid}).validate()
    with pytest.raises(ValueError, match=rf"{strong}.*{ordinary}"):
        replace(params, **{strong: invalid}).validate()


def test_versions_match_the_revised_rules():
    params = WyckoffV4Params()
    assert params.rule_version == RULE_VERSION == "4.1"
    assert params.parameter_version == PARAMETER_VERSION == "v4.1-default-unvalidated"
    with pytest.raises(ValueError, match="rule_version"):
        replace(params, rule_version="4.0").validate()


@pytest.mark.parametrize("direction", [1, -1])
def test_equal_strong_and_ordinary_thresholds_emit_associated_strength(direction):
    params = replace(WyckoffV4Params(), strong_break_min_rvol=1.4,
                     strong_break_min_ns=1.3, joc_min_cp=.7, fti_max_cp=.3)
    params.validate()
    previous_close, close, cp = (100, 101, .7) if direction == 1 else (90, 89, .3)
    previous = feature(0, previous_close)
    current = feature(1, close, open=previous_close, rvol=1.4, ns=1.3, net=direction * .5)
    current = replace(current, cp=replace(current.cp, value=cp, reason=None))
    detector = EventDetector(params)
    detector._features, detector._t, detector._rng = [previous, current], 1, rng()
    detector._now, detector._new = current.bar.bar_end, []
    detector._strength(direction, ())
    assert any(e.event_type is (E.SOS if direction == 1 else E.SOW) for e in detector._new)
    assert any(e.event_type is (E.BREAKOUT_UP if direction == 1 else E.BREAKOUT_DOWN)
               and e.strength == ("joc" if direction == 1 else "fti") for e in detector._new)


def oi_state(*, method="sum_raw", missing=None, late=None, constant=False):
    params = replace(WyckoffV4Params(), oi_window=2)
    state = IndicatorState(params, oi_method=method)
    for i in range(6):
        b = bar(i, 100)
        if i != missing:
            b = replace(b, open_interest=100 if constant else 100 + i,
                        oi_available_at=b.bar_end + (timedelta(hours=1) if i == late else timedelta(0)))
        state.advance(b)
    return state


def emitted_window(state, start=3, now=None):
    detector = EventDetector(state._params)
    detector._features = list(state.features)
    detector._t, detector._now, detector._new = 5, now or bar(5, 100).bar_end, []
    return detector._emit(E.EFFORT_RESULT_LOW_PROGRESS, start)


@pytest.mark.parametrize("kwargs,start,reason", [
    ({"method": ""}, 1, N.OI_METHOD_UNKNOWN),
    ({}, 1, N.OI_HISTORY_INSUFFICIENT),
    ({"missing": 4}, 3, N.OI_MISSING),
    ({"missing": 2}, 3, N.OI_MISSING),
    ({"late": 4}, 3, N.OI_NOT_YET_AVAILABLE),
    ({"late": 2}, 3, N.OI_NOT_YET_AVAILABLE),
    ({"constant": True}, 3, N.OI_ZERO_SCALE),
    ({"constant": True, "late": 2}, 3, N.OI_NOT_YET_AVAILABLE),
])
def test_multibar_event_uses_fixed_oi_reason_codes(kwargs, start, reason):
    state = oi_state(**kwargs)
    occurrence = emitted_window(state, start)
    assert occurrence.metrics["oiz"] is None
    assert occurrence.metrics["oi_reason"] is reason
    if kwargs.get("constant") and "late" in kwargs:
        later = emitted_window(state, start, now=bar(5, 100).bar_end + timedelta(hours=2))
        assert later.metrics["oi_reason"] is N.OI_ZERO_SCALE


def test_window_availability_at_equality_and_later_reads_preserves_history():
    state = oi_state(late=4)
    original = asdict(state.features[4].oi)
    now = bar(5, 100).bar_end
    raw = state.oi_z_window(3, 5)
    assert raw.num.value == 3
    assert raw.realtime_view(now).num.reason is N.OI_NOT_YET_AVAILABLE
    assert state.oi_z_window(3, 5, decision_time=now).num.reason is N.OI_NOT_YET_AVAILABLE
    assert state.oi_z_window(3, 5, decision_time=raw.available_at).num.value == 3
    assert state.features[4].oi.realtime_view(raw.available_at).d_oi.value == 1
    assert asdict(state.features[4].oi) == original
    assert oi_z_from_features(state.features, 5, 4, 2).num.reason is N.WINDOW_INVALID


def test_known_missing_precedes_late_and_declared_method_precedes_missing():
    state = oi_state(missing=4, late=2)
    assert emitted_window(state).metrics["oi_reason"] is N.OI_MISSING
    unknown = oi_state(method="", missing=4)
    assert emitted_window(unknown).metrics["oi_reason"] is N.OI_METHOD_UNKNOWN


def test_single_bar_future_zero_scale_is_hidden_until_its_inputs_arrive():
    state = oi_state(constant=True, late=4)
    oi = state.features[4].oi
    assert oi.oi_z.reason is N.OI_ZERO_SCALE
    current = oi.realtime_view()
    assert current.oi_z.reason is N.OI_NOT_YET_AVAILABLE
    assert current.oi_quadrant_reason is N.OI_NOT_YET_AVAILABLE
    assert oi.realtime_view(oi.oi_z_available_at).oi_z.reason is N.OI_ZERO_SCALE


def test_engine_multibar_oi_recovers_when_a_prior_bar_oi_arrives():
    engine = WyckoffV4Engine(replace(contract(), oi_method="sum_raw"),
                             replace(WyckoffV4Params(), oi_window=2))
    original = None
    for i in range(62):
        b = bar(i, 100, volume=3 if i >= 60 else 1)
        b = replace(b, open_interest=100 + i,
                    oi_available_at=b.bar_end + (timedelta(minutes=5) if i == 60 else timedelta(0)))
        publication = engine.process_bar(b)
        if i == 60:
            original = publication.to_json()
            assert publication.snapshot["indicators"]["oi"]["oi_z"]["reason"] == N.OI_NOT_YET_AVAILABLE.value
    occurrence = next(e for e in publication.events if e.event_type is E.EFFORT_RESULT_LOW_PROGRESS)
    assert occurrence.metrics["oiz"] == 3
    assert occurrence.metrics["oi_reason"] is None
    assert engine.publications[60].to_json() == original


def test_engine_unknown_oi_reason_is_consistent_for_multibar_events():
    engine = WyckoffV4Engine(contract())
    for i in range(62):
        publication = engine.process_bar(bar(i, 100, volume=3 if i >= 60 else 1))
    occurrence = next(e for e in publication.events if e.event_type is E.EFFORT_RESULT_LOW_PROGRESS)
    assert occurrence.metrics["oi_reason"] is N.OI_METHOD_UNKNOWN
    assert publication.snapshot["indicators"]["oi"]["oi_z"]["reason"] == N.OI_METHOD_UNKNOWN.value


@pytest.mark.parametrize("delayed_decision", [False, True])
def test_engine_oi_snapshot_uses_actual_decision_time(delayed_decision):
    delay = timedelta(minutes=2)
    engine = WyckoffV4Engine(replace(contract(), oi_method="sum_raw"),
                             replace(WyckoffV4Params(), oi_window=2),
                             compute_delay=timedelta(0) if delayed_decision else delay)
    for i in range(4):
        b = bar(i, 100)
        b = replace(b, open_interest=100+i,
                    oi_available_at=b.bar_end + (timedelta(minutes=1) if i == 3 else timedelta(0)))
        publication = engine.process_bar(b, decision_time=b.bar_end+delay if delayed_decision else None)
    assert publication.snapshot["indicators"]["oi"]["oi_z"] == {"value": 1.0, "reason": None}
    assert publication.snapshot["indicators"]["oi"]["d_oi"]["value"] == 1


def test_wave_oi_view_hides_future_values_and_later_restores_them():
    engine = WyckoffV4Engine(replace(contract(), oi_method="sum_raw"),
                             replace(WyckoffV4Params(), atr_period=2, oi_window=2))
    prices = [100, 99.8, 97, 97.2, 99, 98.8, 98.5, 97.1, 96.8]
    bounds = [(100.5, 99.5), (100.4, 99.4), (99.9, 96.9), (98.6, 96.6),
              (99.1, 98.4), (99.2, 98.6), (98.9, 98.3), (97.5, 96.9), (97, 96.4)]
    opens = [100, 100, 99.5, 97, 98.5, 98.8, 98.4, 97, 96.6]
    for i, close in enumerate(prices):
        b = bar(i, close, open=opens[i], high=bounds[i][0], low=bounds[i][1], volume=100)
        publication = engine.process_bar(replace(b, open_interest=100 + i,
                                                oi_available_at=b.bar_end + timedelta(hours=1)))
    wave = engine.waves.completed_waves[-1]
    original = asdict(wave)
    frozen_publication = publication.to_json()
    assert wave.wave_oiz.num.value == 4
    current = wave.realtime_view(bar(8, 0).bar_end)
    assert current.wave_oiz.num.reason is N.OI_NOT_YET_AVAILABLE
    assert current.wave_oiz.available_at == wave.wave_oiz.available_at
    assert wave.realtime_view(wave.wave_oiz.available_at).wave_oiz.num.value == 4
    assert asdict(wave) == original
    engine.append_available_oi(wave.wave_oiz.available_at + timedelta(hours=1))
    assert publication.to_json() == frozen_publication

from dataclasses import replace
import math
import pytest

from cta_lab.wyckoff_v4.contract import ContractViolation, check_bar, check_series_contract, to_ticks
from cta_lab.wyckoff_v4.evidence import ActivityGroup, compute_wave_groups, larger_context
from cta_lab.wyckoff_v4.engine import WyckoffV4Engine, assert_prefix_replay
from cta_lab.wyckoff_v4.indicators import IndicatorState, NumFeature, NotEvaluableReason
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.types import TriState, TrendState
from .scenarios import bar, contract, rng
from .test_group_c import make_wave, RNG, PARAMS, CURRENT_BAR
from cta_lab.wyckoff_v4.evaluation import EvaluationBar, evaluate_ohlc


@pytest.mark.parametrize("state", list(TriState))
def test_tristate_cannot_silently_coerce_to_bool(state):
    with pytest.raises(TypeError):
        bool(state)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_values_rejected(value):
    with pytest.raises(ValueError):
        replace(WyckoffV4Params(), tr_cap_mult=value).validate()
    with pytest.raises(ValueError):
        check_series_contract(replace(contract(), price_resolution=value))
    with pytest.raises(ContractViolation):
        to_ticks(value, .01)
    assert "nonfinite_volume" in check_bar(bar(0, 100, volume=value))


def test_missing_wave_metric_is_not_a_truthy_factory_method():
    waves = [make_wave(d, 1, 1, .5, end_index=5 + i) for i, d in enumerate((1, -1, 1, -1))]
    waves[0] = replace(waves[0], wave_activity_rate=NumFeature.not_evaluable(NotEvaluableReason.BV_UNAVAILABLE))
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert groups.activity is ActivityGroup.NOT_EVALUABLE


def test_larger_context_needs_n_plus_one_closes():
    assert larger_context(list(range(50)), 1, PARAMS) is TrendState.UNKNOWN
    assert larger_context(list(range(51)), 1, PARAMS) is TrendState.UP
    with pytest.raises(ValueError, match="warmup_bars"):
        replace(PARAMS, warmup_bars=50).validate()


def test_all_closes_of_wave_must_be_inside_range():
    waves = [make_wave(d, 1, 1, .5, end_index=5 + i) for i, d in enumerate((1, -1, 1, -1))]
    waves[0] = replace(waves[0], minimum_close=80, maximum_close=102)
    groups = compute_wave_groups(waves, RNG, PARAMS, CURRENT_BAR)
    assert waves[0].wave_id not in groups.stats.window_wave_ids


def test_engine_replay_and_snapshot_immutability():
    bars = [bar(i, 100 + i % 3, volume=100) for i in range(65)]
    engine = WyckoffV4Engine(contract())
    publications = engine.replay(bars)
    original = publications[0].to_json()
    publications[0].snapshot["quality"] = "modified by caller"
    assert publications[0].to_json() == original
    assert publications[-1].processing_order == tuple(range(1, 12))
    assert publications[-1].snapshot["quality"] == "ok"
    # Prefixes straddle warmup and the first automatic range creation.
    full = [p.to_json() for p in publications]
    for size in (1, 59, 60, 61, 65):
        assert [p.to_json() for p in WyckoffV4Engine(contract()).replay(bars[:size])] == full[:size]


def test_unclosed_duplicate_precision_and_rollback():
    engine = WyckoffV4Engine(contract())
    assert engine.process_bar(replace(bar(0, 100), is_closed=False)) is None
    engine.process_bar(bar(0, 100))
    original = engine.publications[0].to_json()
    with pytest.raises(ContractViolation, match="duplicate"):
        engine.process_bar(bar(0, 100))
    with pytest.raises(ContractViolation):
        engine.process_bar(bar(1, 100.001))
    assert len(engine.publications) == 1 and engine.publications[0].to_json() == original


def test_engine_transaction_exception_restores_all_mutable_state():
    engine = WyckoffV4Engine(contract())
    engine.process_bar(bar(0, 100))
    before = engine.publications[0].to_json()
    def fail_after_state_changes(*args):
        raise RuntimeError("injected step 8 failure")
    original = engine._evidence
    engine._evidence = fail_after_state_changes
    with pytest.raises(RuntimeError, match="step 8"):
        engine.process_bar(bar(1, 101))
    assert len(engine._features) == len(engine._bars) == len(engine.indicators.features) == 1
    assert len(engine.detector.ring._items) == 1
    assert engine.publications[0].to_json() == before
    engine._evidence = original
    assert engine.process_bar(bar(1, 101)).snapshot["asof_bar"] == 1


def test_complete_prefix_replay_crosses_real_detection_and_acceptance():
    prices = [100 + i % 3 for i in range(60)] + [103, 104, 105, 106, 107, 106, 104, 101, 100, 99]
    bars = [bar(i, c, open=c - .5, high=c + .25, low=c - .5, volume=100) for i, c in enumerate(prices)]
    def factory():
        engine = WyckoffV4Engine(contract())
        # Frozen pre-existing range is part of the same initial replay configuration.
        engine.front_range = replace(rng(r=102.2), established_bar=-1, origin_bar=-1)
        return engine
    full = factory().replay(bars)
    assert any(e.event_type.value == "BREAKOUT_UP" for p in full for e in p.events)
    assert any(e.event_type.value == "BREAKOUT_HOLD_CONFIRMED" for p in full for e in p.events)
    assert any(e.event_type.value == "BREAKOUT_ACCEPTANCE_INVALIDATED" for p in full for e in p.events)
    assert_prefix_replay(bars, factory)


def test_oi_late_snapshot_is_immutable_and_excludes_unavailable_value():
    from datetime import timedelta
    c = replace(contract(), oi_method="sum_raw")
    p = replace(PARAMS, oi_window=2)
    engine = WyckoffV4Engine(c, p)
    for i in range(4):
        b = replace(bar(i, 100 + i), open_interest=100 + i,
                    oi_available_at=bar(i, 0).bar_end + (timedelta(hours=1) if i == 3 else timedelta(0)))
        pub = engine.process_bar(b)
    before = pub.to_json()
    assert pub.snapshot["indicators"]["oi"]["oi_z"]["value"] is None
    assert pub.snapshot["indicators"]["oi"]["oi_z"]["reason"] == "oi_not_yet_available"
    engine.process_bar(replace(bar(4, 104), open_interest=104, oi_available_at=bar(4, 0).bar_end))
    annotations = engine.append_available_oi(bar(3, 0).bar_end + timedelta(hours=2))
    assert any(a.bar_index == 3 and a.oi_z == 1 for a in annotations)
    assert not engine.append_available_oi(bar(3, 0).bar_end + timedelta(hours=2))
    assert pub.to_json() == before


def test_gap_pauses_range_age_and_consecutive_counts():
    engine = WyckoffV4Engine(contract())
    for i in range(60):
        engine.process_bar(bar(i, 100 + i % 3, volume=100))
    engine.front_range = replace(rng(r=102.2), origin_bar=0, established_bar=30)
    snapshot = engine.detector.ring._items[-1]
    engine.detector.ring._items[-1] = replace(snapshot, range_id=engine.front_range.range_id,
                                             range_version=1, S=90, R=102.2, epsilon=.2, range_age=60)
    engine.process_bar(bar(60, 103, volume=100))
    engine.process_bar(bar(61, 103, flags=frozenset({"data_gap"})))
    assert engine.front_range.age(61) == engine.front_range.age(60)
    assert engine.front_range.outside_up_streak == 0
    result = engine.process_bar(bar(62, 103, volume=100))
    assert result.snapshot["quality"] == "ok"
    assert result.snapshot["evidence_groups"]["bias"] == "not_evaluable"


def test_evaluation_rejects_duplicate_bars_and_invalid_calendar():
    anchor = bar(0, 100)
    record = EvaluationBar(anchor, 1, anchor.received_at)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_ohlc(anchor.received_at, [record, record], [])
    with pytest.raises(ValueError, match="calendar"):
        evaluate_ohlc(anchor.received_at, [record], [(anchor.bar_end, anchor.bar_start)])


def test_evaluation_invalid_atr_and_ohlc_are_explicit():
    anchor, future = bar(0, 100), bar(1, 100)
    result = evaluate_ohlc(anchor.received_at, [EvaluationBar(anchor, math.nan, anchor.received_at)], [])
    assert result.outcome == "not_evaluable" and result.reasons == ("anchor_atr_unavailable",)
    invalid = replace(future, high=99)
    result = evaluate_ohlc(anchor.received_at,
                           [EvaluationBar(anchor, 1, anchor.received_at), EvaluationBar(invalid, 1, invalid.received_at)],
                           [(future.bar_start, future.bar_end)], horizon=1)
    assert result.outcome == "censored" and "evaluation_data_invalid" in result.reasons


def test_oi_missing_breaks_consecutive_doi_history():
    from datetime import timedelta
    state = IndicatorState(replace(PARAMS, oi_window=2), oi_method="sum_raw")
    for i in range(4):
        state.advance(replace(bar(i, 100), open_interest=100 + i, oi_available_at=bar(i, 0).bar_end))
    state.advance(bar(4, 100))
    state.advance(replace(bar(5, 100), open_interest=110, oi_available_at=bar(5, 0).bar_end))
    f = state.advance(replace(bar(6, 100), open_interest=111, oi_available_at=bar(6, 0).bar_end))
    assert f.oi.oi_z.reason is NotEvaluableReason.OI_HISTORY_INSUFFICIENT


def test_evaluation_does_not_fallback_when_latest_received_atr_is_missing():
    earlier, latest, future = bar(0, 100), bar(1, 120), bar(3, 121)
    records = [EvaluationBar(earlier, 1, earlier.received_at), EvaluationBar(latest, None, None),
               EvaluationBar(future, 1, future.received_at)]
    result = evaluate_ohlc(latest.received_at, records, [(future.bar_start, future.bar_end)], horizon=1)
    assert result.outcome == "not_evaluable" and result.reference_bar_id == latest.key


def test_evaluation_cannot_skip_missing_first_calendar_interval():
    anchor, missing, later = bar(0, 100), bar(1, 100), bar(2, 103)
    records = [EvaluationBar(anchor, 1, anchor.received_at), EvaluationBar(later, 1, later.received_at)]
    result = evaluate_ohlc(anchor.received_at, records,
                           [(missing.bar_start, missing.bar_end), (later.bar_start, later.bar_end)])
    assert result.outcome == "censored" and "data_gap" in result.reasons


def test_evaluation_both_barriers_intrabar_is_ambiguous():
    anchor, future = bar(0, 100), bar(1, 100, high=102, low=98)
    result = evaluate_ohlc(anchor.received_at,
                           [EvaluationBar(anchor, 1, anchor.received_at), EvaluationBar(future, 1, future.received_at)],
                           [(future.bar_start, future.bar_end)], horizon=1)
    assert result.outcome == "ambiguous_intrabar"

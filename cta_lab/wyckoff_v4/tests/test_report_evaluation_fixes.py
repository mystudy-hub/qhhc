"""Full evaluation paths and historical crossings have independent clocks."""
from dataclasses import replace
from datetime import timedelta

import pytest

from cta_lab.wyckoff_v4.evaluation import EvaluationBar, evaluate_ohlc
from .scenarios import bar


def record(b, atr=1):
    return EvaluationBar(b, atr, b.received_at)


def calendar(bars):
    return [(b.bar_start, b.bar_end) for b in bars]


@pytest.mark.parametrize("direction", [1, -1])
def test_metrics_continue_after_first_hit_over_the_full_horizon(direction):
    anchor = bar(0, 100)
    future = [bar(1, 102, high=103, low=99), bar(2, 101, high=102, low=98),
              bar(3, 104, high=105, low=100)]
    if direction == -1:
        future = [replace(b, open=200-b.open, high=200-b.low, low=200-b.high, close=200-b.close) for b in future]
    result = evaluate_ohlc(anchor.received_at, [record(b) for b in [anchor, *future]],
                           calendar(future), horizon=3, direction=direction, signal_reference=record(anchor), data_version="live-1")
    assert result.outcome == ("upper" if direction == 1 else "lower")
    assert result.hit_bar_id == future[0].key
    assert result.metrics_complete and result.path_bar_count == 3
    assert result.directional_net_displacement == result.directional_net_displacement_atr == 4
    assert result.max_drawdown == result.max_drawdown_atr == 1
    assert result.mfe == result.mfe_atr == 5
    assert result.mae == result.mae_atr == 2
    assert result.data_version == "live-1" and result.pre_crossed is False


def test_hit_label_survives_later_gap_but_metrics_are_marked_partial():
    anchor, first, missing, later = bar(0, 100), bar(1, 102, high=103, low=99), bar(2, 100), bar(3, 120)
    result = evaluate_ohlc(anchor.received_at, [record(b) for b in [anchor, first, later]],
                           calendar([first, missing, later]), horizon=3, direction=1)
    assert result.outcome == "upper" and result.hit_bar_id == first.key
    assert not result.metrics_complete and result.path_bar_count == 1
    assert result.metrics_reasons == ("data_gap",)
    assert result.directional_net_displacement == 2 and result.mfe == 3


def test_non_hit_label_is_censored_at_the_first_gap():
    anchor, first, missing, later = bar(0, 100), bar(1, 100), bar(2, 100), bar(3, 120)
    result = evaluate_ohlc(anchor.received_at, [record(b) for b in [anchor, first, later]],
                           calendar([first, missing, later]), horizon=3, direction=1)
    assert result.outcome == "censored" and "data_gap" in result.reasons
    assert result.path_bar_count == 1 and result.mfe == pytest.approx(.1)


def test_overlap_bar_is_excluded_from_all_metrics():
    anchor, overlap, first = bar(0, 100), bar(1, 100, high=130, low=70), bar(2, 101, high=101.5, low=99.5)
    tau = overlap.bar_start + timedelta(seconds=2)
    result = evaluate_ohlc(tau, [record(b) for b in [anchor, overlap, first]], calendar([overlap, first]),
                           horizon=1, direction=1, signal_reference=record(anchor))
    assert result.pre_crossed is None and result.pre_crossed_reason == "pre_crossed_unknown"
    assert "availability_overlap" in result.reasons and result.excluded_overlap_ids == (overlap.key,)
    assert result.outcome == "neither" and result.path_bar_count == 1
    assert result.mfe == 1.5 and result.mae == .5


def test_overlap_is_reported_even_without_a_future_evaluation_interval():
    anchor, overlap = bar(0, 100), bar(1, 100, high=103)
    tau = overlap.bar_start + timedelta(seconds=2)
    result = evaluate_ohlc(tau, [record(anchor), record(overlap)], calendar([overlap]),
                           signal_reference=record(anchor), direction=1)
    assert result.outcome == "censored"
    assert result.reasons == ("availability_overlap", "calendar_end")
    assert result.pre_crossed_reason == "pre_crossed_unknown" and result.pre_crossed is None
    assert not result.metrics_complete and result.path_bar_count == 0


@pytest.mark.parametrize("side", [1, -1])
def test_pre_crossed_uses_original_signal_bar_and_includes_equality(side):
    signal, past, future = bar(0, 100), bar(1, 100, high=102 if side == 1 else 100.1,
                                         low=98 if side == -1 else 99.9), bar(2, 100)
    result = evaluate_ohlc(past.received_at, [record(b) for b in [signal, past, future]],
                           calendar([past, future]), horizon=1, direction=side, signal_reference=record(signal))
    assert result.pre_crossed is True and result.pre_crossed_bar_ids == (past.key,)
    assert result.reference_bar_id == past.key and result.signal_reference_bar_id == signal.key
    assert result.outcome == "neither"


def test_confirmed_past_crossing_remains_true_despite_unknown_overlap():
    signal, past, overlap, future = bar(0, 100), bar(1, 100, high=103), bar(2, 100), bar(3, 100)
    tau = overlap.bar_start + timedelta(seconds=2)
    result = evaluate_ohlc(tau, [record(b) for b in [signal, past, overlap, future]],
                           calendar([past, overlap, future]), horizon=1, signal_reference=record(signal))
    assert result.pre_crossed is True
    assert result.pre_crossed_reason is None and "availability_overlap" in result.reasons


def test_pre_crossed_unknown_when_past_data_is_missing_or_not_yet_received():
    signal, past, future = bar(0, 100), bar(1, 100, high=110), bar(2, 100)
    tau = past.bar_end
    for records in ([record(signal), record(future)],
                    [record(signal), record(replace(past, received_at=tau+timedelta(hours=1))), record(future)]):
        result = evaluate_ohlc(tau, records, calendar([past, future]), horizon=1, signal_reference=record(signal))
        assert result.pre_crossed is None and result.pre_crossed_reason == "pre_crossed_unknown"


def test_absent_signal_reference_and_direction_are_explicitly_unknown():
    anchor, future = bar(0, 100), bar(1, 101)
    result = evaluate_ohlc(anchor.received_at, [record(anchor), record(future)], calendar([future]), horizon=1)
    assert result.pre_crossed is None and result.pre_crossed_reason == "signal_reference_missing"
    assert result.mfe is result.mae is result.max_drawdown is result.directional_net_displacement is None
    assert result.metrics_complete and result.metrics_reasons == ("direction_unknown",)


def test_unavailable_signal_atr_does_not_fallback_to_evaluation_atr():
    anchor, future = bar(0, 100), bar(1, 101)
    bad_reference = replace(record(anchor), atr_available_at=future.bar_end)
    result = evaluate_ohlc(anchor.received_at, [record(anchor), record(future)], calendar([future]), horizon=1,
                           direction=1, signal_reference=bad_reference)
    assert result.outcome == "neither"
    assert result.pre_crossed is None and result.pre_crossed_reason == "signal_reference_unavailable"


@pytest.mark.parametrize("direction", [True, 0, 2, 1.0, "up"])
def test_invalid_direction_is_rejected(direction):
    with pytest.raises(ValueError, match="direction"):
        evaluate_ohlc(bar(0, 100).received_at, [], [], direction=direction)


def test_calendar_end_metrics_are_partial_and_atr_normalized():
    anchor, future = bar(0, 100), bar(1, 101, high=101.5, low=99.5)
    result = evaluate_ohlc(anchor.received_at, [record(anchor, 2), record(future, 2)], calendar([future]),
                           horizon=2, direction=1)
    assert result.outcome == "censored" and not result.metrics_complete
    assert result.metrics_reasons == ("calendar_end",)
    assert result.directional_net_displacement_atr == .5 and result.mfe_atr == .75

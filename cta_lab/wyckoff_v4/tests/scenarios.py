"""Constructed baselines isolate lifecycle rules from ATR/Bv calibration."""
from dataclasses import replace
from datetime import datetime, timedelta

from cta_lab.wyckoff_v4.contract import BarV4, SeriesContract
from cta_lab.wyckoff_v4.indicators import IndicatorState, NumFeature
from cta_lab.wyckoff_v4.lifecycle import Lifecycle
from cta_lab.wyckoff_v4.output import ContextSnapshot, Occurrence
from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.range import TradingRange
from cta_lab.wyckoff_v4.types import RangeState, TrendState

T0 = datetime(2026, 1, 1)


def contract():
    return SeriesContract(index_id="TEST", index_source="vendor", index_method_version="1",
                          data_version="live-1", constituent_universe_definition="all listed months",
                          weight_method="oi", weight_asof_rule="previous day close",
                          weight_update_schedule="daily", ohlc_method="vendor_published",
                          price_resolution=.01, volume_method="sum_raw", volume_unit="contracts",
                          revision_policy="versioned")


def bar(i, close, *, open=None, high=None, low=None, volume=1, flags=frozenset(), received=None):
    o = close if open is None else open
    return BarV4(T0 + timedelta(minutes=5 * i), T0 + timedelta(minutes=5 * (i + 1)),
                 o, max(o, close) + .1 if high is None else high,
                 min(o, close) - .1 if low is None else low, close, volume,
                 received_at=received or T0 + timedelta(minutes=5 * (i + 1)), quality_flags=flags)


def feature(i, close, *, atr=1, bv=1, rvol=None, ns=None, net=None, **kwargs):
    b = bar(i, close, **kwargs)
    f = IndicatorState(WyckoffV4Params()).advance(b)
    return replace(f, index=i, atr_baseline=NumFeature.available(atr),
                   atr_baseline_standard=atr, atr_baseline_robust=atr,
                   bv_baseline=NumFeature.available(bv), bv_baseline_standard=NumFeature.available(bv),
                   bv_baseline_robust=NumFeature.available(bv),
                   ns=NumFeature.available(ns if ns is not None else b.high - b.low),
                   rvol=NumFeature.available(rvol if rvol is not None else b.volume / bv),
                   net_move=NumFeature.available(net or 0), gap=NumFeature.available(0))


def rng(s=90, r=100, eps=.2):
    return TradingRange("TEST-R", 1, RangeState.ACTIVE, s, r, (s + r) / 2, r - s, 1, eps, -30, -1)


def event(code, i, range_=None, *, start=None, strength="ordinary", metrics=None, level=None, onset=None):
    start = i if start is None else start
    c = onset or ContextSnapshot(f"S:{start - 1}", start - 1, T0, TrendState.NEUTRAL,
                                 range_.range_id if range_ else None, range_.version if range_ else None,
                                 range_.S if range_ else None, range_.R if range_ else None,
                                 range_.epsilon if range_ else None, 30 if range_ else 0)
    data = {"atr": 1, "effort": 1, "ns_ref": 4, "max_rvol": .5}
    data.update(metrics or {})
    return Occurrence(f"E:{code.value}:{start}:{i}", code, start, i,
                      bar(start, 0).bar_end, bar(i, 0).bar_end, bar(i, 0).bar_end,
                      onset_snapshot=c, reference_range_id=c.range_id, reference_range_version=c.range_version,
                      frozen_S=c.S, frozen_R=c.R, frozen_epsilon=c.epsilon,
                      frozen_internal_level=level, strength=strength, observed_metrics=tuple(data.items()))


class Scenario:
    def __init__(self, range_=None, params=None):
        self.range = range_ or rng()
        self.params = params or WyckoffV4Params()
        self.lifecycle = Lifecycle(self.params, .01)
        self.features = []

    def step(self, close, facts=(), **kwargs):
        i = len(self.features)
        f = feature(i, close, **kwargs)
        self.features.append(f)
        self.range = self.range.with_outside_streaks(*self.range.update_outside_streaks(close))
        lc = self.lifecycle
        lc.begin(self.features, self.range, f.bar.bar_end, f"T:{i}")
        lc.invalidate_prices()
        lc.ingest(list(facts))
        lc.confirm()
        lc.expire_candidates()
        self.range = lc.rng
        return lc.new_events

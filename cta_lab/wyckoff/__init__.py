"""维科夫（Wyckoff）识别与交易模块。

    from cta_lab.wyckoff import WyckoffEngine, DistributionEngine, simulate

    result = WyckoffEngine().process(df)
    sim = simulate(result, df)

对外主要入口：
    WyckoffEngine         吸筹状态机（增量式、零未来函数）
    DistributionEngine    派发状态机（价格镜像复用同一逻辑）
    build_plans           事件 -> 交易计划
    simulate              逐 K 回测
    run_backtest          从本地数据库一键回测
    scan                  多品种扫描
"""

from .engine import (
    DISTRIBUTION_EVENT_NAMES,
    EVENT_NAMES,
    DistributionConfig,
    DistributionEngine,
    TradingRange,
    WyckoffConfig,
    WyckoffEngine,
    WyckoffEvent,
    WyckoffResult,
    mirror_ohlc,
)
from .runner import run_backtest, run_both, scan
from .trading import SimResult, Trade, TradeConfig, build_plans, simulate

__all__ = [
    "WyckoffEngine",
    "DistributionEngine",
    "DistributionConfig",
    "WyckoffConfig",
    "WyckoffEvent",
    "WyckoffResult",
    "TradingRange",
    "EVENT_NAMES",
    "DISTRIBUTION_EVENT_NAMES",
    "mirror_ohlc",
    "TradeConfig",
    "Trade",
    "SimResult",
    "build_plans",
    "simulate",
    "run_backtest",
    "run_both",
    "scan",
]

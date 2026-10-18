"""自包含 CTA 策略库（不依赖 vnpy_ctastrategy.strategies）。

这些策略直接继承 CtaTemplate，展示了在 vnpy 4.x 下写策略的标准姿势：
    - parameters / variables 声明
    - on_init 里 load_bar 预热指标
    - on_bar 里通过 ArrayManager 计算指标
    - 用 buy/sell/short/cover 下单

命名约定（避免与官方策略同名互相覆盖）：
    本项目策略 : MaCrossStrategy / DonchianBreakoutStrategy /
                 AtrChannelStrategy / BollingerRevertStrategy
    vnpy 官方  : vnpy_ctastrategy.strategies.<name>_strategy
    官方策略通过 get_strategy("doublema") 等裸名调用。
"""

from __future__ import annotations

import numpy as np

from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy_ctastrategy import ArrayManager, BarGenerator, CtaTemplate
from vnpy_ctastrategy.base import StopOrder


# ---------------------------------------------------------------------------
# 1. 双均线（趋势跟随，基准策略）
# ---------------------------------------------------------------------------

class MaCrossStrategy(CtaTemplate):
    """双均线交叉（本项目版）：快线上穿慢线做多，下穿做空，始终持仓。

    与 vnpy 官方 DoubleMaStrategy 的区别：
        - 增加 fixed_size 仓位参数（官方为固定 1 手）
        - ArrayManager 尺寸随参数自适应
    注意类名与官方不同，避免在 GUI 里互相覆盖。
    """

    author = "cta_lab"

    fast_window: int = 10
    slow_window: int = 20
    fixed_size: int = 5

    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0

    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]

    def on_init(self) -> None:
        self.write_log("策略初始化")
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=max(self.slow_window + 10, 30))
        self.load_bar(max(self.slow_window * 2, 20))

    def on_start(self) -> None:
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        self.write_log("策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        fast_ma = am.sma(self.fast_window, array=True)
        self.fast_ma0 = float(fast_ma[-1])
        self.fast_ma1 = float(fast_ma[-2])

        slow_ma = am.sma(self.slow_window, array=True)
        self.slow_ma0 = float(slow_ma[-1])
        self.slow_ma1 = float(slow_ma[-2])

        cross_over = self.fast_ma0 > self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        cross_below = self.fast_ma0 < self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        if cross_over:
            if self.pos == 0:
                self.buy(bar.close_price, self.fixed_size)
            elif self.pos < 0:
                self.cover(bar.close_price, self.fixed_size)
                self.buy(bar.close_price, self.fixed_size)
        elif cross_below:
            if self.pos == 0:
                self.short(bar.close_price, self.fixed_size)
            elif self.pos > 0:
                self.sell(bar.close_price, self.fixed_size)
                self.short(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        pass

    def on_trade(self, trade: TradeData) -> None:
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass


# ---------------------------------------------------------------------------
# 2. 唐奇安通道突破（海龟类趋势）
# ---------------------------------------------------------------------------

class DonchianBreakoutStrategy(CtaTemplate):
    """唐奇安通道突破 + 反向通道止损。

    入场：收盘价突破过去 entry_window 日最高价做多 / 最低价做空
    出场：回到 exit_window 日反向极值（趋势跟踪经典做法）
    """

    author = "cta_lab"

    entry_window: int = 20
    exit_window: int = 10
    fixed_size: int = 5

    long_entry: float = 0.0
    long_exit: float = 0.0
    short_entry: float = 0.0
    short_exit: float = 0.0

    parameters = ["entry_window", "exit_window", "fixed_size"]
    variables = ["long_entry", "long_exit", "short_entry", "short_exit"]

    def on_init(self) -> None:
        self.write_log("策略初始化")
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=max(self.entry_window, self.exit_window) + 20)
        self.load_bar(max(self.entry_window * 2, 30))

    def on_start(self) -> None:
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        self.write_log("策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        # 用 [:-1] 排除当前 bar，避免未来函数
        high_arr = am.high_array
        low_arr = am.low_array

        self.long_entry = float(np.max(high_arr[-self.entry_window - 1: -1]))
        self.long_exit = float(np.min(low_arr[-self.exit_window - 1: -1]))
        self.short_entry = float(np.min(low_arr[-self.entry_window - 1: -1]))
        self.short_exit = float(np.max(high_arr[-self.exit_window - 1: -1]))

        if self.pos == 0:
            if bar.close_price > self.long_entry:
                self.buy(bar.close_price, self.fixed_size)
            elif bar.close_price < self.short_entry:
                self.short(bar.close_price, self.fixed_size)
        elif self.pos > 0:
            if bar.close_price < self.long_exit:
                self.sell(bar.close_price, self.fixed_size)
        else:
            if bar.close_price > self.short_exit:
                self.cover(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        pass

    def on_trade(self, trade: TradeData) -> None:
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass


# ---------------------------------------------------------------------------
# 3. ATR 通道突破（波动率自适应）
# ---------------------------------------------------------------------------

class AtrChannelStrategy(CtaTemplate):
    """ATR 通道突破：以均线为中心，上下各 k 倍 ATR 作为突破阈值。

    相比固定阈值，ATR 让入场条件随波动率自适应，在波动放大时避免
    被噪音反复触发。
    """

    author = "cta_lab"

    ma_window: int = 20
    atr_window: int = 20
    atr_multiplier: float = 1.5
    fixed_size: int = 5

    ma_value: float = 0.0
    atr_value: float = 0.0
    upper_band: float = 0.0
    lower_band: float = 0.0

    parameters = ["ma_window", "atr_window", "atr_multiplier", "fixed_size"]
    variables = ["ma_value", "atr_value", "upper_band", "lower_band"]

    def on_init(self) -> None:
        self.write_log("策略初始化")
        self.bg = BarGenerator(self.on_bar)
        size = max(self.ma_window, self.atr_window) + 20
        self.am = ArrayManager(size=size)
        self.load_bar(size * 2)

    def on_start(self) -> None:
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        self.write_log("策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        ma = am.sma(self.ma_window, array=True)
        self.ma_value = float(ma[-1])

        atr = am.atr(self.atr_window, array=True)
        self.atr_value = float(atr[-1])

        self.upper_band = self.ma_value + self.atr_multiplier * self.atr_value
        self.lower_band = self.ma_value - self.atr_multiplier * self.atr_value

        if self.pos == 0:
            if bar.close_price > self.upper_band:
                self.buy(bar.close_price, self.fixed_size)
            elif bar.close_price < self.lower_band:
                self.short(bar.close_price, self.fixed_size)
        elif self.pos > 0:
            # 跌破中轨离场
            if bar.close_price < self.ma_value:
                self.sell(bar.close_price, self.fixed_size)
        else:
            if bar.close_price > self.ma_value:
                self.cover(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        pass

    def on_trade(self, trade: TradeData) -> None:
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass


# ---------------------------------------------------------------------------
# 4. 布林带均值回归（震荡市）
# ---------------------------------------------------------------------------

class BollingerRevertStrategy(CtaTemplate):
    """布林带均值回归：触及下轨做多、上轨做空，回归中轨平仓。

    与趋势策略互补，用于检验同一品种在不同市场状态下的表现差异。
    """

    author = "cta_lab"

    boll_window: int = 20
    boll_dev: float = 2.0
    fixed_size: int = 5

    boll_up: float = 0.0
    boll_down: float = 0.0
    boll_mid: float = 0.0

    parameters = ["boll_window", "boll_dev", "fixed_size"]
    variables = ["boll_up", "boll_down", "boll_mid"]

    def on_init(self) -> None:
        self.write_log("策略初始化")
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=self.boll_window + 20)
        self.load_bar(self.boll_window * 3)

    def on_start(self) -> None:
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        self.write_log("策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        self.boll_up, self.boll_down = am.boll(
            self.boll_window, self.boll_dev, array=False
        )
        self.boll_mid = float(am.sma(self.boll_window, array=True)[-1])

        if self.pos == 0:
            if bar.close_price <= self.boll_down:
                self.buy(bar.close_price, self.fixed_size)
            elif bar.close_price >= self.boll_up:
                self.short(bar.close_price, self.fixed_size)
        elif self.pos > 0:
            if bar.close_price >= self.boll_mid:
                self.sell(bar.close_price, self.fixed_size)
        else:
            if bar.close_price <= self.boll_mid:
                self.cover(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        pass

    def on_trade(self, trade: TradeData) -> None:
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass


# ---------------------------------------------------------------------------
# 注册表：策略名 -> (类, 默认参数)
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, tuple[type, dict]] = {
    "MaCross": (MaCrossStrategy, {"fast_window": 10, "slow_window": 20, "fixed_size": 5}),
    "Donchian": (DonchianBreakoutStrategy, {"entry_window": 20, "exit_window": 10, "fixed_size": 5}),
    "AtrChannel": (
        AtrChannelStrategy,
        {"ma_window": 20, "atr_window": 20, "atr_multiplier": 1.5, "fixed_size": 5},
    ),
    "BollingerRevert": (BollingerRevertStrategy, {"boll_window": 20, "boll_dev": 2.0, "fixed_size": 5}),
}

# 参数寻优网格
PARAM_GRIDS: dict[str, dict[str, list]] = {
    "MaCross": {
        "fast_window": [5, 10, 15, 20],
        "slow_window": [30, 40, 60, 90],
    },
    "Donchian": {
        "entry_window": [10, 20, 30, 55],
        "exit_window": [5, 10, 20],
    },
    "AtrChannel": {
        "ma_window": [10, 20, 40],
        "atr_window": [10, 20, 40],
        "atr_multiplier": [1.0, 1.5, 2.0, 2.5],
    },
    "BollingerRevert": {
        "boll_window": [10, 20, 40],
        "boll_dev": [1.5, 2.0, 2.5],
    },
}


def get_strategy(name: str) -> tuple[type, dict]:
    """按名称取策略类与默认参数。"""
    key = name.strip()
    for k, v in STRATEGY_REGISTRY.items():
        if k.lower() == key.lower():
            return v[0], dict(v[1])
    # 兜底：尝试 vnpy 官方自带策略
    import importlib

    mapping = {
        "doublema": "double_ma_strategy:DoubleMaStrategy",
        "bollchannel": "boll_channel_strategy:BollChannelStrategy",
        "atrrsi": "atr_rsi_strategy:AtrRsiStrategy",
        "turtle": "turtle_signal_strategy:TurtleSignalStrategy",
        "dualtrust": "dual_thrust_strategy:DualThrustStrategy",
        "kingkeltner": "king_keltner_strategy:KingKeltnerStrategy",
    }
    if key.lower() in mapping:
        mod_name, cls_name = mapping[key.lower()].split(":")
        mod = importlib.import_module(f"vnpy_ctastrategy.strategies.{mod_name}")
        cls = getattr(mod, cls_name)
        return cls, cls.get_class_parameters()

    raise KeyError(
        f"未知策略 {name!r}；可选: {sorted(STRATEGY_REGISTRY)} 或 "
        f"{sorted(mapping)}（vnpy 官方）"
    )


__all__ = [
    "MaCrossStrategy",
    "DonchianBreakoutStrategy",
    "AtrChannelStrategy",
    "BollingerRevertStrategy",
    "STRATEGY_REGISTRY",
    "PARAM_GRIDS",
    "get_strategy",
]
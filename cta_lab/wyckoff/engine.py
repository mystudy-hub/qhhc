"""维科夫（Wyckoff）交易区间状态机引擎 —— 增量式、零未来函数、双向。

设计要点
========

1. **增量式（incremental）**
   ``update(bar)`` 每个 K 线调用一次，只使用 ``<= t`` 的数据。
   这从结构上消灭了未来函数，而不是靠"事后检查"。
   同一个对象既能用于回测，也能直接用于实盘。

2. **双向（bidirectional）通过镜像实现**
   派发（Distribution）是吸筹（Accumulation）的镜像。
   与其维护两套状态机（必然产生行为漂移），不如把价格取负、
   high/low 互换，跑同一套逻辑，再把事件名和信号方向映射回来。

       p  ->  -p
       买入高潮 BC  ->  卖出高潮 SC
       上冲回落 UTAD ->  弹簧 Spring
       弱势信号 SOW  ->  强势信号 SOS
       LPSY  ->  LPS

3. **修复的单K线死锁**
   不要求"同一根 K 线同时创新低+巨量+长下影"。
   改为 **3 根复合恐慌簇**：最近 3 根内出现过 N 日新低与巨量，
   且当日右侧收阳止跌，即确认 SC。

4. **因果法则（Cause）强制约束**
   Phase B 必须积累足够时长（``min_cause_bars``）才允许进入 Phase C。
   蓄势不足就跌穿冰线 → 判定为 FTI（破冰下挫），反向做空而不是继续等。

5. **跨K线弹簧窗（Multi-Bar Spring Window）**
   允许刺穿冰线后在 ``max_spring_bars`` 根内收复。
   超过窗口未收复 → 确认 FTI。

6. **事件自带交易参数**
   每个事件附带 ``stop`` / ``target``，交易层不需要再猜。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Direction = Literal["long", "short"]

# 事件种类（吸筹视角；派发自动映射）
EVENT_NAMES: dict[str, str] = {
    "CLIMAX": "SC",  # 恐慌抛售（派发：BC 买入高潮）
    "AR": "AR",  # 自动反弹
    "ST": "ST",  # 二次测试
    "SPRING": "SPRING",  # 弹簧（派发：UTAD 上冲回落）
    "SPRING_RECOVER": "SPRING_RECOVERED",
    "TEST": "TEST",  # 无供给测试
    "SOS": "SOS",  # 强势信号 / 跃过小溪
    "LPS": "LPS",  # 最后支撑点
    "FTI": "FTI",  # 破冰下挫（派发：向上突破冰线）
    "INVALID": "INVALID",  # 结构失效
}

#: 派发视角的措辞（把"止跌/反弹"换成"见顶/回落"）
DISTRIBUTION_NOTES: dict[str, str] = {
    "CLIMAX": "3根放量簇右侧见顶确认",
    "AR": "回落极值确认",
    "ST": "缩量二次测试，进入 Phase B",
    "SPRING": "上冲刺穿阻力，进入 UTAD 观察窗",
    "SPRING_RECOVER": "跌破阻力回落，UTAD 成立",
    "TEST": "无需求测试，右侧做空点",
    "SOS": "放量跌破冰线",
    "LPS": "回抽冰线受阻，加仓空点",
    "FTI": "向上突破冰线，反向做多",
}

DISTRIBUTION_EVENT_NAMES: dict[str, str] = {
    "CLIMAX": "BC",
    "AR": "AR",
    "ST": "ST",
    "SPRING": "UTAD",
    "SPRING_RECOVER": "UTAD_RECOVERED",
    "TEST": "TEST_SELL",
    "SOS": "SOW",
    "LPS": "LPSY",
    "FTI": "FTI_BREAKOUT",
    "INVALID": "INVALID",
}


@dataclass
class WyckoffEvent:
    """一个已被右侧确认的维科夫结构事件。"""

    index: int
    time: object
    kind: str               # 归一化前的事件键（CLIMAX/AR/ST/...）
    name: str               # 展示名（SC / BC / UTAD / ...）
    price: float
    direction: Direction    # 该事件对应交易方向的候选
    tr_id: int
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "time": str(self.time)[:10],
            "kind": self.kind,
            "name": self.name,
            "price": round(float(self.price), 4),
            "direction": self.direction,
            "tr_id": self.tr_id,
            "note": self.note,
        }


@dataclass
class TradingRange:
    """一个交易区间（吸筹或派发）。"""

    id: int
    direction: Direction
    start_index: int
    start_time: object
    climax_low: float = 0.0        # 吸筹：SC 低点；派发：BC 高点
    climax_high: float = 0.0       # AR 极值（creek / ice）
    climax_volume: float = 0.0     # SC 当时的峰值成交量
    ice: float = 0.0               # 支撑（吸筹下沿 / 派发上沿）
    creek: float = 0.0             # 阻力（吸筹上沿 / 派发下沿）
    phase: str = "PHASE_A"
    ar_index: int | None = None
    st_index: int | None = None
    spring_index: int | None = None
    spring_extreme: float = 0.0
    test_index: int | None = None
    sos_index: int | None = None
    lps_index: int | None = None
    end_index: int | None = None
    end_reason: str = ""
    events: list[WyckoffEvent] = field(default_factory=list)

    @property
    def is_long(self) -> bool:
        return self.direction == "long"

    @property
    def width(self) -> float:
        return abs(self.creek - self.ice)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "start": str(self.start_time)[:10],
            "phase": self.phase,
            "ice": round(float(self.ice), 4),
            "creek": round(float(self.creek), 4),
            "width": round(float(self.width), 4),
            "bars": (self.end_index - self.start_index + 1)
            if self.end_index is not None else None,
            "events": [e.as_dict() for e in self.events],
        }


@dataclass
class WyckoffConfig:
    """引擎参数配置。支持 macro（宏观大跨度日线）与 micro（局部微观）双模式。"""

    mode: Literal["macro", "micro"] = "macro"
    lookback: int = 60              # 新低/新高基准窗口（macro=60天/1个季度，micro=20天）
    vol_z_thresh: float = 2.0       # 恐慌簇的量能阈值（macro=2.0天量，micro=1.5）
    min_cause_bars: int = 35        # Phase B 最小横盘根数（因果法则，macro=35~40根/近2个月，micro=15根）
    prior_trend_bars: int = 60      # 前置主趋势窗口
    min_prior_drop_pct: float = 0.08 # 前置主趋势最小幅度要求（>= 8%）
    min_prior_drop_atr: float = 4.5  # 前置主趋势最小 ATR 倍数（>= 4.5 ATR）
    max_spring_bars: int = 10       # 弹簧收复窗口
    zone_buffer_pct: float = 0.03   # 冰线/小溪容差
    max_phase_a_bars: int = 35      # SC 后等 AR 的上限（macro=35，micro=25）
    ar_min_move_atr: float = 2.0    # AR 至少偏离高潮极值多少 ATR
    ar_retrace_atr: float = 1.0     # 从 AR 极值回落多少 ATR 算确认
    ar_min_rebound_pct: float = 0.02  # 或偏离百分比达到该值（二者取满足其一）
    use_oi_confirm: bool = True      # SOS 是否要求持仓量配合（量增或仓增）
    fti_require_volume: bool = True  # FTI 破冰是否要求量能确认（防无量假破）
    fti_vol_floor: float = 0.8       # FTI 的量能下限（相对 20 日均量）
    oi_lookback: int = 20            # 持仓量均值窗口
    max_tr_bars: int = 400          # 单个区间最长存活
    stop_buffer_pct: float = 0.015  # 止损缓冲
    target_r: float = 1.0           # 目标 = 冰线 + R × 区间宽度
    warmup: int = 65                # 预热根数

    @classmethod
    def create_macro(cls, **kwargs) -> WyckoffConfig:
        """创建宏观日线维科夫参数（默认推荐，年均1~2个大级别区间）。"""
        cfg = cls(
            mode="macro",
            lookback=60,
            vol_z_thresh=2.0,
            min_cause_bars=35,
            prior_trend_bars=60,
            min_prior_drop_pct=0.08,
            min_prior_drop_atr=4.5,
            max_phase_a_bars=35,
            warmup=65,
        )
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        return cfg

    @classmethod
    def create_micro(cls, **kwargs) -> WyckoffConfig:
        """创建微观短线维科夫参数（适合短周期或高频震荡分析）。"""
        cfg = cls(
            mode="micro",
            lookback=20,
            vol_z_thresh=1.5,
            min_cause_bars=15,
            prior_trend_bars=20,
            min_prior_drop_pct=0.0,
            min_prior_drop_atr=0.0,
            max_phase_a_bars=25,
            warmup=25,
        )
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        return cfg


class WyckoffEngine:
    """增量式维科夫状态机。

    用法::

        engine = WyckoffEngine()
        for bar in bars:
            events = engine.update(bar.high, bar.low, bar.close, bar.volume, bar.open_interest, bar.datetime)
            for e in events:
                print(e.name, e.price)

    或批处理::

        result = WyckoffEngine().process(df)
    """

    def __init__(self, config: WyckoffConfig | None = None) -> None:
        self.cfg = config or WyckoffConfig()

        # 滚动窗口（保证能够容纳 lookback 与 prior_trend_bars）
        win_size = max(self.cfg.lookback, getattr(self.cfg, 'prior_trend_bars', 60), 20) + 2
        self._highs: deque[float] = deque(maxlen=win_size)
        self._lows: deque[float] = deque(maxlen=win_size)
        self._closes: deque[float] = deque(maxlen=win_size)
        self._opens: deque[float] = deque(maxlen=win_size)
        self._vols: deque[float] = deque(maxlen=win_size)
        self._ois: deque[float] = deque(maxlen=max(self.cfg.oi_lookback, 20) + 1)
        self._spreads: deque[float] = deque(maxlen=20)

        self._i = -1
        self._time = None
        self._prev_close: float | None = None

        # 状态机
        self._state = "SEARCH_CLIMAX"
        self._tr: TradingRange | None = None
        self._tr_id = 0
        self._b_start = 0
        self._spring_bar = 0
        self._spring_low = 0.0

        # 输出
        self.events: list[WyckoffEvent] = []
        self.ranges: list[TradingRange] = []

        # 增量输出列
        self.phase_series: list[str] = []
        self.tr_id_series: list[int] = []
        self.signal_series: list[int] = []
        self.ice_series: list[float] = []
        self.creek_series: list[float] = []

    # ------------------------------------------------------------------
    # 增量更新
    # ------------------------------------------------------------------

    def update(
        self,
        high: float,
        low: float,
        close: float,
        volume: float,
        open_interest: float = 0.0,
        time=None,
        open_price: float | None = None,
    ) -> list[WyckoffEvent]:
        """喂一根 K 线，返回该根上新确认的事件。"""
        self._i += 1
        i = self._i
        self._time = time
        op = close if open_price is None else open_price

        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        self._opens.append(op)
        self._vols.append(volume)
        self._ois.append(float(open_interest or 0.0))
        self._spreads.append(high - low)

        event_mark = len(self.events)     # 记录本次调用前的事件数
        signal = 0

        if i >= self.cfg.warmup:
            signal = self._step(i, op, high, low, close, volume, open_interest)

        # 本次调用新增的事件（_emit 已 append 到 self.events）
        new_events: list[WyckoffEvent] = self.events[event_mark:]

        self._prev_close = close

        # 记录增量列
        self.tr_id_series.append(self._tr.id if self._tr else -1)
        self.phase_series.append(self._tr.phase if self._tr else "MARKDOWN")
        self.signal_series.append(signal)
        self.ice_series.append(self._tr.ice if self._tr else float("nan"))
        self.creek_series.append(self._tr.creek if self._tr else float("nan"))

        return new_events

    # ------------------------------------------------------------------
    # 状态机主体（吸筹视角；派发由镜像驱动）
    # ------------------------------------------------------------------

    def _step(
        self,
        i: int,
        op: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        open_interest: float = 0.0,
    ) -> int:
        cfg = self.cfg
        signal = 0

        atr = float(np.mean(list(self._spreads))) if self._spreads else (high - low)
        atr = max(atr, 1e-9)

        vol_ma, vol_sd = self._vol_stats()
        vol_z = (volume - vol_ma) / vol_sd if vol_sd > 1e-9 else 0.0
        oi_ma = self._oi_stats()
        oi_rising = open_interest > oi_ma * 1.005 if oi_ma > 0 else False

        spread = max(high - low, 1e-9)
        close_pos = (close - low) / spread

        # ---------- 0. 全局总时长保护（防止任何状态因极端行情挂死） ----------
        if self._tr is not None and (i - self._tr.start_index) > cfg.max_tr_bars:
            self._close_range(i, "TR_TIMEOUT")
            self._state = "SEARCH_CLIMAX"
            return 0

        # ---------- 状态 1：3 根复合恐慌簇右侧确认 ----------
        if self._state == "SEARCH_CLIMAX":
            if len(self._lows) >= cfg.lookback + 1:
                # 前置主趋势幅度检验（大级别 SC 之前必须经历显著主跌，BC 之前必须经历显著主升）
                if cfg.min_prior_drop_pct > 0 or cfg.min_prior_drop_atr > 0:
                    trend_win = min(len(self._highs) - 1, cfg.prior_trend_bars)
                    if trend_win >= 15:
                        highs_w = list(self._highs)[-(trend_win + 1): -1]
                        peak = max(highs_w) if highs_w else max(self._highs)
                        drop = peak - low
                        drop_pct = drop / max(abs(peak), 1e-9)
                        # 幅度必须达到百分比阈值或 ATR 倍数阈值
                        if drop_pct < cfg.min_prior_drop_pct and drop < atr * cfg.min_prior_drop_atr:
                            return 0

                window_low = min(list(self._lows)[-(cfg.lookback + 1): -1])
                recent_lows = list(self._lows)[-3:]
                recent_vols = list(self._vols)[-3:]
                recent_z = [
                    (v - vol_ma) / vol_sd if vol_sd > 1e-9 else 0.0
                    for v in recent_vols
                ]

                made_new_low = min(recent_lows) <= window_low
                had_climax_volume = max(recent_z) >= cfg.vol_z_thresh
                # 右侧止跌：收阳且收在上半区
                stopped = close > self._prev_close and close_pos >= 0.45

                if made_new_low and had_climax_volume and stopped:
                    self._tr_id += 1
                    lows3 = list(self._lows)[-3:]
                    vols3 = list(self._vols)[-3:]
                    tr = TradingRange(
                        id=self._tr_id,
                        direction="long",
                        start_index=i,
                        start_time=self._time,
                        climax_low=min(lows3),
                        climax_volume=max(vols3) if vols3 else 1.0,
                        phase="PHASE_A",
                    )
                    tr.ice = tr.climax_low
                    self._tr = tr
                    self.ranges.append(tr)
                    oi_note = ""
                    if self.cfg.use_oi_confirm and oi_ma > 0:
                        oi_note = (
                            f"持仓{'回落' if open_interest < oi_ma else '未落'}，"
                        )
                    self._emit(tr, i, "CLIMAX", tr.climax_low,
                               oi_note + "3根恐慌簇右侧止跌确认")
                    self._state = "WAIT_AR"

        # ---------- 状态 2：AR 自动反弹，锚定小溪 ----------
        elif self._state == "WAIT_AR":
            tr = self._tr
            assert tr is not None
            bars_since = i - tr.start_index

            if bars_since > cfg.max_phase_a_bars:
                self._close_range(i, "AR_TIMEOUT")

            elif bars_since >= 3:
                highs = list(self._highs)
                peak = max(highs[-min(bars_since + 1, len(highs)):])
                move = peak - tr.climax_low
                rebound_pct = move / max(abs(tr.climax_low), 1e-9)

                moved_enough = (
                    move >= cfg.ar_min_move_atr * atr
                    or rebound_pct >= cfg.ar_min_rebound_pct
                )
                retraced = close < peak - cfg.ar_retrace_atr * atr

                if moved_enough and retraced:
                    tr.creek = peak
                    tr.ar_index = i
                    self._emit(tr, i, "AR", peak,
                               f"反弹波峰确认（{rebound_pct:.1%} / {move/atr:.1f}ATR）")
                    self._state = "WAIT_ST"

        # ---------- 状态 3：ST 二次测试，确立冰线 ----------
        elif self._state == "WAIT_ST":
            tr = self._tr
            assert tr is not None
            bars_since_ar = i - (tr.ar_index or tr.start_index)
            near_ice = abs(low - tr.climax_low) / max(tr.climax_low, 1e-9) <= cfg.zone_buffer_pct
            vol_contracted = volume < max(tr.climax_volume, 1e-9) * 0.75

            if near_ice and vol_contracted and close >= op:
                tr.st_index = i
                tr.ice = min(tr.climax_low, low)
                tr.phase = "PHASE_B"
                self._b_start = i
                self._emit(tr, i, "ST", low, "缩量二次测试，进入 Phase B")
                self._state = "PHASE_B"

            elif close < tr.climax_low * (1.0 - cfg.zone_buffer_pct):
                # 未形成 ST 就跌破 → 结构失败，反向（需量能确认，防无量假破）
                if self._fti_confirmed(volume, vol_ma):
                    self._emit(tr, i, "FTI", close, "跌破SC低点，反向做空")
                    signal = -1
                    self._close_range(i, "FTI")
                    self._state = "SEARCH_CLIMAX"
            elif bars_since_ar > cfg.max_phase_a_bars * 1.5:
                # 超时未能回到冰线确认 ST -> 阶段 A 结束，关闭区间
                self._close_range(i, "ST_TIMEOUT")
                self._state = "SEARCH_CLIMAX"

        # ---------- 状态 4：Phase B 蓄势（因果法则强约束） ----------
        elif self._state == "PHASE_B":
            tr = self._tr
            assert tr is not None
            cause_bars = i - self._b_start

            if i - tr.start_index > cfg.max_tr_bars:
                self._close_range(i, "TR_TIMEOUT")

            elif low < tr.ice * (1.0 - 0.003):
                if cause_bars >= cfg.min_cause_bars:
                    # 进入弹簧观察窗
                    self._spring_bar = i
                    self._spring_low = low
                    tr.spring_index = i
                    tr.spring_extreme = low
                    tr.phase = "PHASE_C"
                    self._emit(tr, i, "SPRING", low, "刺穿冰线，进入弹簧观察窗，新")
                    self._state = "SPRING_WINDOW"
                elif close < tr.ice * (1.0 - cfg.zone_buffer_pct):
                    # 蓄势不足直接深幅跌穿 → FTI（需量能确认）
                    if self._fti_confirmed(volume, vol_ma):
                        self._emit(tr, i, "FTI", close, "蓄势不足即破冰，反向做空")
                        signal = -1
                        self._close_range(i, "FTI")
                        self._state = "SEARCH_CLIMAX"

            # 无弹簧、直接放量跃过小溪（Schematic #2）
            elif (
                cause_bars >= cfg.min_cause_bars
                and close > tr.creek * 1.005
                and vol_z >= 0.8
                and self._sos_confirmed(vol_z, oi_rising)
            ):
                tr.phase = "PHASE_D"
                tr.sos_index = i
                self._emit(tr, i, "SOS", close,
                           "无弹簧直接放量跃过小溪" + self._sos_note(oi_rising))
                self._state = "WAIT_LPS"

        # ---------- 状态 5：跨K线弹簧收复窗 ----------
        elif self._state == "SPRING_WINDOW":
            tr = self._tr
            assert tr is not None
            bars_in = i - self._spring_bar
            if low < tr.spring_extreme:
                tr.spring_extreme = low
                self._spring_low = low
                tr.spring_index = i

            if close >= tr.ice and close > op:
                self._emit(tr, i, "SPRING_RECOVER", tr.spring_extreme, f"{bars_in}根内收复冰线")
                self._state = "WAIT_TEST"

            elif bars_in > cfg.max_spring_bars or close < tr.ice * (1.0 - cfg.zone_buffer_pct * 1.8):
                # 弹簧逾窗未收复，确认破冰（需量能确认）
                if self._fti_confirmed(volume, vol_ma):
                    self._emit(tr, i, "FTI", close, "弹簧逾窗未收复，确认破冰")
                    signal = -1
                    self._close_range(i, "SPRING_FAILED")
                    self._state = "SEARCH_CLIMAX"

        # ---------- 状态 6：无供给二次测试 → 右侧买点 1 ----------
        elif self._state == "WAIT_TEST":
            tr = self._tr
            assert tr is not None
            bars_since_spring = i - (tr.spring_index or tr.start_index)

            higher_low = low > tr.spring_extreme
            near_ice = abs(low - tr.ice) / max(tr.ice, 1e-9) <= cfg.zone_buffer_pct
            low_vol = volume <= vol_ma * 0.85

            if higher_low and near_ice and low_vol and close >= op:
                tr.test_index = i
                stop = min(tr.spring_extreme, tr.ice) * (1.0 - cfg.stop_buffer_pct)
                target = tr.creek + cfg.target_r * max(tr.creek - tr.ice, atr)
                tr.phase = "PHASE_C"
                self._emit(tr, i, "TEST", close, f"无供给测试，止损{stop:.2f} 目标{target:.2f}")
                signal = 1
                self._state = "WAIT_SOS"

            elif close < tr.spring_extreme:
                self._close_range(i, "TEST_FAILED")
                self._state = "SEARCH_CLIMAX"
            elif bars_since_spring > 25:
                # 弹簧收复后久不测试 -> 测试超时
                self._close_range(i, "TEST_TIMEOUT")
                self._state = "SEARCH_CLIMAX"

        # ---------- 状态 7a：等 SOS ----------
        elif self._state == "WAIT_SOS":
            tr = self._tr
            assert tr is not None
            bars_since_test = i - (tr.test_index or tr.start_index)

            if close > tr.creek and vol_z >= 0.8 and self._sos_confirmed(vol_z, oi_rising):
                tr.sos_index = i
                tr.phase = "PHASE_D"
                self._emit(tr, i, "SOS", close,
                           "放量跃过小溪" + self._sos_note(oi_rising))
                self._state = "WAIT_LPS"
            elif close < tr.spring_extreme:
                self._close_range(i, "STRUCTURE_BROKEN")
                self._state = "SEARCH_CLIMAX"
            elif bars_since_test > 35:
                # 测试后蓄势过久未能突破 Creek
                self._close_range(i, "SOS_TIMEOUT")
                self._state = "SEARCH_CLIMAX"

        # ---------- 状态 7b：等 LPS 回踩 → 加仓买点 2 ----------
        elif self._state == "WAIT_LPS":
            tr = self._tr
            assert tr is not None
            bars_since_sos = i - (tr.sos_index or tr.start_index)

            near_creek = abs(low - tr.creek) / max(tr.creek, 1e-9) <= cfg.zone_buffer_pct
            low_vol = volume <= vol_ma * 0.95

            if near_creek and low_vol and close >= op:
                tr.lps_index = i
                tr.phase = "PHASE_E"
                self._emit(tr, i, "LPS", close, "回踩小溪企稳，加仓买点")
                signal = 1
                self._state = "MARKUP"

            elif close > tr.creek * 1.08 and bars_since_sos >= 3:
                # 价格脱离小溪线超过 8% 且不深幅回踩 -> 逃逸型主升浪，自动晋级 Phase E
                tr.phase = "PHASE_E"
                self._state = "MARKUP"

            elif close < tr.creek - atr * 1.5 or close < tr.ice:
                self._close_range(i, "LPS_FAILED")
                self._state = "SEARCH_CLIMAX"

            elif bars_since_sos > 30:
                self._close_range(i, "LPS_TIMEOUT")
                self._state = "SEARCH_CLIMAX"

        # ---------- 状态 8：主升浪，等区间结束 ----------
        elif self._state == "MARKUP":
            tr = self._tr
            assert tr is not None
            bars_in_markup = i - (tr.lps_index or tr.sos_index or tr.start_index)
            closes_list = list(self._closes)
            ma20 = float(np.mean(closes_list[-20:])) if len(closes_list) >= 20 else close

            # 趋势衰竭条件：
            # 1. 跌破 20日均线且跌破 Creek 线
            # 2. 或主升浪运行超 90 根 K 线
            # 3. 或价格达到 Creek 1.5 倍
            trend_broken = close < ma20 and close < tr.creek
            if (
                close > tr.creek * 1.5
                or (trend_broken and bars_in_markup > 15)
                or bars_in_markup > 90
                or (i - tr.start_index) > cfg.max_tr_bars
            ):
                self._close_range(i, "MARKUP_DONE")
                self._state = "SEARCH_CLIMAX"

        return signal

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _vol_stats(self) -> tuple[float, float]:
        vols = list(self._vols)
        if len(vols) < 5:
            return (vols[-1] if vols else 0.0), 0.0
        arr = np.asarray(vols[-20:], dtype=float)
        return float(arr.mean()), float(arr.std(ddof=0))

    def _oi_stats(self) -> float:
        """近 N 日持仓量均值（期货资金承诺维度的基线）。"""
        ois = list(self._ois)
        if not ois:
            return 0.0
        arr = np.asarray(ois[-self.cfg.oi_lookback:], dtype=float)
        return float(arr.mean())

    def _fti_confirmed(self, volume: float, vol_ma: float) -> bool:
        """FTI 破冰是否需要量能确认。

        无量破冰通常是死水市场里的噪声击穿，不做；
        只有成交量不低于 20 日均量的 fti_vol_floor 倍才算有效破冰。
        """
        if not self.cfg.fti_require_volume:
            return True
        if vol_ma <= 1e-9:
            return True
        return volume >= vol_ma * self.cfg.fti_vol_floor

    def _sos_confirmed(self, vol_z: float, oi_rising: bool) -> bool:
        """SOS 需量增或仓增（区分真实突破 vs 空头减仓反弹）。

        期货里单靠量能会误把 AR 当 SOS；持仓量同步上升才是新多头进场。
        宽松策略：只要量增(vol_z>=0.8)且(持仓升 或 不开启 OI 校验)即通过。
        """
        if not self.cfg.use_oi_confirm:
            return True
        return vol_z >= 0.8 and (oi_rising or vol_z >= 1.5)

    def _sos_note(self, oi_rising: bool) -> str:
        if not self.cfg.use_oi_confirm:
            return ""
        return "（仓增）" if oi_rising else "（量暴）"

    def _emit(
        self,
        tr: TradingRange,
        i: int,
        kind: str,
        price: float,
        note: str,
    ) -> WyckoffEvent:
        name = EVENT_NAMES.get(kind, kind)
        direction: Direction = "long"
        if kind == "FTI":
            direction = "short"
        ev = WyckoffEvent(
            index=i,
            time=self._time,
            kind=kind,
            name=name,
            price=float(price),
            direction=direction,
            tr_id=tr.id,
            note=note,
        )
        self.events.append(ev)
        tr.events.append(ev)
        return ev

    def _close_range(self, i: int, reason: str) -> None:
        if self._tr is not None:
            self._tr.end_index = i
            self._tr.end_reason = reason
            self._tr = None
        self._state = "SEARCH_CLIMAX"

    # ------------------------------------------------------------------
    # 批处理
    # ------------------------------------------------------------------

    def process(self, df) -> "WyckoffResult":
        """批处理。

        参数 df 需包含列：
            open / high / low / close / volume
            可选 open_interest，索引可为 DatetimeIndex
        """
        import pandas as pd

        reset = self.__class__(self.cfg)
        for idx in range(len(df)):
            row = df.iloc[idx]
            reset.update(
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                open_interest=float(row.get("open_interest", 0.0) or 0.0),
                time=df.index[idx],
                open_price=float(row["open"]),
            )

        out = pd.DataFrame(
            {
                "tr_id": reset.tr_id_series,
                "phase": reset.phase_series,
                "signal": reset.signal_series,
                "ice": reset.ice_series,
                "creek": reset.creek_series,
            },
            index=df.index,
        )
        return WyckoffResult(
            df=out,
            events=reset.events,
            ranges=reset.ranges,
            engine=reset,
        )


@dataclass
class WyckoffResult:
    df: object
    events: list[WyckoffEvent]
    ranges: list[TradingRange]
    engine: WyckoffEngine

    @property
    def signal(self):
        return self.df["signal"]

    def event_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.name] = counts.get(e.name, 0) + 1
        return dict(sorted(counts.items()))

    def timeline(self) -> list[dict]:
        return [e.as_dict() for e in sorted(self.events, key=lambda x: x.index)]


# ----------------------------------------------------------------------
# 派发（Distribution）镜像
# ----------------------------------------------------------------------

def mirror_ohlc(df):
    """把价格镜像成"派发等价吸筹"形态。

    映射：
        open  -> -open
        close -> -close
        high  -> -low
        low   -> -high

    校验：原 open ∈ [low, high]  ⇒  -open ∈ [-high, -low]
    恰好落在镜像后的 [low', high'] 区间内，OHLC 关系保持自洽。

    镜像后原图的"顶部"变成"底部"，于是同一套吸筹状态机
    可以直接识别 BC / UTAD / SOW / LPSY，无需维护第二份逻辑。
    """
    import pandas as pd

    vol = df["volume"] if "volume" in df.columns else pd.Series(0.0, index=df.index)
    oi = (
        df["open_interest"]
        if "open_interest" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    return pd.DataFrame(
        {
            "open": -df["open"],
            "high": -df["low"],
            "low": -df["high"],
            "close": -df["close"],
            "volume": vol,
            "open_interest": oi,
        },
        index=df.index,
    )


# ----------------------------------------------------------------------
# 派发（Distribution）引擎：内部复用吸筹状态机 + 价格镜像
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# 派发（Distribution）配置：镜像后市场波动特征不对称，需独立参数
# ----------------------------------------------------------------------

@dataclass
class DistributionConfig(WyckoffConfig):
    """派发专用参数。

    商品期货里，下跌(吸筹的 SC)往往比上涨(派发的 BC)更急更烈：
    - 买入高潮的量能 Z 阈值略低（顶部放量更温和）
    - 回落(AR)幅度更浅、更慢（阴跌）
    - 顶部蓄势时间略短
    因此默认放宽这些阈值，避免派发形态刚见 BC 就被 FTI 打断。
    """

    vol_z_thresh: float = 1.3
    ar_min_move_atr: float = 1.5
    ar_min_rebound_pct: float = 0.015
    min_cause_bars: int = 12


class DistributionEngine(WyckoffEngine):
    """高位派发识别器。

    实现方式：把 OHLC 取负（高低互换）后喂给吸筹状态机，
    再把输出的事件名、价格、方向映射回真实空间。

    事件映射：
        SC     -> BC          买入高潮
        AR     -> AR          自动回落
        ST     -> ST          二次测试
        SPRING -> UTAD        上冲回落
        TEST   -> TEST_SELL   无需求测试（右侧做空点 1）
        SOS    -> SOW         弱势信号（跌破冰线）
        LPS    -> LPSY        回抽确认（右侧做空点 2）
        FTI    -> FTI_BREAKOUT 向上突破，反向做多

    映射只做一次：用 `_mapped_ranges` / `_mapped_events` 计数器记录
    已转换数量，每次 update 只处理新增部分，杜绝重复取负。
    """

    def __init__(self, config=None) -> None:
        # 未显式传参时用派发专用默认参数，否则尊重调用方配置
        super().__init__(config if config is not None else DistributionConfig())
        self._mapped_events: int = 0

    def update(
        self,
        high: float,
        low: float,
        close: float,
        volume: float,
        open_interest: float = 0.0,
        time=None,
        open_price: float | None = None,
    ) -> list[WyckoffEvent]:
        op = close if open_price is None else open_price

        # 镜像后喂给吸筹状态机（此时 self.events / self.ranges 里是镜像空间的值）
        super().update(
            high=-low,
            low=-high,
            close=-close,
            volume=volume,
            open_interest=open_interest,
            time=time,
            open_price=-op,
        )

        # 只翻转"对外返回的事件"。
        # 绝不能在这里改 tr.ice / tr.creek / tr.climax_low ——
        # 那些字段正被状态机在镜像空间里持续比较，改写会让后续判定全部失效
        # （曾因此导致 48 个 BC 却 0 个 AR）。
        # 区间字段统一在 process() 结束时一次性映射。
        for ev in self.events[self._mapped_events:]:
            ev.name = DISTRIBUTION_EVENT_NAMES.get(ev.kind, ev.name)
            ev.price = -ev.price
            ev.direction = "short" if ev.direction == "long" else "long"
            if ev.kind in DISTRIBUTION_NOTES:
                ev.note = DISTRIBUTION_NOTES[ev.kind]
        new_events = self.events[self._mapped_events:]
        self._mapped_events = len(self.events)

        return new_events

    def process(self, df) -> "WyckoffResult":  # type: ignore[override]
        """批处理（镜像输入 -> 真实输出）。"""
        import pandas as pd

        reset = DistributionEngine(self.cfg)
        for idx in range(len(df)):
            row = df.iloc[idx]
            reset.update(
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                open_interest=float(row.get("open_interest", 0.0) or 0.0),
                time=df.index[idx],
                open_price=float(row["open"]),
            )

        # 回测结束后，把区间边界一次性从镜像空间翻回真实空间
        for tr in reset.ranges:
            tr.direction = "short"
            tr.climax_low = -tr.climax_low
            tr.climax_high = -tr.climax_high
            tr.ice = -tr.ice
            tr.creek = -tr.creek
            tr.spring_extreme = -tr.spring_extreme

        out = pd.DataFrame(
            {
                "tr_id": reset.tr_id_series,
                "phase": reset.phase_series,
                # 镜像空间的 signal 也要翻转
                "signal": [-x for x in reset.signal_series],
                "ice": [-x for x in reset.ice_series],
                "creek": [-x for x in reset.creek_series],
            },
            index=df.index,
        )
        return WyckoffResult(
            df=out,
            events=reset.events,
            ranges=reset.ranges,
            engine=reset,
        )

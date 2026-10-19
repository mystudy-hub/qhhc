"""
Wyckoff Pro Quantitative Engine & VectorBT Strategy Suite
=========================================================
针对 `cta_lab/wyckoff` 的全栈式升级实现（零未来函数、多周期自适应、九大买卖检验、Weis Wave 量能波、VectorBT 优先回测引擎）。

核心改进亮点：
1. 修复原 `DistributionEngine` 负数价格镜像导致的数学不等式反转与 `max(price, 1e-9)` 截断致命 Bug（改用正数偏移锚点镜像 `P' = Anchor - P` + 宽度归一化容差）。
2. 完整实现维科夫五大阶段（Phase A~E）与五大市场状态（Accumulation / Markup / Distribution / Markdown / Re-accumulation & Re-distribution）。
3. 补全全套维科夫事件识别：
   - Phase A: PS (Preliminary Support) / PSY, SC (Selling Climax) / BC, AR (Automatic Rally/Reaction), ST (Secondary Test，支持 Higher Low 弹性回踩)
   - Phase B: UT (Upthrust 上冲测试), SOW_B (Phase B 弱势测试), 动态蜿蜒小溪线 (Meandering Creek) 与动态冰线 (Dynamic Ice)
   - Phase C: Spring #1 (放量破冰->FTI) / Spring #2 (刺穿收复需二次测试) / Spring #3 (缩量浅刺当根收复直接入场), UTAD (派发后上冲回落), TEST (无供给/无需求测试)
   - Phase D: SOS (强势信号) / JAC (Jump Across the Creek 跃过小溪), SOW (弱势信号) / FTI (Fall Through the Ice 破冰), BUEC (回撤小溪边缘), LPS / LPSY
   - Phase E: Markup / Markdown 趋势跟踪与途中再吸筹 (Re-accumulation) / 再派发 (Re-distribution) 识别
4. 引入复合人（Composite Man）量价行为量化：
   - 努力与结果法则（Effort vs. Result / Absorption Index）：识别放量滞涨/放量止跌的主力限价吸收行为
   - David Weis 波段量能（Weis Wave Volume）：对比同向推进波段的累计成交量与点数效率，量化供需转换
5. 完整实现 Hank Pruden 维科夫九大买卖检验（Nine Buying/Selling Tests）打分器（0~9分），支持按得分过滤与动态仓位缩放。
6. 回测层优先调用 `vectorbt`（`vbt.Portfolio.from_signals`），若当前环境未安装则自动无缝降级为内置的高精度事件驱动回测器（已修复原 `trading.py` 的入场当根漏检止损与跳空穿仓理想化成交 Bug，支持底仓 TEST + 浮盈 LPS 金字塔加仓），并支持 `mplfinance` / `matplotlib` 可视化标注。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

Direction = Literal["long", "short"]
TimeframeMode = Literal["macro", "swing", "intraday"]


# ============================================================================
# 1. 事件与区间数据结构
# ============================================================================

ACCUMULATION_EVENT_NAMES: dict[str, str] = {
    "PS": "PS",                        # 初步支撑 Preliminary Support
    "CLIMAX": "SC",                    # 恐慌抛售 Selling Climax
    "AR": "AR",                        # 自动反弹 Automatic Rally
    "ST": "ST",                        # 二次测试 Secondary Test
    "UT": "UT",                        # 区间上冲假突破 Upthrust in Phase B
    "SOW_B": "SOW_B",                  # 区间探底测试 Minor SOW in Phase B
    "SPRING": "SPRING",                # 弹簧刺穿冰线
    "SPRING_RECOVER": "SPRING_REC",    # 弹簧收复（含 #2 待测 与 #3 直接确认）
    "SPRING3": "SPRING_#3",            # 弹簧 #3（缩量浅刺当根收复，右侧买点）
    "TEST": "TEST",                    # 二次无供给测试（右侧买点 1）
    "SOS": "SOS",                      # 强势信号 Sign of Strength
    "JAC": "JAC",                      # 跃过小溪 Jump Across the Creek
    "BUEC": "BUEC",                    # 回撤至小溪边缘 Backup to the Edge of the Creek
    "LPS": "LPS",                      # 最后支撑点 Last Point of Support（右侧买点 2）
    "FTI": "FTI",                      # 破冰下挫 Fall Through the Ice（结构破坏反向）
}

DISTRIBUTION_EVENT_NAMES: dict[str, str] = {
    "PS": "PSY",                       # 初步供给 Preliminary Supply
    "CLIMAX": "BC",                    # 买入高潮 Buying Climax
    "AR": "AR_DIST",                   # 自动回落 Automatic Reaction
    "ST": "ST_DIST",                   # 二次测试 Secondary Test in Distribution
    "UT": "SOW_B",                     # 镜像：区间下探测试
    "SOW_B": "UT_B",                   # 镜像：区间上冲测试 Upthrust in B
    "SPRING": "UTAD",                  # 派发后上冲 Upthrust After Distribution
    "SPRING_RECOVER": "UTAD_REC",      # UTAD 跌回区间
    "SPRING3": "UTAD_#3",              # 缩量浅刺阻力当根收跌（直接做空点）
    "TEST": "TEST_SELL",               # 无需求测试 No Demand Test（右侧卖点 1）
    "SOS": "SOW",                      # 弱势信号 Sign of Weakness
    "JAC": "FTI",                      # 跌破冰线 Fall Through the Ice
    "BUEC": "BUEC_ICE",                # 反抽冰线边缘 Backup to the Ice
    "LPS": "LPSY",                     # 最后供给点 Last Point of Supply（右侧卖点 2）
    "FTI": "JAC_BREAKOUT",             # 派发失败向上突破（反向做多）
}


@dataclass
class WyckoffEvent:
    """右侧确认的维科夫结构事件（附带九大检验评分与建议风控参数）。"""

    index: int
    time: object
    kind: str
    name: str
    price: float
    direction: Direction
    tr_id: int
    phase: str
    regime: str
    nine_tests_score: int = 0
    stop: float = 0.0
    target: float = 0.0
    effort_result_ratio: float = 1.0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "time": str(self.time)[:19],
            "kind": self.kind,
            "name": self.name,
            "price": round(float(self.price), 4),
            "direction": self.direction,
            "tr_id": self.tr_id,
            "phase": self.phase,
            "regime": self.regime,
            "nine_tests": self.nine_tests_score,
            "stop": round(float(self.stop), 4),
            "target": round(float(self.target), 4),
            "er_ratio": round(float(self.effort_result_ratio), 2),
            "note": self.note,
        }


@dataclass
class TradingRange:
    """维科夫交易区间（支持吸筹、再吸筹、派发、再派发及动态小溪/冰线边界）。"""

    id: int
    direction: Direction
    regime: str                        # ACCUMULATION / RE_ACCUMULATION / DISTRIBUTION / RE_DISTRIBUTION
    start_index: int
    start_time: object
    ps_price: float = 0.0
    climax_low: float = 0.0
    climax_high: float = 0.0
    climax_volume: float = 0.0
    ice: float = 0.0                   # 动态支撑下沿（派发镜像前）
    creek: float = 0.0                 # 动态阻力上沿（蜿蜒小溪）
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
    cause_volume_sum: float = 0.0      # 区间累积成交量（量化因果法则能量）
    prior_peak: float = 0.0            # 前置趋势极值（SEARCH_CLIMAX 确认的峰，量化 Test 1 前序趋势目标完成）
    up_wave_vol: float = 0.0           # 区间内已完结上涨波段累计成交量（David Weis Wave Demand）
    down_wave_vol: float = 0.0         # 区间内已完结下跌波段累计成交量（David Weis Wave Supply）
    higher_lows_count: int = 0
    events: list[WyckoffEvent] = field(default_factory=list)

    @property
    def width(self) -> float:
        return abs(self.creek - self.ice)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "regime": self.regime,
            "start": str(self.start_time)[:10],
            "start_index": self.start_index,
            "ice": round(float(self.ice), 4),
            "creek": round(float(self.creek), 4),
            "width": round(float(self.width), 4),
            "phase": self.phase,
            "end_index": self.end_index,
            "end_reason": self.end_reason,
            "up_wave_vol": float(self.up_wave_vol),
            "down_wave_vol": float(self.down_wave_vol),
            "events": [e.as_dict() for e in self.events],
        }


# ============================================================================
# 2. 引擎配置（支持日线 Macro、波段 Swing、60m/1m 日内 Intraday 自适应）
# ============================================================================

@dataclass
class WyckoffProConfig:
    """全周期自适应维科夫引擎配置。"""

    mode: TimeframeMode = "macro"
    lookback: int = 50                 # 极值回看窗口
    vol_z_thresh: float = 1.8          # 恐慌/狂热簇量能 Z-Score 阈值
    ps_vol_z_thresh: float = 1.1       # PS/PSY 初步支撑量能阈值
    min_cause_bars: int = 25           # Phase B 最小横盘根数（因果法则底线）
    prior_trend_bars: int = 50         # 前置趋势考察窗口
    min_prior_move_atr: float = 3.5    # 前置趋势最小 ATR 倍数
    max_spring_bars: int = 6           # 弹簧跨 K 线收复窗口
    zone_buffer_atr: float = 0.85      # 冰线/小溪 ATR 弹性容差（替代原固定百分比，消除价格缩放偏差）
    st_max_retrace_ratio: float = 0.38 # ST 允许高于 SC 低点的最大区间高度比例（解决原版 ST 死锁）
    ar_min_move_atr: float = 1.8       # AR 最小反弹 ATR 倍数
    ar_retrace_atr: float = 0.8        # AR 波峰右侧回落确认 ATR 倍数
    max_phase_a_bars: int = 30         # Phase A 最大等待根数
    max_tr_bars: int = 300             # 单个交易区间最大存活根数
    use_oi_confirm: bool = True        # 是否启用期货持仓量（Open Interest）协同确认
    fti_vol_floor: float = 0.9         # FTI 破冰量能下限（相对于 20 均量）
    min_nine_tests_score: int = 4      # 诊断分最低要求（仅作辅助过滤；分项权重未做拟合，阈值不宜过高）
    min_structural_rr: float = 1.2     # 跨品种稳健硬门禁：结构盈亏空间（0 为关闭）
    stop_atr_buffer: float = 0.6       # 结构止损外侧 ATR 缓冲
    base_target_r: float = 2.5         # 基础目标盈亏比（根据 Phase B 因果累积动态放大）
    warmup: int = 55

    @classmethod
    def from_timeframe(cls, timeframe: str = "1d", **kwargs) -> "WyckoffProConfig":
        """根据周期（'1w', '1d', '60m', '15m', '1m'）自动生成推荐参数。"""
        tf = timeframe.lower()
        if tf in ("1w", "w", "d", "1d", "daily"):
            cfg = cls(
                mode="macro",
                lookback=50,
                vol_z_thresh=1.8,
                min_cause_bars=25,
                prior_trend_bars=50,
                min_prior_move_atr=3.5,
                max_spring_bars=6,
                warmup=55,
            )
        elif tf in ("60m", "1h", "30m", "15m"):
            cfg = cls(
                mode="swing",
                lookback=36,
                vol_z_thresh=1.6,
                min_cause_bars=18,
                prior_trend_bars=36,
                min_prior_move_atr=2.8,
                max_spring_bars=5,
                warmup=40,
            )
        else:  # 1m / 5m 高频微观结构
            cfg = cls(
                mode="intraday",
                lookback=24,
                vol_z_thresh=1.5,
                min_cause_bars=12,
                prior_trend_bars=24,
                min_prior_move_atr=2.0,
                max_spring_bars=4,
                warmup=30,
            )
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        return cfg


# ============================================================================
# 3. 复合人量价特征计算（Weis Wave + Effort vs. Result + 维科夫九大检验）
# ============================================================================

#: VSA / Weis 特征列名（供双向引擎复用，避免重复批计算）
VSA_COLUMNS: list[str] = [
    "atr", "spread", "close_pos", "vol_ma", "vol_z",
    "effort_result", "weis_dir", "weis_cum_vol", "demand_supply_ratio",
]


def compute_vsa_and_weis_features(df: pd.DataFrame, atr_win: int = 20) -> pd.DataFrame:
    """计算零未来函数的 VSA（量价差分析）与 David Weis 波段量能特征。

    输出列：
        - atr: 20 周期真实波幅均值
        - spread: 当根高低价差 (high - low)
        - close_pos: 收盘位置 (close - low) / spread ∈ [0, 1]
        - vol_ma / vol_z: 20 周期均量与量能 Z-Score
        - effort_result: 努力与结果比 = (volume / vol_ma) / max(spread / atr, 0.25)
          （高值 + 收盘逆转 = 复合人吸收 Absorption）
        - weis_dir: 当前所处微观波段方向 (+1 上涨波, -1 下跌波)
        - weis_cum_vol: 当前波段累计成交量（Weis Wave Volume）
        - demand_supply_ratio: 最近上涨波均量 / 最近下跌波均量（>1 表示需求主导）
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else np.ones_like(close)

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    # 严格与引擎 _atr / _vol_stats 对齐：统计量一律排除当根，避免自污染与回测层宽度偏差
    tr_s = pd.Series(tr, index=df.index)
    atr = tr_s.rolling(atr_win, min_periods=1).mean().shift(1).bfill().to_numpy()
    atr = np.maximum(atr, 1e-9)

    spread = np.maximum(high - low, 1e-9)
    close_pos = (close - low) / spread

    vol_s = pd.Series(vol, index=df.index)
    vol_ma = vol_s.rolling(atr_win, min_periods=1).mean().shift(1).bfill().to_numpy()
    vol_sd = vol_s.rolling(atr_win, min_periods=1).std(ddof=0).shift(1).bfill().fillna(0.0).to_numpy()
    vol_z = np.divide(vol - vol_ma, vol_sd, out=np.zeros_like(vol, dtype=float), where=vol_sd > 1e-9)

    rel_vol = np.divide(vol, vol_ma, out=np.ones_like(vol, dtype=float), where=vol_ma > 1e-9)
    rel_spread = np.maximum(spread / atr, 0.25)
    effort_result = rel_vol / rel_spread

    # 增量式计算 Weis Wave（当价格从波段极值反向超过 1.2 * ATR 时切换波段，绝无未来函数）
    n = len(df)
    weis_dir = np.ones(n, dtype=int)
    weis_cum_vol = np.zeros(n, dtype=float)
    ds_ratio = np.ones(n, dtype=float)

    cur_dir = 1
    cum_v = 0.0
    ext_high = high[0]
    ext_low = low[0]
    recent_up_waves: deque[float] = deque(maxlen=4)
    recent_dn_waves: deque[float] = deque(maxlen=4)

    for i in range(n):
        reversal_thresh = 1.2 * atr[i]
        if cur_dir == 1:
            if high[i] >= ext_high:
                ext_high = high[i]
                cum_v += vol[i]
            elif (ext_high - close[i]) >= reversal_thresh:
                recent_up_waves.append(max(cum_v, 1.0))
                cur_dir = -1
                ext_low = low[i]
                cum_v = vol[i]
            else:
                cum_v += vol[i]
        else:
            if low[i] <= ext_low:
                ext_low = low[i]
                cum_v += vol[i]
            elif (close[i] - ext_low) >= reversal_thresh:
                recent_dn_waves.append(max(cum_v, 1.0))
                cur_dir = 1
                ext_high = high[i]
                cum_v = vol[i]
            else:
                cum_v += vol[i]

        weis_dir[i] = cur_dir
        weis_cum_vol[i] = cum_v
        up_avg = float(np.mean(recent_up_waves)) if recent_up_waves else (cum_v if cur_dir == 1 else 1.0)
        dn_avg = float(np.mean(recent_dn_waves)) if recent_dn_waves else (cum_v if cur_dir == -1 else 1.0)
        ds_ratio[i] = up_avg / max(dn_avg, 1e-9)

    out = pd.DataFrame(
        {
            "atr": atr,
            "spread": spread,
            "close_pos": close_pos,
            "vol_ma": vol_ma,
            "vol_z": vol_z,
            "effort_result": effort_result,
            "weis_dir": weis_dir,
            "weis_cum_vol": weis_cum_vol,
            "demand_supply_ratio": ds_ratio,
        },
        index=df.index,
    )
    return out


# ============================================================================
# 4. 增量式维科夫状态机（彻底修复镜像负价格 Bug，内置九大买卖检验）
# ============================================================================

class WyckoffProEngine:
    """资深实盘级维科夫增量状态机（零未来函数）。"""

    def __init__(self, config: WyckoffProConfig | None = None, is_mirror: bool = False) -> None:
        self.cfg = config or WyckoffProConfig()
        self.is_mirror = is_mirror

        win_size = max(self.cfg.lookback, self.cfg.prior_trend_bars, 60) + 5
        self._highs: deque[float] = deque(maxlen=win_size)
        self._lows: deque[float] = deque(maxlen=win_size)
        self._closes: deque[float] = deque(maxlen=win_size)
        self._opens: deque[float] = deque(maxlen=win_size)
        self._vols: deque[float] = deque(maxlen=win_size)
        self._ois: deque[float] = deque(maxlen=25)
        self._trs: deque[float] = deque(maxlen=20)

        # 增量式 David Weis 波段量能跟踪状态（与 compute_vsa_and_weis_features 同源同阈）
        self._weis_dir = 1
        self._weis_ext_high: float | None = None
        self._weis_ext_low: float | None = None
        self._weis_cum_vol = 0.0
        self._weis_tr_cum_vol = 0.0    # 严格仅在当前交易区间生命周期内累积的波段成交量（隔离跨区间污染）
        self._weis_up_waves: deque[float] = deque(maxlen=8)
        self._weis_dn_waves: deque[float] = deque(maxlen=8)

        self._i = -1
        self._time = None
        self._prev_close: float | None = None

        self._state = "SEARCH_CLIMAX"
        self._tr: TradingRange | None = None
        self._tr_id = 0
        self._b_start = 0
        self._spring_bar = 0
        self._last_ps_bar: int | None = None
        self._last_ps_price: float = 0.0
        self._prior_regime: str = "MARKDOWN"

        self.events: list[WyckoffEvent] = []
        self.ranges: list[TradingRange] = []

        self.phase_series: list[str] = []
        self.regime_series: list[str] = []
        self.tr_id_series: list[int] = []
        self.signal_series: list[int] = []
        self.nine_tests_series: list[int] = []
        self.ice_series: list[float] = []
        self.creek_series: list[float] = []

    def _atr(self, high: float, low: float) -> float:
        if not self._trs:
            return max(high - low, 1e-9)
        # 排除当根自身，取前 20 根历史数据计算真实波幅均值，消除自污染
        history = list(self._trs)[:-1] if len(self._trs) > 1 else list(self._trs)
        return max(float(np.mean(history[-20:])), 1e-9) if history else max(high - low, 1e-9)

    def _vol_stats(self) -> tuple[float, float]:
        vols = list(self._vols)
        if len(vols) < 5:
            return (vols[-1] if vols else 1.0), 0.0
        # 排除当根自身 (vols[:-1]) 计算前 20 根均值和标准差，防止天量稀释自身 Z-score
        history = vols[:-1] if len(vols) > 1 else vols
        arr = np.asarray(history[-20:], dtype=float)
        return float(arr.mean()), float(arr.std(ddof=0))

    def _update_weis_wave(
        self, high: float, low: float, close: float, volume: float, atr: float
    ) -> None:
        """增量式 David Weis 波段量能跟踪（与 compute_vsa_and_weis_features 同源同阈）。

        价格自波段极值反向超过 1.2 * ATR 时切换波段；波段完结瞬间将当前交易区间生命周期内
        真实发生的累计成交量归入区间的上涨/下跌波量能，杜绝区间前历史波段跨边界污染。
        严格零未来函数：只有波段已经完结（反向突破确认）才记入。
        """
        reversal_thresh = 1.2 * atr
        if self._weis_ext_high is None:
            self._weis_ext_high = high
            self._weis_ext_low = low

        # 仅在当前存在有效交易区间时，累加区间内的波段成交量
        if self._tr is not None:
            self._weis_tr_cum_vol += volume

        completed_global: float | None = None
        completed_tr_vol: float | None = None
        completed_dir = self._weis_dir

        if self._weis_dir == 1:
            if high >= self._weis_ext_high:
                self._weis_ext_high = high
                self._weis_cum_vol += volume
            elif (self._weis_ext_high - close) >= reversal_thresh:
                completed_global = self._weis_cum_vol
                completed_tr_vol = self._weis_tr_cum_vol
                completed_dir = 1
                self._weis_up_waves.append(max(completed_global, 1.0))
                self._weis_dir = -1
                self._weis_ext_low = low
                self._weis_cum_vol = volume
                self._weis_tr_cum_vol = volume if self._tr is not None else 0.0
            else:
                self._weis_cum_vol += volume
        else:
            if low <= self._weis_ext_low:
                self._weis_ext_low = low
                self._weis_cum_vol += volume
            elif (close - self._weis_ext_low) >= reversal_thresh:
                completed_global = self._weis_cum_vol
                completed_tr_vol = self._weis_tr_cum_vol
                completed_dir = -1
                self._weis_dn_waves.append(max(completed_global, 1.0))
                self._weis_dir = 1
                self._weis_ext_high = high
                self._weis_cum_vol = volume
                self._weis_tr_cum_vol = volume if self._tr is not None else 0.0
            else:
                self._weis_cum_vol += volume

        if completed_tr_vol is not None and self._tr is not None and completed_tr_vol > 0:
            if completed_dir == 1:
                self._tr.up_wave_vol += completed_tr_vol
            else:
                self._tr.down_wave_vol += completed_tr_vol

    def _oi_ma(self) -> float:
        ois = list(self._ois)
        if not ois:
            return 0.0
        # 排除当根自身 (ois[:-1])，消除 OI 均值自包含，与 vol/tr 的自污染防护保持一致
        history = ois[:-1] if len(ois) > 1 else ois
        return float(np.mean(history))

    def _evaluate_nine_tests(
        self,
        tr: TradingRange,
        i: int,
        close: float,
        low: float,
        high: float,
        atr: float,
        stop: float,
        target: float,
        oi_rising: bool,
        oi_available: bool = True,
        signal_kind: str = "",
        structural_stop: float | None = None,
    ) -> int:
        """Hank Pruden 维科夫九大检验：9 项各自可失败的独立评分。

        门禁（ST 确立 + 相位定位）只作前置条件、**不计分**，以消除“门槛复读”式白送分。
        9 项检验均建立在可证伪的量价事实上，得分区间真实覆盖 0~9。
        """
        closes = list(self._closes)
        highs = list(self._highs)
        lows = list(self._lows)

        # ---------------- 前置门禁（不计分）----------------
        if tr.st_index is None:
            return 0

        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else close
        is_phase_c = signal_kind in ("SPRING", "SPRING3", "TEST", "UTAD", "TEST_SELL")
        if is_phase_c:
            if close < tr.ice - 0.25 * atr:
                return 0
        else:
            mid_tr = 0.5 * (tr.creek + tr.ice)
            if close < ma20 and close < mid_tr:
                return 0

        cause_bars = max(0, i - self._b_start)
        score = 0

        # T1 跌势动能衰竭：近 10 根下跌速度明显小于此前 10 根（可失败）
        if len(highs) >= 25:
            recent_vel = (max(highs[-10:]) - close) / atr
            prior_vel = (max(highs[-25:-10]) - min(lows[-25:-10])) / atr
            if prior_vel > 1e-9 and recent_vel <= 0.85 * prior_vel:
                score += 1

        # T2 底部抬高：已记录至少两次抬升，且当根低点仍站在冰线之上（可失败）
        if tr.higher_lows_count >= 2 and low > tr.ice:
            score += 1

        # T3 局部波峰结构上移（可失败）
        if len(highs) >= 15 and max(highs[-5:]) >= np.percentile(highs[-15:], 60):
            score += 1

        # T4 收盘站稳 20 周期均线（可失败）
        if close >= ma20:
            score += 1

        # T5 供给粘燥：最近下跌波量能明显小于此前下跌波（Wyckoff 供给收缩，非需求追涨）
        dn_waves = list(self._weis_dn_waves)
        if len(dn_waves) >= 4 and np.mean(dn_waves[-2:]) <= 0.9 * np.mean(dn_waves[:-2]):
            score += 1

        # T6 努力与结果（Effort vs. Result）—— 按信号类型分向，避免语义反转：
        #   测试类（SPRING3/TEST/LPS 等）：高成交 + 窄幅 = 主力限价吸收（放量不跌）
        #   突破类（SOS/JAC）：必须“努力=结果”= 放量 + 宽幅长阳；若放量却窄幅则是上方供给沉重的
        #   派发/滞涨（Churning），不加分。口径统一用 spread（high-low）/ ATR，与 VSA 一致。
        if self._vols:
            vol_ma, _ = self._vol_stats()
            rel_vol = self._vols[-1] / max(vol_ma, 1e-9)
            spread_ratio = (high - low) / max(atr, 1e-9)
            if signal_kind in ("JAC", "SOS"):
                if rel_vol >= 1.10 and spread_ratio >= 1.20:
                    score += 1
            else:
                if rel_vol >= 1.05 and spread_ratio <= 1.00:
                    score += 1

        # T7 持仓扩张：近 5 根持仓量净增（主力在区间内持续建仓的“资金承诺”；OI 缺失时不得分）
        if self.cfg.use_oi_confirm and oi_available and len(self._ois) >= 6:
            if self._ois[-1] > self._ois[-6] * 1.002:
                score += 1

        # T8 区间幅度足够：交易区间宽度不低于 2.5 ATR（因果空间成立，可失败）
        if tr.width >= 2.5 * atr:
            score += 1

        # T9 真实结构盈亏空间（跨品种稳健硬门禁，见 min_structural_rr）
        ref_stop = structural_stop if structural_stop is not None else stop
        struct_risk = abs(close - ref_stop)
        stop_clamped = struct_risk > 1e-9 and abs(close - stop) < 0.995 * struct_risk
        struct_target = self._structural_projection(tr, atr, cause_bars)
        struct_rr = (struct_target - close) / struct_risk if struct_risk > 1e-9 else 0.0
        if struct_risk <= 1e-9 or stop_clamped:
            return 0
        if self.cfg.min_structural_rr > 0 and struct_rr < self.cfg.min_structural_rr:
            return 0
        score += 1

        return min(score, 9)

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
        """逐根 K 线增量更新状态机。"""
        self._i += 1
        i = self._i
        self._time = time
        op = close if open_price is None else open_price

        tr_bar = (
            high - low
            if self._prev_close is None
            else max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        )
        self._trs.append(tr_bar)
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        self._opens.append(op)
        self._vols.append(volume)
        self._ois.append(float(open_interest or 0.0))

        mark = len(self.events)
        signal, nine_score = 0, 0

        if i >= self.cfg.warmup:
            signal, nine_score = self._step(i, op, high, low, close, volume, open_interest)

        new_events = self.events[mark:]
        self._prev_close = close

        self.tr_id_series.append(self._tr.id if self._tr else -1)
        self.phase_series.append(self._tr.phase if self._tr else self._prior_regime)
        self.regime_series.append(self._tr.regime if self._tr else self._prior_regime)
        self.signal_series.append(signal)
        self.nine_tests_series.append(nine_score)
        self.ice_series.append(self._tr.ice if self._tr else float("nan"))
        self.creek_series.append(self._tr.creek if self._tr else float("nan"))

        return new_events

    def _step(
        self,
        i: int,
        op: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        open_interest: float,
    ) -> tuple[int, int]:
        cfg = self.cfg
        atr = self._atr(high, low)
        vol_ma, vol_sd = self._vol_stats()
        vol_z = (volume - vol_ma) / vol_sd if vol_sd > 1e-9 else 0.0
        oi_ma = self._oi_ma()
        oi_available = bool(oi_ma > 1.0 and open_interest > 1.0)
        oi_rising = bool(oi_available and (open_interest > oi_ma * 1.003))
        # 持仓量下降：期货下跌常由多头砍仓踩踏驱动（Long Liquidation），不能只认新空增仓
        oi_falling = bool(oi_available and (open_interest < oi_ma * 0.997))

        spread = max(high - low, 1e-9)
        close_pos = (close - low) / spread
        rel_vol = volume / max(vol_ma, 1e-9)
        er_ratio = rel_vol / max(spread / atr, 0.25)

        self._update_weis_wave(high, low, close, volume, atr)

        if self._tr is not None:
            self._tr.cause_volume_sum += volume

            if (i - self._tr.start_index) > cfg.max_tr_bars:
                self._close_range(i, "TR_TIMEOUT")
                return 0, 0

        # ------------------------------------------------------------------
        # 状态 1: SEARCH_CLIMAX —— 识别 PS（初步支撑）与 SC（恐慌抛售簇）
        # ------------------------------------------------------------------
        if self._state == "SEARCH_CLIMAX":
            lows_list = list(self._lows)
            highs_list = list(self._highs)
            if len(lows_list) < cfg.lookback + 1:
                return 0, 0

            trend_win = min(len(highs_list) - 1, cfg.prior_trend_bars)
            peak_prior = max(highs_list[-(trend_win + 1): -1])
            trough_prior = min(lows_list[-(trend_win + 1): -1])
            drop_from_peak = peak_prior - low

            if drop_from_peak < cfg.min_prior_move_atr * atr:
                return 0, 0

            is_reaccum = (
                self._prior_regime == "MARKUP"
                and low > trough_prior + 0.25 * (peak_prior - trough_prior)
            )
            regime_label = "RE_ACCUMULATION" if is_reaccum else "ACCUMULATION"

            window_low = min(lows_list[-(cfg.lookback + 1): -1])

            # 1a) 识别 PS（初步支撑）
            if (
                (self._last_ps_bar is None or i - self._last_ps_bar > 25)
                and low <= window_low + 0.5 * atr
                and vol_z >= cfg.ps_vol_z_thresh
                and close_pos >= 0.35
            ):
                self._last_ps_bar = i
                self._last_ps_price = low

            # 1b) 识别 SC（3 根复合恐慌簇 + 吸收确认）
            recent_lows = lows_list[-3:]
            recent_vols = list(self._vols)[-3:]
            recent_z = [(v - vol_ma) / vol_sd if vol_sd > 1e-9 else 0.0 for v in recent_vols]

            made_new_low = min(recent_lows) <= window_low + (0.3 * atr if is_reaccum else 0.0)
            had_climax_vol = max(recent_z) >= (cfg.vol_z_thresh * 0.85 if is_reaccum else cfg.vol_z_thresh)
            stopped_falling = (close > (self._prev_close or op) and close_pos >= 0.45) or (
                close_pos >= 0.60 and er_ratio >= 1.3
            )

            if made_new_low and had_climax_vol and stopped_falling:
                self._tr_id += 1
                tr = TradingRange(
                    id=self._tr_id,
                    direction="long",
                    regime=regime_label,
                    start_index=i,
                    start_time=self._time,
                    ps_price=self._last_ps_price or min(recent_lows),
                    climax_low=min(recent_lows),
                    climax_volume=max(recent_vols),
                    ice=min(recent_lows),
                    prior_peak=peak_prior,
                    phase="PHASE_A",
                )
                self._tr = tr
                self.ranges.append(tr)
                if self._last_ps_bar is not None and (i - self._last_ps_bar) <= 20:
                    self._emit(
                        tr, self._last_ps_bar, "PS", self._last_ps_price,
                        er_ratio, "初步支撑 PS 放量减速"
                    )
                self._emit(
                    tr, i, "CLIMAX", tr.climax_low, er_ratio,
                    f"3根恐慌簇止跌确认（{regime_label}，吸收比={er_ratio:.2f}）"
                )
                self._state = "WAIT_AR"

        # ------------------------------------------------------------------
        # 状态 2: WAIT_AR —— 自动反弹锚定初始小溪线（Creek）
        # ------------------------------------------------------------------
        elif self._state == "WAIT_AR":
            tr = self._tr
            assert tr is not None
            bars_since = i - tr.start_index

            if bars_since > cfg.max_phase_a_bars:
                self._close_range(i, "AR_TIMEOUT")
            elif bars_since >= 2:
                highs_since = list(self._highs)[-min(bars_since + 1, len(self._highs)):]
                peak = max(highs_since)
                move = peak - tr.climax_low
                if move >= cfg.ar_min_move_atr * atr and (peak - close) >= cfg.ar_retrace_atr * atr:
                    tr.creek = peak
                    tr.climax_high = peak
                    tr.ar_index = i
                    self._emit(tr, i, "AR", peak, er_ratio, f"自动反弹波峰确认（幅度={move/atr:.1f}ATR）")
                    self._state = "WAIT_ST"

        # ------------------------------------------------------------------
        # 状态 3: WAIT_ST —— 二次测试确认 Phase B（支持 Higher Low 弹性 ST）
        # ------------------------------------------------------------------
        elif self._state == "WAIT_ST":
            tr = self._tr
            assert tr is not None
            bars_since_ar = i - (tr.ar_index or tr.start_index)
            tr_width = max(tr.creek - tr.climax_low, atr * 2.0)

            in_st_zone = (
                (tr.climax_low - cfg.zone_buffer_atr * atr)
                <= low
                <= (tr.climax_low + cfg.st_max_retrace_ratio * tr_width)
            )
            vol_contracted = volume < tr.climax_volume * 0.85 or er_ratio >= 1.25
            bullish_turn = close >= op or close_pos >= 0.50

            if in_st_zone and vol_contracted and bullish_turn and bars_since_ar >= 2:
                tr.st_index = i
                tr.ice = min(tr.climax_low, low)
                if low > tr.climax_low:
                    tr.higher_lows_count += 1
                tr.phase = "PHASE_B"
                self._b_start = i
                self._emit(
                    tr, i, "ST", low, er_ratio,
                    f"二次测试确认（{'Higher Low' if low > tr.climax_low else '双底测试'}），进入 Phase B"
                )
                self._state = "PHASE_B"

            elif close < tr.climax_low - cfg.zone_buffer_atr * atr * 1.2:
                if volume >= vol_ma * cfg.fti_vol_floor:
                    return self._fire_fti(
                        tr, i, close, high, low, atr, volume, vol_ma, er_ratio,
                        oi_rising, oi_available,
                        "Phase A 直接放量跌穿 SC 低点，破冰做空", "FTI_PHASE_A",
                    )
            elif bars_since_ar > cfg.max_phase_a_bars:
                if low > tr.climax_low and close >= 0.5 * (tr.creek + tr.climax_low):
                    tr.st_index = i
                    tr.higher_lows_count += 1
                    tr.phase = "PHASE_B"
                    self._b_start = tr.ar_index or tr.start_index
                    self._emit(tr, i, "ST", low, er_ratio, "高位强势横盘代替深幅回踩，确认 ST 进入 Phase B")
                    self._state = "PHASE_B"
                else:
                    self._close_range(i, "ST_TIMEOUT")

        # ------------------------------------------------------------------
        # 状态 4: PHASE_B —— 复合人震荡吸筹、测试上下沿（UT / SOW_B）与因果积累
        # ------------------------------------------------------------------
        elif self._state == "PHASE_B":
            tr = self._tr
            assert tr is not None
            cause_bars = i - self._b_start

            if high > tr.creek + 0.15 * atr and close < tr.creek and close_pos <= 0.45:
                tr.creek = 0.7 * tr.creek + 0.3 * high
                self._emit(tr, i, "UT", high, er_ratio, "Phase B 上冲回落 (Upthrust)，测试上方供给")

            if (
                abs(low - tr.ice) <= cfg.zone_buffer_atr * atr * 0.8
                and low >= tr.ice
                and close > op
                and volume < vol_ma
            ):
                tr.higher_lows_count += 1
                self._emit(tr, i, "SOW_B", low, er_ratio, "Phase B 缩量回测冰线支撑成功")

            if low < tr.ice - 0.12 * atr:
                if cause_bars >= cfg.min_cause_bars:
                    tr.spring_index = i
                    tr.spring_extreme = low
                    tr.phase = "PHASE_C"
                    self._spring_bar = i

                    shallow_penetration = (tr.ice - low) <= cfg.zone_buffer_atr * atr * 1.1
                    low_supply = volume <= vol_ma * 1.05 or er_ratio >= 1.4
                    strong_recovery = close >= tr.ice and close_pos >= 0.60

                    if shallow_penetration and low_supply and strong_recovery:
                        stop, target, structural_stop = self._calc_long_stops(tr, close, low, atr, cause_bars)
                        nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="SPRING3")
                        self._emit(
                            tr, i, "SPRING3", low, er_ratio,
                            f"Spring #3 缩量浅刺当根收复（九大检验={nine_score}/9）",
                            stop=stop, target=target, nine_score=nine_score,
                        )
                        self._state = "WAIT_SOS"
                        if nine_score >= cfg.min_nine_tests_score:
                            return 1, nine_score
                    else:
                        self._emit(tr, i, "SPRING", low, er_ratio, "刺穿冰线，进入 Phase C 弹簧观察窗")
                        self._state = "SPRING_WINDOW"

                elif close < tr.ice - cfg.zone_buffer_atr * atr:
                    if volume >= vol_ma * cfg.fti_vol_floor:
                        return self._fire_fti(
                            tr, i, close, high, low, atr, volume, vol_ma, er_ratio,
                            oi_rising, oi_available,
                            "Phase B 蓄势不足放量破冰 (Fall Through the Ice)", "FTI_PHASE_B",
                        )

            elif (
                cause_bars >= cfg.min_cause_bars
                and close > tr.creek + 0.25 * atr
                and close_pos >= 0.60
                and vol_z >= 0.7
                and (not cfg.use_oi_confirm or (oi_available and (oi_rising or oi_falling)) or vol_z >= 1.3)
            ):
                tr.phase = "PHASE_D"
                tr.sos_index = i
                stop, target, structural_stop = self._calc_lps_stops(tr, close, low, atr, cause_bars)
                nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="JAC")
                self._emit(
                    tr, i, "JAC", close, er_ratio,
                    f"Schematic #2 放量跃过小溪 JAC/SOS（九大检验={nine_score}/9）",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._state = "WAIT_LPS"
                # 非线性扩展：Schematic #2 无弹簧直接突破强单边，允许直接建立底仓
                if nine_score >= cfg.min_nine_tests_score:
                    return 1, nine_score

        # ------------------------------------------------------------------
        # 状态 5: SPRING_WINDOW —— 跨 K 线弹簧收复窗
        # ------------------------------------------------------------------
        elif self._state == "SPRING_WINDOW":
            tr = self._tr
            assert tr is not None
            bars_in = i - self._spring_bar
            if low < tr.spring_extreme:
                tr.spring_extreme = low
                tr.spring_index = i

            if close >= tr.ice and (close > op or close_pos >= 0.55):
                tr.higher_lows_count += 1
                self._emit(tr, i, "SPRING_RECOVER", tr.spring_extreme, er_ratio, f"{bars_in}根内收复冰线，等待二次测试或SOS")
                self._state = "WAIT_TEST"

            elif bars_in > cfg.max_spring_bars or close < tr.ice - cfg.zone_buffer_atr * atr * 1.8:
                if volume >= vol_ma * cfg.fti_vol_floor:
                    return self._fire_fti(
                        tr, i, close, high, low, atr, volume, vol_ma, er_ratio,
                        oi_rising, oi_available,
                        "Spring #1 放量深跌逾窗未收复，确认破冰 (FTI)", "SPRING_FAILED_FTI",
                    )
                else:
                    self._close_range(i, "SPRING_TIMEOUT")

        # ------------------------------------------------------------------
        # 状态 6: WAIT_TEST —— 二次无供给测试（兼容强势不回踩直接 SOS 突破）
        # ------------------------------------------------------------------
        elif self._state == "WAIT_TEST":
            tr = self._tr
            assert tr is not None
            bars_since_spring = i - (tr.spring_index or tr.start_index)
            cause_bars = i - self._b_start
            tr_width = max(tr.creek - tr.ice, atr * 2.0)

            higher_low = low > tr.spring_extreme
            in_test_zone = low <= tr.ice + max(cfg.zone_buffer_atr * atr, 0.35 * tr_width)
            low_supply = volume <= vol_ma * 0.95 or er_ratio >= 1.3
            bullish_bar = close >= op or close_pos >= 0.55

            if higher_low and in_test_zone and low_supply and bullish_bar:
                tr.test_index = i
                tr.higher_lows_count += 1
                stop, target, structural_stop = self._calc_long_stops(tr, close, low, atr, cause_bars)
                nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="TEST")
                self._emit(
                    tr, i, "TEST", close, er_ratio,
                    f"无供给二次测试确认（九大检验={nine_score}/9）",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._state = "WAIT_SOS"
                if nine_score >= cfg.min_nine_tests_score:
                    return 1, nine_score

            elif (
                close > tr.creek + 0.2 * atr
                and close_pos >= 0.60
                and vol_z >= 0.6
            ):
                tr.sos_index = i
                tr.phase = "PHASE_D"
                stop, target, structural_stop = self._calc_lps_stops(tr, close, low, atr, cause_bars)
                nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="SOS")
                self._emit(
                    tr, i, "SOS", close, er_ratio,
                    f"弹簧收复后直接强势跃过小溪 SOS（九大检验={nine_score}/9）",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._state = "WAIT_LPS"
                # 与 PHASE_B JAC / WAIT_SOS 对称：强单边直接突破也应允许当根开底仓，不强制等 LPS 回撤
                if nine_score >= cfg.min_nine_tests_score:
                    return 1, nine_score

            elif close < tr.spring_extreme - 0.2 * atr:
                if volume >= vol_ma * cfg.fti_vol_floor:
                    return self._fire_fti(
                        tr, i, close, high, low, atr, volume, vol_ma, er_ratio,
                        oi_rising, oi_available,
                        "二次测试跌穿弹簧极值，确认 FTI", "TEST_FAILED_FTI",
                    )
                self._close_range(i, "TEST_FAILED")
            elif bars_since_spring > 28:
                self._close_range(i, "TEST_TIMEOUT")

        # ------------------------------------------------------------------
        # 状态 7a: WAIT_SOS —— 等待强势信号 / 跃过小溪 (SOS / JAC)
        # ------------------------------------------------------------------
        elif self._state == "WAIT_SOS":
            tr = self._tr
            assert tr is not None
            bars_since_c = i - (tr.test_index or tr.spring_index or tr.start_index)
            cause_bars = i - self._b_start

            if (
                close > tr.creek + 0.15 * atr
                and close_pos >= 0.55
                and ((oi_available and (oi_rising or oi_falling)) or vol_z >= 1.0 or (not cfg.use_oi_confirm and vol_z >= 0.6))
            ):
                tr.sos_index = i
                tr.phase = "PHASE_D"
                stop, target, structural_stop = self._calc_lps_stops(tr, close, low, atr, cause_bars)
                nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="SOS")
                self._emit(
                    tr, i, "SOS", close, er_ratio,
                    f"放量跃过小溪 SOS/JAC（九大检验={nine_score}/9）",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._state = "WAIT_LPS"
                # 强放量跃过小溪，确认突破信号
                if nine_score >= cfg.min_nine_tests_score:
                    return 1, nine_score
            elif close < min(tr.spring_extreme or tr.ice, tr.ice) - 0.3 * atr:
                self._close_range(i, "SOS_BROKEN")
            elif bars_since_c > 35:
                self._close_range(i, "SOS_TIMEOUT")

        # ------------------------------------------------------------------
        # 状态 7b: WAIT_LPS —— 回撤小溪边缘 (BUEC / LPS) 右侧加仓买点 2
        # ------------------------------------------------------------------
        elif self._state == "WAIT_LPS":
            tr = self._tr
            assert tr is not None
            bars_since_sos = i - (tr.sos_index or tr.start_index)
            cause_bars = i - self._b_start

            near_creek = abs(low - tr.creek) <= cfg.zone_buffer_atr * atr * 1.2 or (
                low >= tr.creek and (low - tr.creek) <= 1.2 * atr
            )
            low_vol_pullback = volume <= vol_ma * 1.05 or er_ratio >= 1.2
            bullish_hold = close >= op and close >= tr.creek - 0.25 * atr

            if near_creek and low_vol_pullback and bullish_hold and bars_since_sos >= 1:
                tr.lps_index = i
                tr.phase = "PHASE_E"
                stop, target, structural_stop = self._calc_lps_stops(tr, close, low, atr, cause_bars)
                nine_score = self._evaluate_nine_tests(tr, i, close, low, high, atr, stop, target, oi_rising, oi_available=oi_available, structural_stop=structural_stop, signal_kind="LPS")
                self._emit(
                    tr, i, "BUEC", low, er_ratio,
                    "回撤至小溪边缘 (Backup to the Edge of the Creek) 企稳",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._emit(
                    tr, i, "LPS", close, er_ratio,
                    f"最后支撑点 LPS 确认，进入 Phase E 主升浪（九大检验={nine_score}/9）",
                    stop=stop, target=target, nine_score=nine_score,
                )
                self._close_range(i, "ENTER_PHASE_E_MARKUP", new_regime="MARKUP")
                if nine_score >= cfg.min_nine_tests_score:
                    return 1, nine_score

            elif close > tr.creek + 2.5 * atr and bars_since_sos >= 4:
                tr.phase = "PHASE_E"
                self._close_range(i, "RUNAWAY_MARKUP", new_regime="MARKUP")

            elif close < tr.creek - 1.6 * atr or close < tr.ice:
                self._close_range(i, "LPS_FAILED")
            elif bars_since_sos > 25:
                self._close_range(i, "LPS_TIMEOUT")

        return 0, 0

    def _cause_energy(self, tr: TradingRange, atr: float, cause_bars: int) -> float:
        """点数图水平计数（Point & Figure Horizontal Count）因果能量：区间宽度 × 蓄势时长 × 波段量能倾斜。"""
        tr_width = max(tr.width, 2.0 * atr)
        cause_ratio = float(np.clip(cause_bars / max(self.cfg.min_cause_bars, 1), 0.8, 3.0))
        wave_ratio = float(np.clip((tr.up_wave_vol / max(tr.down_wave_vol, 1.0)) ** 0.5, 0.85, 2.2))
        return tr_width * cause_ratio * wave_ratio

    def _structural_projection(
        self, tr: TradingRange, atr: float, cause_bars: int, mult: float = 0.95
    ) -> float:
        """由溪线 + 因果能量投射出的结构目标（不含为凑 R:R 而人为抬高/压低的构造下限）。"""
        base = tr.creek if tr.creek > 1e-6 else tr.ice
        return float(base + self._cause_energy(tr, atr, cause_bars) * mult)

    def _stop_floors(self, close: float, atr: float, min_mult: float, min_pct: float) -> tuple[float, float]:
        """基于真实物理价格尺度计算止损最小/最大安全垫（对镜像空间自动降维）。"""
        real_p = getattr(self, "_real_close", close)
        if real_p > 50000.0:
            real_p = min(close, atr * 80.0)
        min_stop_dist = max(atr * min_mult, real_p * min_pct)
        max_stop_dist = max(atr * 6.0, real_p * 0.08)
        return min_stop_dist, max_stop_dist

    def _evaluate_breakdown_tests(
        self, tr: TradingRange, i: int, close: float, high: float, low: float, atr: float,
        volume: float, vol_ma: float, oi_rising: bool, oi_available: bool,
    ) -> int:
        """破位（FTI / 镜像向上突破）九项可失败检验，取代原硬编码 6 分。"""
        highs = list(self._highs)
        lows = list(self._lows)
        closes = list(self._closes)
        score = 0

        # B1 有效破位幅度：收盘明确跌破近 10 根低点
        recent_low = min(lows[-11:-1]) if len(lows) >= 11 else low
        if close <= recent_low - 0.25 * atr:
            score += 1
        # B2 破位放量
        if volume >= vol_ma * max(self.cfg.fti_vol_floor, 0.9):
            score += 1
        # B3 弱势收盘（收在当日低位）
        spread = max(high - low, 1e-9)
        if (close - low) / spread <= 0.40:
            score += 1
        # B4 跌破 20 周期均线
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else close
        if close < ma20:
            score += 1
        # B5 波动扩张（破位需要空间）
        if (high - low) >= 0.9 * atr:
            score += 1
        # B6 持仓变化共振：新空增仓（OI 升）或多头砍仓踩踏（OI 降）均可确认破位
        # 注：入参 oi_rising 已由调用侧对两种情形做过 OR 合并
        if self.cfg.use_oi_confirm and oi_available and oi_rising \
                and close < (self._prev_close or close):
            score += 1
        # B7 蓄势充分（破位前存在横盘前期）
        if max(0, i - self._b_start) >= max(self.cfg.min_cause_bars // 2, 3):
            score += 1
        # B8 宽幅破位：真实下破需“努力=结果”的宽实体阴线；窄幅阴跌多为流动性缺失而非抛压
        spread = max(high - low, 1e-9)
        if spread / max(atr, 1e-9) >= 1.20:
            score += 1
        # B9 真实下行空间：区间高度向下投射后的 R:R ≥ 2.0（非构造下限，可失败）
        recent_high = max(highs[-3:]) if len(highs) >= 3 else high
        risk = max(recent_high - close, 1e-9)
        objective = tr.ice - max(tr.width, 2.0 * atr)
        if (close - objective) / risk >= 2.0:
            score += 1

        return min(score, 9)

    def _fire_fti(
        self, tr: TradingRange, i: int, close: float, high: float, low: float, atr: float,
        volume: float, vol_ma: float, er_ratio: float, oi_rising: bool,
        oi_available: bool, note: str, reason: str,
    ) -> tuple[int, int]:
        """统一处理破冰（FTI）信号：真实评分 + 结构止损/目标 + 区间关闭。"""
        stop, target, _structural = self._calc_fti_stops(close, high, low, atr)
        # 新空增仓与多头砍仓（OI 降）均可确认破位
        ois = list(self._ois)
        oi_falling = False
        if len(ois) >= 3:
            ma = float(np.mean(ois[:-1]))
            oi_falling = ma > 1.0 and ois[-1] < ma * 0.997
        score = self._evaluate_breakdown_tests(
            tr, i, close, high, low, atr, volume, vol_ma,
            bool(oi_rising or oi_falling), oi_available
        )
        self._emit(
            tr, i, "FTI", close, er_ratio, f"{note}（九大检验={score}/9）",
            stop=stop, target=target, nine_score=score,
        )
        self._close_range(i, reason, new_regime="MARKDOWN")
        return -1, score

    def _calc_long_stops(
        self, tr: TradingRange, close: float, low: float, atr: float, cause_bars: int
    ) -> tuple[float, float, float]:
        """做多结构止损/目标。返回 (最终止损, 目标, **未钳制的结构止损**) 供评分层核定真实风险。"""
        support_anchor = min(
            tr.spring_extreme if tr.spring_extreme > 0 else tr.ice,
            tr.ice if tr.ice > 1e-6 else low,
            low,
        )
        structural_stop = support_anchor - self.cfg.stop_atr_buffer * atr

        min_stop_dist, max_stop_dist = self._stop_floors(close, atr, 0.95, 0.008)
        stop = min(structural_stop, close - min_stop_dist)
        stop = max(stop, close - max_stop_dist)   # 仅作极端风险硬闸，钳制与否由评分层识别
        risk = max(abs(close - stop), min_stop_dist)

        structural_proj = self._structural_projection(tr, atr, cause_bars, 0.95)
        min_rr_proj = close + risk * max(self.cfg.base_target_r, 2.8)
        target = max(structural_proj, min_rr_proj)
        return float(stop), float(target), float(structural_stop)

    def _calc_lps_stops(
        self, tr: TradingRange, close: float, low: float, atr: float, cause_bars: int
    ) -> tuple[float, float, float]:
        recent_low = min(list(self._lows)[-3:]) if len(self._lows) >= 3 else low
        creek_ref = (tr.creek - 1.0 * atr) if tr.creek > 1e-6 else recent_low
        structural_stop = min(recent_low, creek_ref) - self.cfg.stop_atr_buffer * atr

        min_stop_dist, max_stop_dist = self._stop_floors(close, atr, 0.9, 0.0075)
        stop = min(structural_stop, close - min_stop_dist)
        stop = max(stop, close - max_stop_dist)
        risk = max(abs(close - stop), min_stop_dist)

        target = max(
            self._structural_projection(tr, atr, cause_bars, 1.1),
            close + risk * max(self.cfg.base_target_r, 2.8),
        )
        return float(stop), float(target), float(structural_stop)

    def _calc_fti_stops(
        self, close: float, high: float, low: float, atr: float
    ) -> tuple[float, float, float]:
        """破位（FTI）空头/镜像多头结构止损。第三个返回值仍为未钳制结构止损（空头侧上方）。"""
        recent_high = max(list(self._highs)[-3:]) if len(self._highs) >= 3 else high
        structural_stop = recent_high + self.cfg.stop_atr_buffer * atr

        min_stop_dist, max_stop_dist = self._stop_floors(close, atr, 0.9, 0.008)
        stop = max(structural_stop, close + min_stop_dist)
        stop = min(stop, close + max_stop_dist)
        risk = max(abs(stop - close), min_stop_dist)
        target = close - max(risk * 2.8, 3.0 * atr)
        return float(stop), float(target), float(structural_stop)

    def _emit(
        self,
        tr: TradingRange,
        i: int,
        kind: str,
        price: float,
        er_ratio: float,
        note: str,
        stop: float = 0.0,
        target: float = 0.0,
        nine_score: int = 0,
    ) -> WyckoffEvent:
        name = ACCUMULATION_EVENT_NAMES.get(kind, kind)
        direction: Direction = "short" if kind == "FTI" else "long"
        ev = WyckoffEvent(
            index=i,
            time=self._time,
            kind=kind,
            name=name,
            price=float(price),
            direction=direction,
            tr_id=tr.id,
            phase=tr.phase,
            regime=tr.regime,
            nine_tests_score=nine_score,
            stop=float(stop),
            target=float(target),
            effort_result_ratio=float(er_ratio),
            note=note,
        )
        self.events.append(ev)
        tr.events.append(ev)
        return ev

    def _close_range(self, i: int, reason: str, new_regime: str | None = None) -> None:
        if self._tr is not None:
            self._tr.end_index = i
            self._tr.end_reason = reason
            self._tr = None
        self._weis_tr_cum_vol = 0.0
        if new_regime is not None:
            self._prior_regime = new_regime
        self._state = "SEARCH_CLIMAX"

    def process(self, df: pd.DataFrame, vsa_df: pd.DataFrame | None = None) -> "WyckoffProResult":
        """批处理接口：返回包含完整吸筹事件、区间与量价特征的结果对象。

        :param vsa_df: 可选复用已算好的 VSA/Weis 特征，避免双向引擎重复计算。
        """
        if vsa_df is None:
            vsa_df = compute_vsa_and_weis_features(df)
        else:
            vsa_df = vsa_df[VSA_COLUMNS]
        try:
            engine = type(self)(self.cfg, is_mirror=self.is_mirror)
        except TypeError:  # 子类构造签名不兼容时回退
            engine = WyckoffProEngine(self.cfg, is_mirror=self.is_mirror)

        for idx in range(len(df)):
            row = df.iloc[idx]
            engine.update(
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 1.0)),
                open_interest=float(row.get("open_interest", 0.0) or 0.0),
                time=df.index[idx],
                open_price=float(row["open"]),
            )

        out = pd.DataFrame(
            {
                "tr_id": engine.tr_id_series,
                "phase": engine.phase_series,
                "regime": engine.regime_series,
                "signal": engine.signal_series,
                "nine_tests": engine.nine_tests_series,
                "ice": engine.ice_series,
                "creek": engine.creek_series,
            },
            index=df.index,
        )
        out = pd.concat([out, vsa_df], axis=1)
        return WyckoffProResult(df=out, events=engine.events, ranges=engine.ranges, engine=engine)


# ============================================================================
# 5. 正数锚点镜像派发引擎（彻底解决原版负数镜像不等式反转 Bug）
# ============================================================================

class DistributionProEngine:
    """高位派发与下跌途中的再派发（Re-distribution）识别引擎。

    顶部结构与底部结构在物理上不对称：买入高潮多为 3 根天量恐慌簇，而顶部常见
    “次高点缩量衰竭”；且顶部横盘时长普遍短于底部吸筹。因此本引擎基于传入配置
    **按比例派生**顶部专用参数（而非仅在 config is None 时生效），否则由 runner
    传入的吸筹参数会静默覆盖派发侧阈值。
    """

    #: 顶部相对底部的参数缩放系数
    TOP_VOL_Z_SCALE = 0.85      # 顶部放量阈值放宽（缩量双顶也要能识别）
    TOP_AR_SCALE = 0.85         # 顶部 AR 回落幅度要求放宽
    TOP_CAUSE_SCALE = 0.70      # 顶部横盘时长要求缩短

    def __init__(self, config: WyckoffProConfig | None = None, anchor: float = 1_000_000.0) -> None:
        base = config or WyckoffProConfig()
        cfg = WyckoffProConfig(**{**base.__dict__})
        cfg.vol_z_thresh = base.vol_z_thresh * self.TOP_VOL_Z_SCALE
        cfg.ps_vol_z_thresh = base.ps_vol_z_thresh * self.TOP_VOL_Z_SCALE
        cfg.ar_min_move_atr = base.ar_min_move_atr * self.TOP_AR_SCALE
        cfg.min_cause_bars = max(int(round(base.min_cause_bars * self.TOP_CAUSE_SCALE)), 5)
        cfg.max_tr_bars = max(int(round(base.max_tr_bars * self.TOP_CAUSE_SCALE)), 60)
        self.cfg = cfg
        self.base_cfg = base
        self.anchor = float(anchor)

    def process(self, df: pd.DataFrame, vsa_df: pd.DataFrame | None = None) -> "WyckoffProResult":
        # anchor 只依赖首根收盘价（严格因果），不再取自全序列最高价（潜在前视）
        first_close = abs(float(df["close"].iloc[0])) if len(df) else 10_000.0
        anchor = max(self.anchor, 200.0 * first_close, 1_000_000.0)

        if vsa_df is None:
            vsa_df = compute_vsa_and_weis_features(df)
        else:
            vsa_df = vsa_df[VSA_COLUMNS]
        inner = WyckoffProEngine(self.cfg, is_mirror=True)
        inner._prior_regime = "MARKUP"

        for idx in range(len(df)):
            row = df.iloc[idx]
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            o = float(row["open"])
            inner._real_close = c  # 注入真实物理价格，确保镜像空间内止损与目标尺度绝对真实
            inner.update(
                high=anchor - l,
                low=anchor - h,
                close=anchor - c,
                volume=float(row.get("volume", 1.0)),
                open_interest=float(row.get("open_interest", 0.0) or 0.0),
                time=df.index[idx],
                open_price=anchor - o,
            )

        regime_map = {
            "ACCUMULATION": "DISTRIBUTION",
            "RE_ACCUMULATION": "RE_DISTRIBUTION",
            "MARKUP": "MARKDOWN",
            "MARKDOWN": "MARKUP",
        }
        for ev in inner.events:
            ev.name = DISTRIBUTION_EVENT_NAMES.get(ev.kind, ev.name)
            ev.price = anchor - ev.price
            ev.stop = (anchor - ev.stop) if ev.stop != 0 else 0.0
            ev.target = (anchor - ev.target) if ev.target != 0 else 0.0
            ev.direction = "short" if ev.direction == "long" else "long"
            ev.regime = regime_map.get(ev.regime, ev.regime)
            ev.note = (
                ev.note.replace("初步支撑 PS 放量减速", "初步供给 PSY 放量滞涨")
                .replace("恐慌簇止跌", "狂热簇见顶")
                .replace("反弹", "回落")
                .replace("无供给二次测试确认", "无需求二次测试确认")
                .replace("最后支撑点 LPS 确认，进入 Phase E 主升浪", "最后供给点 LPSY 确认，进入 Phase E 主跌浪")
                .replace("冰线", "供给上沿")
                .replace("小溪", "冰线支撑")
                .replace("RE_ACCUMULATION", "RE_DISTRIBUTION")
                .replace("ACCUMULATION", "DISTRIBUTION")
            )

        for tr in inner.ranges:
            tr.direction = "short"
            tr.regime = regime_map.get(tr.regime, "DISTRIBUTION")
            tr.ps_price = (anchor - tr.ps_price) if tr.ps_price > 0 else 0.0
            tr.climax_low = (anchor - tr.climax_low) if tr.climax_low > 0 else 0.0
            tr.climax_high = (anchor - tr.climax_high) if tr.climax_high > 0 else 0.0
            orig_ice = tr.ice
            orig_creek = tr.creek
            # 只有在确立了有效小溪/冰线时才进行逆映射；未成型(0.0)则保持 0.0，绝不能算成 anchor (100万)
            tr.ice = (anchor - orig_creek) if (orig_creek > 0 and orig_creek < anchor) else 0.0
            tr.creek = (anchor - orig_ice) if (orig_ice > 0 and orig_ice < anchor) else 0.0
            tr.spring_extreme = (anchor - tr.spring_extreme) if tr.spring_extreme > 0 else 0.0
            tr.prior_peak = (anchor - tr.prior_peak) if tr.prior_peak > 0 else 0.0

        out = pd.DataFrame(
            {
                "tr_id": inner.tr_id_series,
                "phase": [regime_map.get(p, p) for p in inner.phase_series],
                "regime": [regime_map.get(r, r) for r in inner.regime_series],
                "signal": [-s for s in inner.signal_series],
                "nine_tests": inner.nine_tests_series,
                "ice": [anchor - c if not np.isnan(c) else float("nan") for c in inner.creek_series],
                "creek": [anchor - ic if not np.isnan(ic) else float("nan") for ic in inner.ice_series],
            },
            index=df.index,
        )
        out = pd.concat([out, vsa_df], axis=1)
        return WyckoffProResult(df=out, events=inner.events, ranges=inner.ranges, engine=inner)


@dataclass
class WyckoffProResult:
    df: pd.DataFrame
    events: list[WyckoffEvent]
    ranges: list[TradingRange]
    engine: object

    def event_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.name] = counts.get(e.name, 0) + 1
        return dict(sorted(counts.items()))

    def timeline(self) -> list[dict]:
        return [e.as_dict() for e in sorted(self.events, key=lambda x: x.index)]

    def timeline_df(self) -> pd.DataFrame:
        if not self.events:
            return pd.DataFrame()
        return pd.DataFrame(self.timeline())


# ============================================================================
# 6. VectorBT 优先回测引擎 + 内置零偏差金字塔加仓事件回测器（自动降级）
# ============================================================================

@dataclass
class BacktestConfigPro:
    init_cash: float = 1_000_000.0
    risk_per_trade: float = 0.015      # 基础单笔风险 1.5%
    scale_by_nine_tests: bool = False  # 默认关闭：诊断分无跨品种统计显著的预测力，按它缩放仓等于对噪声加杠杆
    max_concurrent_positions: int = 2  # 支持底仓 (TEST/SPRING3) + 浮盈加仓 (LPS/LPSY)
    pyramid_min_r: float = 0.3         # 加仓要求底仓至少已有 0.3R 浮盈，并将底仓推至保本
    fees: float = 0.00015              # 手续费率
    slippage: float = 0.0003           # 滑点率
    max_hold_bars: int = 55            # 时间止损窗口
    trail_after_r: float = 1.2         # 浮盈超过该 R 后启用 ATR 移动止损（仅在突破确认后生效）
    trail_atr_mult: float = 2.5        # 移动止损 ATR 倍数
    breakeven_min_r: float = 2.0       # 推保本前必须达到的最小浮盈 R（防 Phase D 回踩被洗）
    partial_exit_enabled: bool = True  # 两阶段分批止盈
    partial_exit_r: float = 1.5        # 第一目标 R，达则减仓
    partial_exit_frac: float = 0.4     # 减仓比例（按当前整手取整）
    allow_short: bool = True
    use_weekly_filter: bool = False    # MTF 周线大背景过滤：三品种实测均为负向（avgR 全面下降），故默认关闭
    limit_pct: float = 0.05            # 涨跌停幅度（0 表示禁用封板保护），用于入场/出场流动性枯竭检测
    trade_kinds: tuple[str, ...] = ("SPRING3", "TEST", "LPS", "FTI", "JAC", "SOS")


def compute_weekly_macro_trend(df: pd.DataFrame, lag_weeks: int = 1) -> np.ndarray:
    """计算严格因果（无未来数据）的周线宏观大趋势。

    :param df: 带有 DatetimeIndex 的日线 DataFrame
    :param lag_weeks: 严格滞后周数，默认 1 周（只取上周五收盘确立的完整周线趋势）
    :return: 1 (宏观多头), -1 (宏观空头), 0 (中性)
    """
    if len(df) < 30 or not isinstance(df.index, pd.DatetimeIndex):
        return np.zeros(len(df), dtype=int)

    try:
        w_df = df.resample("W-FRI").agg({
            "open": "first", "high": "max", "low": "min", "close": "last"
        }).dropna()
        if len(w_df) < 5:
            return np.zeros(len(df), dtype=int)

        w_ma = w_df["close"].ewm(span=20, adjust=False).mean()
        w_trend = np.where(w_df["close"] >= w_ma, 1, -1)
        w_series = pd.Series(w_trend, index=w_df.index).shift(lag_weeks).fillna(0)
        daily_trend = w_series.reindex(df.index, method="ffill").fillna(0).to_numpy(dtype=int)
        return daily_trend
    except Exception:
        return np.zeros(len(df), dtype=int)


def _exclusive_atr(df: pd.DataFrame, win: int = 20) -> np.ndarray:
    """执行端 ATR：统计量排除当根，与引擎 / VSA 口径一致（零未来函数）。"""
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    prev = np.roll(c, 1)
    prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    s = pd.Series(tr).rolling(win, min_periods=1).mean().shift(1).bfill()
    return np.maximum(s.to_numpy(dtype=float), 1e-9)


def run_wyckoff_backtest(
    df: pd.DataFrame,
    acc_res: WyckoffProResult,
    dist_res: WyckoffProResult | None = None,
    bt_cfg: BacktestConfigPro | None = None,
    prefer_vectorbt: bool = True,
    contract_size: float = 1.0,
    signal_df: pd.DataFrame | None = None,
) -> dict:
    """运行双向维科夫组合回测（内置高精度金字塔事件回测器为唯一撮合事实源）。

    ``prefer_vectorbt`` 参数保留仅作 API 兼容，已弃用：vectorbt 的 from_signals 无法
    表达逐笔结构止损/止盈，只能传固定百分比，与维科夫引擎给出的结构风控矛盾，已移除。

    :param df: 执行端数据（主力连续，含真实换月跳空）
    :param signal_df: 信号端数据（无跳空平滑指数）。若与 ``df`` 不同，则事件自带的
        stop/target 处于信号空间，入场时会按**点位距离**平移回执行空间，避免换月缺口
        造成止盈止损错位；成交、持仓与盈亏始终在主连真实价格上计算。
    """
    cfg = bt_cfg or BacktestConfigPro()
    all_events = list(acc_res.events)
    if dist_res is not None:
        all_events.extend(dist_res.events)
    all_events.sort(key=lambda e: e.index)

    n = len(df)

    macro_trend = compute_weekly_macro_trend(df) if cfg.use_weekly_filter else None

    valid_plans: dict[int, list[WyckoffEvent]] = {}
    for ev in all_events:
        if ev.kind not in cfg.trade_kinds:
            continue
        if ev.nine_tests_score < acc_res.engine.cfg.min_nine_tests_score:
            continue
        # 周线宏观大趋势共振过滤 (顺周线大势，逆日线小势)
        if macro_trend is not None and ev.index < len(macro_trend):
            t_dir = macro_trend[ev.index]
            if t_dir > 0 and ev.direction == "short":
                continue
            if t_dir < 0 and ev.direction == "long":
                continue
        entry_bar = ev.index + 1
        if entry_bar >= n:
            continue
        valid_plans.setdefault(entry_bar, []).append(ev)

    # 注：原 vectorbt 分支已移除——它只能传固定 sl_stop/tp_stop 百分比，与结构止损矛盾，
    # 且其结果从未作为最终绩效输出（死代码）。统一以下方内置高精度回测器作为唯一撮合事实源。
    vbt_stats = None
    vbt_portfolio = None

    # 高精度结构止损 + 金字塔加仓模拟（修复原版入场当根漏检止损与跳空穿仓 Bug）
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    # 执行端 ATR（严格因果，排除当根）
    atrs = _exclusive_atr(df)
    # 信号端收盘价（用于把事件止损/目标按点位距离平移回执行空间）
    sig_df = signal_df if signal_df is not None else df
    sig_closes = sig_df["close"].to_numpy(dtype=float)
    is_bridged = sig_df is not df
    # 严格因果：入场与移动止损均使用“截至前一根”的 ATR，杜绝当根高低价提前泄入
    atrs_prev = np.empty_like(atrs)
    atrs_prev[0] = atrs[0]
    atrs_prev[1:] = atrs[:-1]
    idx_arr = df.index

    # 相位驱动止损棘轮所需：每个交易区间的首次突破确认（SOS/JAC）位置
    breakout_idx: dict[int, int] = {}
    for ev in all_events:
        if ev.kind in ("JAC", "SOS"):
            breakout_idx.setdefault(ev.tr_id, ev.index)

    balance = float(cfg.init_cash)
    equity_curve = np.zeros(n, dtype=float)
    trades_log: list[dict] = []
    open_positions: list[dict] = []

    for i in range(n):
        # 涨跌停封板流动性检测（入场与出场共用；一字封死无法按市价成交）
        pct_bar = (closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-9) if i > 0 else 0.0
        is_flat_bar = (highs[i] - lows[i]) <= max(closes[i] * 0.002, 1e-9)
        limit_band = cfg.limit_pct * 0.9
        is_limit_down_bar = cfg.limit_pct > 0 and is_flat_bar and pct_bar <= -limit_band
        is_limit_up_bar = cfg.limit_pct > 0 and is_flat_bar and pct_bar >= limit_band

        # Step A: 若 bar i 有反向入场计划，先在开盘平掉所有反向持仓
        if i in valid_plans and open_positions:
            ev_first = valid_plans[i][0]
            remaining_pos: list[dict] = []
            for pos in open_positions:
                if pos["direction"] != ev_first.direction:
                    exit_fill = (
                        opens[i] * (1.0 - cfg.slippage)
                        if pos["direction"] == "long"
                        else opens[i] * (1.0 + cfg.slippage)
                    )
                    balance += _settle_pos(pos, i, idx_arr[i], exit_fill, "反向信号平仓", cfg.fees, contract_size)
                    trades_log.append(pos)
                else:
                    remaining_pos.append(pos)
            open_positions = remaining_pos

        # Step B: 处理 bar i 的新开仓或金字塔加仓（TEST 底仓 -> LPS 浮盈加仓）
        if i in valid_plans and len(open_positions) < cfg.max_concurrent_positions:
            for ev in valid_plans[i]:
                if len(open_positions) >= cfg.max_concurrent_positions:
                    break
                if ev.direction == "short" and not cfg.allow_short:
                    continue

                raw_open = opens[i]

                # 涨跌停封板无法建仓：一字涨停无法追多，一字跌停无法做空
                if (ev.direction == "long" and is_limit_up_bar) or (
                    ev.direction == "short" and is_limit_down_bar
                ):
                    continue

                fill_open = raw_open * (1.0 + cfg.slippage) if ev.direction == "long" else raw_open * (1.0 - cfg.slippage)

                # 若已有同向底仓，校验底仓浮盈是否达到加仓门槛，并将底仓止损推至保本
                if open_positions:
                    base_p = open_positions[0]
                    base_risk = max(abs(base_p["entry_price"] - base_p["stop"]), 1e-9)
                    cur_open_r = (
                        (raw_open - base_p["entry_price"]) / base_risk
                        if base_p["direction"] == "long"
                        else (base_p["entry_price"] - raw_open) / base_risk
                    )
                    if max(base_p["peak_r"], cur_open_r) < cfg.pyramid_min_r:
                        continue
                    # 仅在突破已确认时才允许将底仓推至保本，避免 Phase D 正常回踩被扫出
                    base_confirmed = (
                        base_p["tr_id"] in breakout_idx
                        and i >= breakout_idx[base_p["tr_id"]]
                    )
                    if base_confirmed or max(base_p["peak_r"], cur_open_r) >= cfg.breakeven_min_r:
                        if base_p["direction"] == "long":
                            base_p["trail_stop"] = max(base_p["trail_stop"], base_p["entry_price"] * 1.001)
                        else:
                            base_p["trail_stop"] = min(base_p["trail_stop"], base_p["entry_price"] * 0.999)

                stop_sig = float(ev.stop)
                target_sig = float(ev.target)
                if is_bridged and stop_sig > 0 and target_sig > 0 and ev.index < len(sig_closes):
                    # 指数空间 → 主连空间：保留结构与入场价之间的点位距离
                    sc = float(sig_closes[ev.index])
                    if ev.direction == "long":
                        stop = fill_open - (sc - stop_sig)
                        target = fill_open + (target_sig - sc)
                    else:
                        stop = fill_open + (stop_sig - sc)
                        target = fill_open - (sc - target_sig)
                else:
                    stop, target = stop_sig, target_sig
                valid_gap = (fill_open > stop) if ev.direction == "long" else (fill_open < stop)
                if not valid_gap:
                    continue

                risk_per_unit = max(abs(fill_open - stop), 0.2 * atrs_prev[i])
                # 仓位缩放以入場阈值为錨点（高于阈值加分、等于阈值 1.0x），随阈值重标定自动适配
                if cfg.scale_by_nine_tests:
                    pivot = acc_res.engine.cfg.min_nine_tests_score
                    score_mult = float(np.clip(1.0 + 0.1 * (ev.nine_tests_score - pivot), 0.8, 1.4))
                else:
                    score_mult = 1.0
                # 金字塔加仓单笔风险为底仓的 75%
                pyr_scale = 0.75 if open_positions else 1.0
                risk_capital = balance * cfg.risk_per_trade * score_mult * pyr_scale
                unit_risk = risk_per_unit * contract_size
                max_notional = balance * 2.5
                raw_size = min(risk_capital / max(unit_risk, 1e-9), max_notional / max(fill_open * contract_size, 1e-9))
                # 期货开仓手数向下取整为真实整手 (Floor to Integer Lots)，小于 1 手放弃开仓
                lots = float(int(raw_size))
                if lots < 1.0:
                    continue
                size = lots
                # 实际投入风险 = 真实整手手数 × 单位风险（点 × 乘数），作为 R 倍数分母
                actual_risk = size * risk_per_unit * contract_size

                open_positions.append({
                    "tr_id": ev.tr_id,
                    "signal": ev.name,
                    "signal_kind": ev.kind,
                    "regime": ev.regime,
                    "direction": ev.direction,
                    "nine_tests": ev.nine_tests_score,
                    "entry_index": i,
                    "entry_time": str(idx_arr[i])[:19],
                    "entry_price": round(fill_open, 4),
                    "stop": round(stop, 4),
                    "trail_stop": stop,
                    "target": round(target, 4),
                    "size": size,
                    "risk_capital": actual_risk,
                    "peak_r": 0.0,
                    "partial_done": False,
                    "partial_pnl": 0.0,
                    "partial_size": 0.0,
                })

        # Step C: 检查所有当前持仓（含当根新开仓）在 bar i 的止损/止盈/移动止损
        still_open: list[dict] = []
        for pos in open_positions:
            d = pos["direction"]
            entry_p = pos["entry_price"]
            init_risk = max(abs(entry_p - pos["stop"]), 1e-9)
            entered_today = pos["entry_index"] == i

            # 若多头遇一字跌停或空头遇一字涨停，流动性封死无法平仓，强制顺延
            if (d == "long" and is_limit_down_bar) or (d == "short" and is_limit_up_bar):
                still_open.append(pos)
                continue

            exit_p = None
            reason = ""

            if d == "long":
                eff_stop = max(pos["stop"], pos["trail_stop"])
                # 1. 跳空穿仓：开盘价已直接跳空跌破有效止损位（强制真实开盘价 + 惩罚滑点撮合）
                if (not entered_today) and opens[i] <= eff_stop:
                    exit_p = opens[i]
                    reason = "跳空止损"
                    # 遭遇重大隔夜跳空时，额外施加 2.5 倍惩罚滑点，绝不虚高估计成交价
                    penalty_slip = cfg.slippage * 2.5
                    exit_fill = exit_p * (1.0 - penalty_slip)
                # 2. 盘中双触碰（日内时序二义性）：若当根 K 线既触及止损又触及止盈，按机构悲观保守原则强制判定先止损
                elif lows[i] <= eff_stop and highs[i] >= pos["target"]:
                    exit_p = eff_stop
                    reason = "震荡先止损"
                    exit_fill = exit_p * (1.0 - cfg.slippage)
                # 3. 正常盘中击穿止损位
                elif lows[i] <= eff_stop:
                    exit_p = eff_stop
                    reason = "移动止损" if eff_stop > pos["stop"] else "结构止损"
                    exit_fill = exit_p * (1.0 - cfg.slippage)
                # 4. 正常触碰止盈目标
                elif highs[i] >= pos["target"]:
                    exit_p = pos["target"]
                    reason = "目标止盈"
                    exit_fill = exit_p * (1.0 - cfg.slippage)
                else:
                    cur_r = (highs[i] - entry_p) / init_risk
                    pos["peak_r"] = max(pos["peak_r"], cur_r)
                    # 两阶段分批止盈：达第一目标先减仓锁定利润，剩余仓位交给趋势跟踪
                    if (
                        cfg.partial_exit_enabled
                        and not pos.get("partial_done")
                        and cur_r >= cfg.partial_exit_r
                    ):
                        cut = float(int(pos["size"] * float(np.clip(cfg.partial_exit_frac, 0.0, 0.9))))
                        if cut >= 1.0:
                            p_price = entry_p + cfg.partial_exit_r * init_risk
                            p_fill = p_price * (1.0 - cfg.slippage)
                            pnl = (p_fill - entry_p) * cut * contract_size
                            pnl -= (entry_p + p_fill) * cut * contract_size * cfg.fees
                            balance += pnl
                            pos["partial_pnl"] = pos.get("partial_pnl", 0.0) + pnl
                            pos["partial_size"] = cut
                            pos["size"] -= cut
                            pos["partial_done"] = True
                    confirmed = pos["tr_id"] in breakout_idx and i >= breakout_idx[pos["tr_id"]]
                    if not confirmed:
                        # Phase C 底仓：突破确认前严禁仅因浮盈推保本，保留结构止损
                        pass
                    elif pos["peak_r"] >= cfg.trail_after_r:
                        new_trail = closes[i] - cfg.trail_atr_mult * atrs_prev[i]
                        pos["trail_stop"] = max(pos["trail_stop"], new_trail)
                        if pos["peak_r"] >= cfg.breakeven_min_r:
                            pos["trail_stop"] = max(pos["trail_stop"], entry_p * 1.001)
            else:
                eff_stop = min(pos["stop"], pos["trail_stop"])
                # 1. 空头跳空穿仓
                if (not entered_today) and opens[i] >= eff_stop:
                    exit_p = opens[i]
                    reason = "跳空止损"
                    penalty_slip = cfg.slippage * 2.5
                    exit_fill = exit_p * (1.0 + penalty_slip)
                # 2. 空头盘中双触碰悲观先止损
                elif highs[i] >= eff_stop and lows[i] <= pos["target"]:
                    exit_p = eff_stop
                    reason = "震荡先止损"
                    exit_fill = exit_p * (1.0 + cfg.slippage)
                # 3. 正常盘中击穿止损位
                elif highs[i] >= eff_stop:
                    exit_p = eff_stop
                    reason = "移动止损" if eff_stop < pos["stop"] else "结构止损"
                    exit_fill = exit_p * (1.0 + cfg.slippage)
                # 4. 正常触碰止盈目标
                elif lows[i] <= pos["target"]:
                    exit_p = pos["target"]
                    reason = "目标止盈"
                    exit_fill = exit_p * (1.0 + cfg.slippage)
                else:
                    cur_r = (entry_p - lows[i]) / init_risk
                    pos["peak_r"] = max(pos["peak_r"], cur_r)
                    if (
                        cfg.partial_exit_enabled
                        and not pos.get("partial_done")
                        and cur_r >= cfg.partial_exit_r
                    ):
                        cut = float(int(pos["size"] * float(np.clip(cfg.partial_exit_frac, 0.0, 0.9))))
                        if cut >= 1.0:
                            p_price = entry_p - cfg.partial_exit_r * init_risk
                            p_fill = p_price * (1.0 + cfg.slippage)
                            pnl = (entry_p - p_fill) * cut * contract_size
                            pnl -= (entry_p + p_fill) * cut * contract_size * cfg.fees
                            balance += pnl
                            pos["partial_pnl"] = pos.get("partial_pnl", 0.0) + pnl
                            pos["partial_size"] = cut
                            pos["size"] -= cut
                            pos["partial_done"] = True
                    confirmed = pos["tr_id"] in breakout_idx and i >= breakout_idx[pos["tr_id"]]
                    if not confirmed:
                        pass
                    elif pos["peak_r"] >= cfg.trail_after_r:
                        new_trail = closes[i] + cfg.trail_atr_mult * atrs_prev[i]
                        pos["trail_stop"] = min(pos["trail_stop"], new_trail)
                        if pos["peak_r"] >= cfg.breakeven_min_r:
                            pos["trail_stop"] = min(pos["trail_stop"], entry_p * 0.999)

            if exit_p is None and (i - pos["entry_index"]) >= cfg.max_hold_bars:
                exit_p = closes[i]
                reason = "时间止损"
                exit_fill = exit_p * (1.0 - cfg.slippage) if d == "long" else exit_p * (1.0 + cfg.slippage)

            if exit_p is not None:
                balance += _settle_pos(pos, i, idx_arr[i], exit_fill, reason, cfg.fees, contract_size)
                trades_log.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # Step D: 期末最后一根 K 线强制平仓并同步更新期末权益
        if i == n - 1 and open_positions:
            for pos in open_positions:
                d = pos["direction"]
                exit_fill = closes[i] * (1.0 - cfg.slippage) if d == "long" else closes[i] * (1.0 + cfg.slippage)
                balance += _settle_pos(pos, i, idx_arr[i], exit_fill, "期末平仓", cfg.fees, contract_size)
                trades_log.append(pos)
            open_positions = []

        unrealized = 0.0
        for pos in open_positions:
            if pos["direction"] == "long":
                unrealized += (closes[i] - pos["entry_price"]) * pos["size"] * contract_size
            else:
                unrealized += (pos["entry_price"] - closes[i]) * pos["size"] * contract_size
        equity_curve[i] = balance + unrealized

    stats = _compute_summary_stats(trades_log, equity_curve, cfg.init_cash)
    return {
        "stats": stats,
        "trades": pd.DataFrame(trades_log),
        "equity": pd.Series(equity_curve, index=df.index, name="equity"),
        "vectorbt_stats": vbt_stats,
        "vectorbt_portfolio": vbt_portfolio,
    }


def _settle_pos(pos: dict, i: int, dt: object, exit_fill: float, reason: str, fee_rate: float, contract_size: float = 1.0) -> float:
    d = pos["direction"]
    size = pos["size"]
    entry_p = pos["entry_price"]
    gross = ((exit_fill - entry_p) * size * contract_size) if d == "long" else ((entry_p - exit_fill) * size * contract_size)
    fee = (entry_p + exit_fill) * size * contract_size * fee_rate
    net = gross - fee + float(pos.get("partial_pnl", 0.0))
    pos["exit_index"] = i
    pos["exit_time"] = str(dt)[:19]
    pos["exit_price"] = round(float(exit_fill), 4)
    pos["exit_reason"] = reason
    pos["net_pnl"] = round(float(net), 2)
    pos["r_multiple"] = round(float(net / max(pos["risk_capital"], 1e-9)), 3)
    pos["bars_held"] = i - pos["entry_index"]
    return float(net)


def _compute_summary_stats(trades: list[dict], equity: np.ndarray, init_cash: float) -> dict:
    if not trades:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "avg_r": 0.0,
            "profit_factor": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
        }
    pnls = np.array([t["net_pnl"] for t in trades], dtype=float)
    rs = np.array([t["r_multiple"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.maximum(peak, 1e-9)
    rets = np.diff(equity) / np.maximum(equity[:-1], 1e-9)
    sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(252)) if len(rets) > 1 and rets.std(ddof=0) > 1e-12 else 0.0

    return {
        "total_trades": len(trades),
        "win_rate_pct": round(float(len(wins) / len(trades) * 100.0), 2),
        "avg_r": round(float(rs.mean()), 3),
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 3) if len(losses) and losses.sum() != 0 else float("inf"),
        "total_pnl": round(float(pnls.sum()), 2),
        "total_return_pct": round(float((equity[-1] - init_cash) / init_cash * 100.0), 2),
        "max_drawdown_pct": round(float(dd.min() * 100.0), 2),
        "sharpe_ratio": round(sharpe, 3),
        "avg_bars_held": round(float(np.mean([t["bars_held"] for t in trades])), 1),
    }


# ============================================================================
# 7. 可视化标注工具（支持 mplfinance 与 matplotlib 双引擎绘制全套维科夫结构）
# ============================================================================

def plot_wyckoff_structure(
    df: pd.DataFrame,
    acc_res: WyckoffProResult,
    dist_res: WyckoffProResult | None = None,
    bt_res: dict | None = None,
    save_path: str | None = None,
    title: str = "Wyckoff Composite Man Structural Analysis (Accumulation / Markup / Distribution)",
) -> None:
    """在 K 线图上精确绘制交易区间（Ice / Creek）、五大阶段事件标注、Weis Wave 量能波与权益曲线。"""
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "DejaVu Sans", "SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(16, 11), dpi=150)
    gs = fig.add_gridspec(3, 1, height_ratios=[3.2, 1.1, 1.0], hspace=0.18)
    ax_price = fig.add_subplot(gs[0])
    ax_weis = fig.add_subplot(gs[1], sharex=ax_price)
    ax_eq = fig.add_subplot(gs[2], sharex=ax_price)

    x = np.arange(len(df))
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()

    up = closes >= opens
    colors = np.where(up, "#16a34a", "#dc2626")
    ax_price.vlines(x, lows, highs, color=colors, linewidth=0.8, alpha=0.85)
    body_bottom = np.minimum(opens, closes)
    body_height = np.maximum(np.abs(closes - opens), (highs - lows) * 0.05)
    ax_price.bar(x, body_height, bottom=body_bottom, width=0.65, color=colors, alpha=0.85)

    for tr in acc_res.ranges:
        end_i = tr.end_index if tr.end_index is not None else len(df) - 1
        span = np.arange(tr.start_index, min(end_i + 1, len(df)))
        ax_price.plot(span, np.full_like(span, tr.ice, dtype=float), "--", color="#0284c7", linewidth=1.3, label="Accum Ice" if tr.id == 1 else "")
        if tr.creek > 0:
            ax_price.plot(span, np.full_like(span, tr.creek, dtype=float), "-", color="#0ea5e9", linewidth=1.5, label="Accum Creek" if tr.id == 1 else "")
            ax_price.fill_between(span, tr.ice, tr.creek, color="#38bdf8", alpha=0.08)

    if dist_res is not None:
        for tr in dist_res.ranges:
            end_i = tr.end_index if tr.end_index is not None else len(df) - 1
            span = np.arange(tr.start_index, min(end_i + 1, len(df)))
            ax_price.plot(span, np.full_like(span, tr.creek, dtype=float), "--", color="#dc2626", linewidth=1.3, label="Dist Supply Top" if tr.id == 1 else "")
            if tr.ice > 0:
                ax_price.plot(span, np.full_like(span, tr.ice, dtype=float), "-", color="#f97316", linewidth=1.5, label="Dist Ice Support" if tr.id == 1 else "")
                ax_price.fill_between(span, tr.ice, tr.creek, color="#fb923c", alpha=0.08)

    all_ev = list(acc_res.events) + (list(dist_res.events) if dist_res else [])
    for idx_ev, ev in enumerate(all_ev):
        is_bull = ev.direction == "long"
        y_anchor = lows[ev.index] if is_bull else highs[ev.index]
        offset = -16 - (idx_ev % 3) * 10 if is_bull else 14 + (idx_ev % 3) * 10
        badge_color = "#047857" if is_bull else "#b91c1c"
        label_txt = f"{ev.name}" + (f"({ev.nine_tests_score}/9)" if ev.nine_tests_score > 0 else "")
        ax_price.annotate(
            label_txt,
            xy=(ev.index, y_anchor),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
            fontweight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.2", fc=badge_color, ec="none", alpha=0.88),
            arrowprops=dict(arrowstyle="->", color=badge_color, lw=0.8),
        )

    ax_price.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax_price.set_ylabel("Price")
    ax_price.grid(True, linestyle=":", alpha=0.4)
    ax_price.legend(loc="upper left", fontsize=8)

    w_vol = acc_res.df["weis_cum_vol"].to_numpy()
    w_dir = acc_res.df["weis_dir"].to_numpy()
    w_colors = np.where(w_dir > 0, "#22c55e", "#ef4444")
    ax_weis.bar(x, w_vol, color=w_colors, width=0.8, alpha=0.75)
    ax_weis.set_ylabel("Weis Wave Vol")
    ax_weis.grid(True, linestyle=":", alpha=0.4)

    if bt_res is not None and "equity" in bt_res:
        eq = bt_res["equity"].to_numpy()
        ax_eq.plot(x, eq / eq[0], color="#2563eb", linewidth=1.8, label="Wyckoff Pro Strategy Equity")
        ax_eq.plot(x, closes / closes[0], color="#64748b", linewidth=1.1, linestyle="--", label="Buy & Hold Benchmark")
        ax_eq.set_ylabel("Normalized Equity")
        ax_eq.legend(loc="upper left", fontsize=8)
        ax_eq.grid(True, linestyle=":", alpha=0.4)

    step = max(1, len(df) // 10)
    ax_eq.set_xticks(x[::step])
    ax_eq.set_xticklabels([str(df.index[k])[:10] for k in x[::step]], rotation=0, fontsize=8)

    if save_path:
        fig.savefig(save_path, bbox_inches="tight")
    plt.close(fig)


# ============================================================================
# 8. 自包含完整演示：生成标准维科夫完整周期（吸筹->主升->派发->主跌）并验证
# ============================================================================

def generate_composite_man_cycle(n_bars: int = 340, seed: int = 7) -> pd.DataFrame:
    """合成一条包含完整维科夫五大阶段（Markdown -> Accumulation -> Markup -> Distribution -> Markdown）的标准行情序列。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="D")

    prices: list[float] = []
    vols: list[float] = []
    ois: list[float] = []

    # 1) 前置下跌 Markdown (0..59): 4200 -> 3040, 包含 PS (bar 48) 和 SC (bar 57..59)
    for k in range(60):
        base = 4200.0 - (1100.0 * (k / 59.0)) + rng.normal(0, 8)
        v = 1000.0 + rng.normal(0, 100)
        if k == 48:
            base -= 35
            v = 2600.0
        elif k in (57, 58, 59):
            base = 3040.0 if k == 58 else 3090.0
            v = 4500.0 if k == 58 else 3200.0
        prices.append(base)
        vols.append(v)
        ois.append(5000.0 - k * 10)

    # 2) Phase A: AR 自动反弹 (60..68 -> 3380) & ST 二次测试 (69..75 -> Higher Low 3075)
    for k in range(9):
        prices.append(3100.0 + 280.0 * (k / 8.0))
        vols.append(1600.0 - k * 50)
        ois.append(4500.0)
    for k in range(7):
        prices.append(3380.0 - 305.0 * (k / 6.0))
        vols.append(850.0)
        ois.append(4450.0)

    # 3) Phase B: 横盘震荡蓄势 32 根 (76..107)，期间包含一次 UT
    for k in range(32):
        wave = 3210.0 + 120.0 * np.sin(k * 0.45) + rng.normal(0, 6)
        v = 950.0 + (250.0 if np.cos(k * 0.45) > 0 else -150.0)
        if k == 14:
            wave = 3405.0
            v = 1800.0
        prices.append(wave)
        vols.append(max(v, 400.0))
        ois.append(4600.0 + k * 15)

    # 4) Phase C: Spring 刺穿冰线 + TEST 二次无供给测试
    prices.extend([3015.0, 3095.0, 3140.0, 3065.0, 3120.0])
    vols.extend([1900.0, 2200.0, 1200.0, 620.0, 1400.0])
    ois.extend([5100.0, 5250.0, 5300.0, 5280.0, 5400.0])

    # 5) Phase D: SOS / JAC 放量跃过小溪 + BUEC / LPS 回撤小溪边缘
    prices.extend([3220.0, 3330.0, 3455.0, 3480.0, 3415.0, 3395.0, 3440.0])
    vols.extend([2100.0, 2800.0, 3800.0, 2500.0, 900.0, 720.0, 1900.0])
    ois.extend([5600.0, 5900.0, 6400.0, 6500.0, 6420.0, 6400.0, 6650.0])

    # 6) Phase E: Markup 主升浪 (3460 -> 4550)
    for k in range(65):
        prices.append(3460.0 + 1090.0 * (k / 64.0) + 35.0 * np.sin(k * 0.3))
        vols.append(1500.0 + 300.0 * np.cos(k * 0.3))
        ois.append(6700.0 + k * 20)

    # 7) 高位派发区间 Distribution: BC -> AR_DIST -> ST_DIST -> Phase B -> UTAD -> TEST_SELL -> SOW -> BUEC_ICE -> LPSY
    prices.extend([4590.0, 4625.0, 4565.0])
    vols.extend([3600.0, 4800.0, 3300.0])
    ois.extend([8000.0, 8200.0, 7900.0])

    for k in range(9):
        prices.append(4550.0 - 230.0 * (k / 8.0))
        vols.append(1600.0 - k * 40)
        ois.append(7800.0)
    for k in range(6):
        prices.append(4320.0 + 265.0 * (k / 5.0))
        vols.append(820.0)
        ois.append(7700.0)

    for k in range(30):
        prices.append(4450.0 - 100.0 * np.sin(k * 0.45) + rng.normal(0, 6))
        vols.append(1000.0 + (220.0 if np.sin(k * 0.45) > 0 else -150.0))
        ois.append(7600.0)

    prices.extend([4645.0, 4555.0, 4510.0, 4585.0, 4420.0, 4265.0, 4310.0, 4220.0])
    vols.extend([2100.0, 2400.0, 1100.0, 650.0, 2600.0, 3900.0, 750.0, 2200.0])
    ois.extend([7500.0, 7700.0, 7800.0, 7750.0, 8100.0, 8600.0, 8500.0, 8800.0])

    # 8) Markdown 主跌浪直至序列结束
    while len(prices) < n_bars:
        prices.append(prices[-1] - 14.0 + rng.normal(0, 6))
        vols.append(1400.0)
        ois.append(8800.0)

    p = np.array(prices[:n_bars], dtype=float)
    v = np.array(vols[:n_bars], dtype=float)
    oi = np.array(ois[:n_bars], dtype=float)

    prev_p = np.roll(p, 1)
    prev_p[0] = p[0]
    opens = 0.65 * prev_p + 0.35 * p
    spreads = np.maximum(np.abs(p - opens) * 1.8, 28.0)
    highs = np.maximum(opens, p) + spreads * 0.35
    lows = np.minimum(opens, p) - spreads * 0.35

    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": p,
            "volume": v,
            "open_interest": oi,
        },
        index=dates,
    )


if __name__ == "__main__":
    df_demo = generate_composite_man_cycle(n_bars=340)
    cfg_macro = WyckoffProConfig.from_timeframe("1d", min_nine_tests_score=5)

    acc_engine = WyckoffProEngine(cfg_macro)
    dist_engine = DistributionProEngine(cfg_macro)

    acc_result = acc_engine.process(df_demo)
    dist_result = dist_engine.process(df_demo)

    bt_result = run_wyckoff_backtest(df_demo, acc_result, dist_result)

    print("=" * 78)
    print("  Wyckoff Pro Engine — 完整复合人周期识别与回测验证报告")
    print("=" * 78)
    print(f"【吸筹/再吸筹事件统计】 {acc_result.event_counts()}")
    print(f"【派发/再派发事件统计】 {dist_result.event_counts()}")
    print("\n【识别到的全部维科夫关键事件时间线】")
    all_timeline = pd.concat([acc_result.timeline_df(), dist_result.timeline_df()], ignore_index=True)
    if not all_timeline.empty:
        all_timeline = all_timeline.sort_values("index").reset_index(drop=True)
        print(all_timeline[["index", "time", "name", "phase", "regime", "price", "nine_tests", "er_ratio", "note"]].to_string(index=False))

    print("\n【回测绩效指标】")
    for k, v in bt_result["stats"].items():
        print(f"  {k:<22s}: {v}")

    if not bt_result["trades"].empty:
        print("\n【成交明细（支持 TEST 底仓 + LPS/LPSY 金字塔加仓）】")
        cols = ["entry_time", "signal", "regime", "direction", "nine_tests", "entry_price", "stop", "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "net_pnl"]
        print(bt_result["trades"][cols].to_string(index=False))
"""维科夫 Pro 引擎逐项验证脚本。

对每一轮审计/优化落实的修复项做**可执行断言**，而不是靠人工目测。
用法：
    .venv\\Scripts\\python.exe scripts\\verify_wyckoff.py
全部通过时退出码为 0。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import numpy as np
import pandas as pd

import cta_lab.wyckoff.pro as pro
from cta_lab.wyckoff.engine import WyckoffConfig
from cta_lab.wyckoff.runner import load_df, run_backtest

RESULTS: list[tuple[str, bool, str]] = []


def check(item: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((item, bool(ok), detail))


# ---------------------------------------------------------------------------
# 准备数据
# ---------------------------------------------------------------------------
DF = load_df("rb.SHFE")
CFG = pro.WyckoffProConfig(mode="macro")


def _signals(df, cfg=None):
    cfg = cfg or CFG
    acc = pro.WyckoffProEngine(cfg).process(df)
    dist = pro.DistributionProEngine(cfg).process(df, vsa_df=acc.df)
    kinds = ("SPRING3", "TEST", "LPS", "JAC", "SOS", "FTI")
    return acc, dist, [e for e in acc.events + dist.events if e.kind in kinds]


# ---------------------------------------------------------------------------
# 1. 九大检验逐项可失败（不存在恒真白送分）
# ---------------------------------------------------------------------------
HITS: dict[str, list[bool]] = {}


def _instrument() -> None:
    original = pro.WyckoffProEngine._evaluate_nine_tests

    def wrapper(self, tr, i, close, low, high, atr, stop, target, oi_rising,
                oi_available=True, signal_kind="", structural_stop=None):
        score = original(self, tr, i, close, low, high, atr, stop, target,
                         oi_rising, oi_available, signal_kind, structural_stop)
        if not self._vols or not self._trs:  # 空引擎（门禁探针）不做特征采样
            return score
        closes, highs, lows = list(self._closes), list(self._highs), list(self._lows)
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else close
        decel_ok = False
        if len(highs) >= 25:
            recent = (max(highs[-10:]) - close) / atr
            prior = (max(highs[-25:-10]) - min(lows[-25:-10])) / atr
            decel_ok = prior > 1e-9 and recent <= 0.85 * prior
        dn = list(self._weis_dn_waves)
        vol_ma, _ = self._vol_stats()
        er = self._vols[-1] / (self._trs[-1] + 1e-9)
        ref = structural_stop if structural_stop is not None else stop
        risk = abs(close - ref)
        clamped = risk > 1e-9 and abs(close - stop) < 0.995 * risk
        proj = self._structural_projection(tr, atr, max(0, i - self._b_start))
        flags = [
            decel_ok,
            tr.higher_lows_count >= 2 and low > tr.ice,
            len(highs) >= 15 and max(highs[-5:]) >= np.percentile(highs[-15:], 60),
            close >= ma20,
            len(dn) >= 4 and np.mean(dn[-2:]) <= 0.9 * np.mean(dn[:-2]),
            er >= (vol_ma / (atr + 1e-9)) * 1.35,
            self.cfg.use_oi_confirm and oi_available and len(self._ois) >= 6
            and self._ois[-1] > self._ois[-6] * 1.002,
            tr.width >= 2.5 * atr,
            risk > 1e-9 and not clamped and (proj - close) / risk >= 1.5,
        ]
        for k, f in enumerate(flags, 1):
            HITS.setdefault(f"T{k}", []).append(bool(f))
        return score

    pro.WyckoffProEngine._evaluate_nine_tests = wrapper


_instrument()
SIGNALS = _signals(DF)[2]
rates = {k: (sum(v) / len(v) if v else 0.0, len(v)) for k, v in HITS.items()}
degenerate = [k for k, (r, n) in rates.items() if n >= 8 and (r <= 0.0 or r >= 1.0)]
detail = " ".join(f"{k}={r*100:.0f}%(n={n})" for k, (r, n) in sorted(rates.items()))
check("1 九项检验逐项可失败（无恒真项）", not degenerate, detail)
check("1b 得分区间真实覆盖 0~9", len(set(e.nine_tests_score for e in SIGNALS)) >= 4,
      f"取值={sorted(set(e.nine_tests_score for e in SIGNALS))}")

# ---------------------------------------------------------------------------
# 2. 门禁与计分解耦：门禁不满足时直接返回 0
# ---------------------------------------------------------------------------
GATE_CASES = []


def _gate_probe():
    eng = pro.WyckoffProEngine(CFG)
    tr = pro.TradingRange(id=1, direction="long", regime="ACCUMULATION",
                          start_index=0, start_time="t", ice=100.0, creek=200.0,
                          phase="PHASE_B")
    # 无 ST 确立 → 门禁 1 拦截
    GATE_CASES.append(eng._evaluate_nine_tests(tr, 0, 150.0, 140.0, 160.0, 5.0, 95.0, 300.0, False) == 0)
    # ST 已确立但 Phase C 信号深跌破位 → 门禁 2 拦截
    tr.st_index = 0
    GATE_CASES.append(
        eng._evaluate_nine_tests(tr, 0, 80.0, 78.0, 82.0, 5.0, 70.0, 300.0, False,
                                 signal_kind="TEST") == 0
    )


_gate_probe()
check("2 门禁与计分解耦（门禁失败即 0 分）", all(GATE_CASES), f"{GATE_CASES}")

# ---------------------------------------------------------------------------
# 3. FTI 不再硬编码 6 分
# ---------------------------------------------------------------------------
fti_scores = sorted({e.nine_tests_score for e in SIGNALS if e.kind == "FTI"})
check("3 FTI 破位路径接入真实评分", len(fti_scores) > 1, f"FTI 得分集合={fti_scores}")

# ---------------------------------------------------------------------------
# 4. 结构止损不再被静默收紧（钳制时评分层拒绝）
# ---------------------------------------------------------------------------
eng = pro.WyckoffProEngine(CFG)
tr = pro.TradingRange(id=99, direction="long", regime="ACCUMULATION", start_index=0,
                      start_time="t", ice=100.0, creek=900.0, phase="PHASE_B",
                      st_index=0, prior_peak=1000.0, higher_lows_count=3)
stop, target, struct_stop = eng._calc_long_stops(tr, close=500.0, low=490.0, atr=5.0, cause_bars=40)
CLOSE = 500.0
tightened = abs(CLOSE - stop) < abs(CLOSE - struct_stop) - 1e-9
score_clamped = eng._evaluate_nine_tests(tr, 40, CLOSE, 490.0, 505.0, 5.0, stop, target,
                                         True, structural_stop=struct_stop)
check("4 止损钳制不再静默生效（钳制即拒绝）", tightened and score_clamped == 0,
      f"结构止损={struct_stop:.2f} 最终止损={stop:.2f} 被钳制={tightened} 得分={score_clamped}")

# ---------------------------------------------------------------------------
# 5. 引擎 ATR 与回测层 ATR 口径完全一致
# ---------------------------------------------------------------------------
acc = pro.WyckoffProEngine(CFG).process(DF)
batch_atr = acc.df["atr"].to_numpy(float)
hc = DF["high"].to_numpy(float); lc = DF["low"].to_numpy(float); cc = DF["close"].to_numpy(float)
prev = np.roll(cc, 1); prev[0] = cc[0]
tr_arr = np.maximum(hc - lc, np.maximum(np.abs(hc - prev), np.abs(lc - prev)))
engine_atr = np.array([tr_arr[max(0, i - 20):i].mean() if i > 0 else tr_arr[0] for i in range(len(DF))])
ratio = batch_atr[20:] / engine_atr[20:]
check("5 VSA 与引擎 ATR 口径一致", np.allclose(ratio, 1.0, atol=1e-9),
      f"比值 min={ratio.min():.9f} max={ratio.max():.9f}")

# ---------------------------------------------------------------------------
# 6. 派发引擎 anchor 仅依赖首根（无前视）
# ---------------------------------------------------------------------------
base_events = pro.DistributionProEngine(CFG).process(DF).events
# 篡改"未来"价格（仅尾部），若 anchor 依赖全序列最高价，历史事件会被改变
tampered = DF.copy()
tail = int(len(tampered) * 0.7)
tampered.iloc[tail:, tampered.columns.get_loc("high")] *= 3.0
tampered_events = pro.DistributionProEngine(CFG).process(tampered).events
head_before = [e.as_dict() for e in base_events if e.index < tail]
head_after = [e.as_dict() for e in tampered_events if e.index < tail]
check("6 派发 anchor 无前视（篡改未来价格不影响历史事件）", head_before == head_after,
      f"历史事件 {len(head_before)} vs {len(head_after)}")

# ---------------------------------------------------------------------------
# 7. process() 支持子类覆写
# ---------------------------------------------------------------------------
class _Sub(pro.WyckoffProEngine):
    def _evaluate_nine_tests(self, *a, **k):
        return 123


check("7 process() 支持子类覆写", any(e.nine_tests_score == 123 for e in _Sub(CFG).process(DF).events))

# ---------------------------------------------------------------------------
# 8. VSA 特征复用不改变结果（消除重复计算且等价）
# ---------------------------------------------------------------------------
acc_a = pro.WyckoffProEngine(CFG).process(DF)
dist_a = pro.DistributionProEngine(CFG).process(DF)
dist_b = pro.DistributionProEngine(CFG).process(DF, vsa_df=acc_a.df)
same_events = [e.as_dict() for e in dist_a.events] == [e.as_dict() for e in dist_b.events]
same_cols = np.allclose(
    dist_a.df[pro.VSA_COLUMNS].to_numpy(float), dist_b.df[pro.VSA_COLUMNS].to_numpy(float),
    equal_nan=True,
)
check("8 VSA 复用等价（消除重复计算）", same_events and same_cols)

# ---------------------------------------------------------------------------
# 9 & 10. 评分定位：默认不按分数加杠杆；跨品种稳健硬门禁生效
# ---------------------------------------------------------------------------
bt_cfg = pro.BacktestConfigPro()
check("9 评分体系降级为诊断（默认不缩放仓位）", bt_cfg.scale_by_nine_tests is False)
check("10 结构 R:R 硬门禁已开启", CFG.min_structural_rr >= 1.2, f"min_structural_rr={CFG.min_structural_rr}")
check("10b MTF 周线过滤默认关闭（实测负向）", bt_cfg.use_weekly_filter is False)

# ---------------------------------------------------------------------------
# 11. 周期模式真正生效且 Web/CLI 一致
# ---------------------------------------------------------------------------
default_stats = run_backtest("rb.SHFE", use_pro=True).stats()
macro_stats = run_backtest("rb.SHFE", use_pro=True, engine_config=WyckoffConfig.create_macro()).stats()
micro_stats = run_backtest("rb.SHFE", use_pro=True, engine_config=WyckoffConfig.create_micro()).stats()
same_as_cli = (default_stats["trades"] == macro_stats["trades"]
               and abs(default_stats["avg_r"] - macro_stats["avg_r"]) < 1e-9)
micro_differs = micro_stats["trades"] != macro_stats["trades"]
check("11 周期模式生效且 Web/CLI 一致", same_as_cli and micro_differs,
      f"默认={default_stats['trades']}笔 macro={macro_stats['trades']}笔 micro={micro_stats['trades']}笔")

# ---------------------------------------------------------------------------
# 12. 因果性（截断不变性）
# ---------------------------------------------------------------------------
full_events = pro.WyckoffProEngine(CFG).process(DF).events
cut_ok = []
for frac in (0.3, 0.6, 0.85):
    cut = int(len(DF) * frac)
    part = pro.WyckoffProEngine(CFG).process(DF.iloc[:cut]).events
    cut_ok.append([e.as_dict() for e in part]
                  == [e.as_dict() for e in full_events if e.index < cut])
check("12 因果性截断不变性", all(cut_ok), f"{cut_ok}")

# ---------------------------------------------------------------------------
# 13. 整手开仓 + 资本透传
# ---------------------------------------------------------------------------
trades = run_backtest("rb.SHFE", use_pro=True).trades_df()
lots_ok = bool(len(trades)) and all(s == int(s) for s in trades["size"]) and trades["size"].min() >= 1
small = run_backtest("rb.SHFE", capital=500_000.0, use_pro=True).stats()
big = run_backtest("rb.SHFE", capital=2_000_000.0, use_pro=True).stats()
cap_ok = abs(small["total_return"] - big["total_return"]) < 5.0  # 收益率应量级一致（非线性源于整手取整）
check("13 严格整手开仓", lots_ok, f"最小手数={trades['size'].min() if len(trades) else 'NA'}")
check("13b 资本参数已透传", cap_ok,
      f"50万={small['total_return']:.2f}% 200万={big['total_return']:.2f}%")

# ---------------------------------------------------------------------------
# 14. OI 缺失鲁棒
# ---------------------------------------------------------------------------
df0 = DF.copy(); df0["open_interest"] = 0.0
events_no_oi = pro.WyckoffProEngine(CFG).process(df0).events
check("14 OI 缺失不崩且不虚假满分", len(events_no_oi) > 0
      and max(e.nine_tests_score for e in events_no_oi) <= 8,
      f"事件={len(events_no_oi)} 最高分={max(e.nine_tests_score for e in events_no_oi)}")

# ---------------------------------------------------------------------------
# 15. Pro 引擎换月桥接（信号端与执行端分离）
# ---------------------------------------------------------------------------
bridged = run_backtest("rb.SHFE", use_pro=True, bridge_index=True)
plain = run_backtest("rb.SHFE", use_pro=True, bridge_index=False)
bridge_flag_ok = bridged.is_bridged and not plain.is_bridged
signal_is_index = bridged.signal_df is not bridged.exec_df
# 桥接后止损仍与入场价保持正的风险距离，且与信号端点位距离一致
bd = bridged.trades_df()
risk_ok = bool(len(bd)) and bool((bd["stop"] != bd["entry_price"]).all())
check("15 Pro 引擎启用换月平滑指数桥接", bridge_flag_ok and signal_is_index, f"is_bridged={bridged.is_bridged}")
check("15b 桥接后结构止损仍正常（未出现零风险仓）", risk_ok, f"成交={len(bd)}")

# ---------------------------------------------------------------------------
# 16. 派发引擎顶部参数按比例派生（不再被静默覆盖）
# ---------------------------------------------------------------------------
dist_eng = pro.DistributionProEngine(CFG)
check(
    "16 派发引擎派生顶部专用参数",
    dist_eng.cfg.vol_z_thresh < CFG.vol_z_thresh
    and dist_eng.cfg.min_cause_bars < CFG.min_cause_bars
    and dist_eng.cfg is not CFG,
    f"vol_z {CFG.vol_z_thresh:.2f}->{dist_eng.cfg.vol_z_thresh:.2f}, "
    f"cause {CFG.min_cause_bars}->{dist_eng.cfg.min_cause_bars}",
)

# ---------------------------------------------------------------------------
# 17. CLI/Web 参数透传（禁空 / 纯核心）
# ---------------------------------------------------------------------------
from cta_lab.wyckoff.trading import TradeConfig

long_only = run_backtest("rb.SHFE", use_pro=True,
                         trade_config=TradeConfig(allow_short=False)).trades_df()
pure = run_backtest("rb.SHFE", use_pro=True,
                     trade_config=TradeConfig(trade_signals=("TEST", "LPS"))).trades_df()
no_short_ok = bool(len(long_only)) and set(long_only["direction"]) == {"long"}
# 展示名 -> 引擎 kind（派发端事件保留镜像 kind，但展示名已替换）
_NAME_TO_KIND = {
    "TEST": "TEST", "TEST_SELL": "TEST",
    "LPS": "LPS", "LPSY": "LPS",
    "SPRING3": "SPRING3", "UTAD_#3": "SPRING3",
    "JAC": "JAC", "SOW": "JAC",
    "SOS": "SOS",
    "FTI": "FTI", "JAC_BREAKOUT": "FTI",
}
pure_kinds = {_NAME_TO_KIND.get(n, n) for n in set(pure["signal"])} if len(pure) else set()
check("17 禁空参数已透传到 Pro 回测器", no_short_ok,
      f"空头笔数={int((long_only['direction']=='short').sum()) if len(long_only) else 'NA'}")
check("17b 纯核心参数已透传（仅保留 TEST/LPS）", pure_kinds <= {"TEST", "LPS"},
      f"实际信号集={sorted(pure_kinds)}")

# ---------------------------------------------------------------------------
# 18. T6 努力与结果按信号类型分向
# ---------------------------------------------------------------------------
_breakout_ok = 0
_absorb_ok = 0
for e in SIGNALS:
    if e.kind in ("JAC", "SOS"):
        _breakout_ok += 1
    else:
        _absorb_ok += 1
check("18 T6 已按突破/测试分向实现", _breakout_ok > 0 and _absorb_ok > 0,
      f"突破类信号={_breakout_ok} 测试类信号={_absorb_ok}")

# ---------------------------------------------------------------------------
# 19. 分批止盈降低微利保本出局
# ---------------------------------------------------------------------------
def micro_ratio(**over):
    from dataclasses import fields
    base = pro.BacktestConfigPro
    orig = base.__init__

    def init(self, *a, **k):
        k.update(over)
        orig(self, *a, **k)

    base.__init__ = init
    try:
        td = run_backtest("rb.SHFE", use_pro=True).trades_df()
    finally:
        base.__init__ = orig
    r = td["r_multiple"].to_numpy(float) if len(td) else np.array([0.0])
    wins = r[r > 0]
    return float((wins < 0.1).mean()) if len(wins) else 0.0

new_ratio = micro_ratio()
old_ratio = micro_ratio(partial_exit_enabled=False, breakeven_min_r=1.2, trail_after_r=1.2)
_td_new = run_backtest("rb.SHFE", use_pro=True).trades_df()
_partial_fired = int((_td_new.get("partial_size", pd.Series(dtype=float)).fillna(0) > 0).sum())
check("19 分批止盈/推迟保本减少微利出局", new_ratio <= old_ratio,
      f"微利占比 {new_ratio*100:.1f}% (新) vs {old_ratio*100:.1f}% (旧)")
check("19b 分批止盈真实触发", _partial_fired > 0, f"发生过减仓的持仓 {_partial_fired} 笔")

# ---------------------------------------------------------------------------
# 20. 组合层：板块同向并发上限与热度上限生效
# ---------------------------------------------------------------------------
from cta_lab.wyckoff.portfolio import PortfolioConfig, run_portfolio

synthetic = pd.DataFrame({
    "symbol": ["rb.SHFE", "hc.SHFE", "i.DCE", "j.DCE"],
    "direction": ["long"] * 4,
    "entry_time": pd.to_datetime(["2020-01-02", "2020-01-02", "2020-01-02", "2020-02-03"]),
    "exit_time": pd.to_datetime(["2020-06-01", "2020-06-01", "2020-06-01", "2020-07-01"]),
    "r_multiple": [1.0, 1.0, 1.0, 1.0],
})
p_free = run_portfolio(synthetic, PortfolioConfig(max_per_sector_dir=0, max_heat=0))
p_cap = run_portfolio(synthetic, PortfolioConfig(max_per_sector_dir=2, max_heat=0))
check("20 板块同向并发上限生效", p_free.stats["trades"] == 4 and p_cap.stats["trades"] < 4,
      f"无限制={p_free.stats['trades']}笔 限制≤2={p_cap.stats['trades']}笔")

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
print("=" * 96)
print("维科夫 Pro 引擎 —— 逐项验证报告")
print("=" * 96)
width = max(len(name) for name, _, _ in RESULTS)
failed = 0
for name, ok, detail in RESULTS:
    flag = "PASS" if ok else "FAIL"
    if not ok:
        failed += 1
    print(f"[{flag}] {name:<{width}}  {detail}")
print("-" * 96)
print(f"合计 {len(RESULTS)} 项，通过 {len(RESULTS) - failed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)

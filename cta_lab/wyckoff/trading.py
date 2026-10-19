"""把维科夫事件转成可执行的交易计划，并做逐 K 回测。

为什么要单独一层
----------------
状态机只负责"识别结构"，它不知道：
    - 止损该放哪（冰线？弹簧低点？）
    - 目标该多远（测量移动）
    - 一笔亏多少本金（按风险定额算手数）
    - 什么时候该走（时间止损 / 移动止损 / 结构失效）

把这些放在交易层，可以独立调参、独立验证，也避免污染识别逻辑。

入场时点
--------
**信号在 t 根收盘确认，在 t+1 根开盘成交**。
这是实盘唯一能做到的时点，也是回测唯一诚实的时点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .engine import WyckoffEvent, WyckoffResult


@dataclass
class TradeConfig:
    """交易层参数。"""

    risk_per_trade: float = 0.01     # 单笔风险占本金比例
    stop_buffer_pct: float = 0.015   # 止损离结构边界的缓冲
    target_r: float = 1.0            # 目标 = 区间宽度 × R
    max_hold_bars: int = 60          # 时间止损
    trail_atr_mult: float = 3.0      # ATR 移动止损宽度；>0 启用
    trail_after_r: float = 1.0       # 浮盈达到多少 R 后才启用移动止损（保本优先）
    atr_window: int = 20
    slippage_pct: float = 0.0005     # 滑点（按价格比例）
    commission_rate: float = 0.0001  # 手续费率（按成交额）
    fixed_fee_per_lot: float = 0.0   # 每手固定手续费（期货真实成本）
    max_gap_atr: float = 2.0         # 入场开盘跳空超过 N 倍 ATR 则放弃（防封板/跳空）
    allow_short: bool = True
    max_position_notional: float = 5.0  # 名义金额上限（相对本金），防爆仓
    max_concurrent_trades: int = 2      # 最多同时持仓笔数（底仓+加仓）
    pyramid_min_r: float = 0.5          # 加仓时要求底仓浮盈至少达标 R
    allow_reversal: bool = True         # 出现反向破坏信号时是否反手平仓
    trade_signals: tuple[str, ...] = ("TEST", "LPS", "FTI")  # 允许参与交易的信号类型，默认全开


@dataclass
class Trade:
    """一笔完整交易。"""

    direction: str
    entry_index: int
    entry_time: object
    entry_price: float
    stop: float
    target: float
    size: float
    risk_amount: float

    exit_index: int | None = None
    exit_time: object = None
    exit_price: float = 0.0
    exit_reason: str = ""

    peak_r: float = 0.0             # 持仓期间最高浮盈 R（用于移动止损触发）
    trail_stop: float = 0.0         # 当前移动止损价

    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    tr_id: int = -1
    signal_kind: str = ""
    partial_size: float = 0.0        # 分批止盈已减仓手数（0 表示未触发）
    is_cross_asset: bool = False

    @property
    def signal(self) -> str:
        return self.signal_kind

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry_time": str(self.entry_time)[:10],
            "entry_price": round(self.entry_price, 4),
            "stop": round(self.stop, 4),
            "target": round(self.target, 4),
            "size": round(self.size, 4),
            "exit_time": str(self.exit_time)[:10] if self.exit_time else "",
            "exit_price": round(self.exit_price, 4),
            "exit_reason": self.exit_reason,
            "net_pnl": round(self.net_pnl, 2),
            "r_multiple": round(self.r_multiple, 3),
            "bars_held": self.bars_held,
            "tr_id": self.tr_id,
            "signal": self.signal_kind,
            "partial_size": round(self.partial_size, 4),
        }


@dataclass
class SimResult:
    trades: list[Trade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    equity_index: list = field(default_factory=list)
    config: TradeConfig = field(default_factory=TradeConfig)
    capital: float = 0.0

    # ---- 统计 ------------------------------------------------------
    def statistics(self) -> dict:
        t = self.trades
        if not t:
            return {
                "trades": 0, "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0,
                "total_pnl": 0.0, "total_return": 0.0, "max_drawdown_pct": 0.0,
                "sharpe": 0.0, "avg_bars": 0.0, "expectancy": 0.0,
            }

        pnls = np.array([x.net_pnl for x in t], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        rs = np.array([x.r_multiple for x in t], dtype=float)

        total = float(pnls.sum())
        gross_win = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) else 0.0

        eq = np.array(self.equity, dtype=float) if self.equity else np.array(
            [self.capital], dtype=float)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / np.maximum(peak, 1e-9)
        max_dd = float(dd.min() * 100) if len(dd) else 0.0

        rets = np.diff(eq) / np.maximum(eq[:-1], 1e-9) if len(eq) > 1 else np.array([0.0])
        sharpe = (
            float(rets.mean() / rets.std(ddof=0) * np.sqrt(240))
            if rets.std(ddof=0) > 1e-12 else 0.0
        )

        return {
            "trades": len(t),
            "win_rate": float(len(wins) / len(t) * 100),
            "avg_r": float(rs.mean()),
            "profit_factor": float(gross_win / gross_loss) if gross_loss else float("inf"),
            "total_pnl": total,
            "total_return": float(total / self.capital * 100) if self.capital else 0.0,
            "max_drawdown_pct": max_dd,
            "sharpe": sharpe,
            "avg_bars": float(np.mean([x.bars_held for x in t])),
            "expectancy": float(pnls.mean()),
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "max_win": float(pnls.max()),
            "max_loss": float(pnls.min()),
        }


# ----------------------------------------------------------------------
# 事件 -> 交易计划
# ----------------------------------------------------------------------

#: 哪些事件产生入场信号
ENTRY_KINDS = {
    "TEST": "右侧买点1（无供给测试）",
    "LPS": "右侧买点2（回踩小溪）",
    "FTI": "破冰反向",
}


def _atr(highs, lows, closes, i: int, window: int) -> float:
    lo = max(0, i - window + 1)
    trs = []
    for k in range(lo, i + 1):
        if k == 0:
            trs.append(highs[k] - lows[k])
        else:
            trs.append(max(
                highs[k] - lows[k],
                abs(highs[k] - closes[k - 1]),
                abs(lows[k] - closes[k - 1]),
            ))
    return float(np.mean(trs)) if trs else float(highs[i] - lows[i])


def build_plans(
    result: WyckoffResult | list[WyckoffResult],
    df,
    config: TradeConfig | None = None,
    exec_df=None,
) -> list[dict]:
    """把事件流转换成交易计划（支持指数识别 -> 主连跨资产执行映射）。

    :param df: 信号识别源数据（如加权指数，无换月跳空）
    :param exec_df: 真实撮合执行数据（如主力连续合约，真实成交价位）。若为 None 则默认同 df。
    """
    cfg = config or TradeConfig()
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    target_df = exec_df if exec_df is not None else df
    target_date_map = {dt: idx for idx, dt in enumerate(target_df.index)}

    results = result if isinstance(result, list) else [result]
    events: list[WyckoffEvent] = []
    range_map: dict[tuple[str, int], object] = {}
    for res in results:
        events.extend(res.events)
        for r in res.ranges:
            range_map[(r.direction, r.id)] = r
    events.sort(key=lambda e: e.index)

    plans: list[dict] = []

    for ev in events:
        if ev.kind not in ENTRY_KINDS or ev.kind not in cfg.trade_signals:
            continue
        tr = range_map.get((ev.direction if ev.kind != "FTI" else
                            ("long" if ev.direction == "short" else "short"), ev.tr_id))
        if tr is None:
            # 兜底：只按 id 找
            tr = next((r for (d, i), r in range_map.items() if i == ev.tr_id), None)
        if tr is None:
            continue

        i = ev.index
        if i + 1 >= len(df):
            continue

        atr = _atr(highs, lows, closes, i, cfg.atr_window)
        is_long = ev.direction == "long"
        width = max(tr.width, atr)

        if not is_long and not cfg.allow_short:
            continue

        if ev.kind == "FTI":
            # 结构失败反向：止损放在结构边界外
            if is_long:
                stop = min(lows[max(0, i - 2): i + 1]) * (1 - cfg.stop_buffer_pct)
                risk_unit = abs(closes[i] - stop)
                target = closes[i] + max(risk_unit * 2.0, atr * 2.0)
            else:
                stop = max(highs[max(0, i - 2): i + 1]) * (1 + cfg.stop_buffer_pct)
                risk_unit = abs(stop - closes[i])
                target = closes[i] - max(risk_unit * 2.0, atr * 2.0)
        elif ev.kind == "LPS":
            # LPS 位于小溪线附近回踩：止损收敛至回踩低点或 Creek 下方 1.5 ATR（不再放回冰线）
            if is_long:
                recent_low = min(lows[max(0, i - 3): i + 1])
                stop = min(recent_low, tr.creek - 1.5 * atr) * (1 - cfg.stop_buffer_pct)
                risk_unit = abs(closes[i] - stop)
                target = closes[i] + max(risk_unit * 2.5, width * cfg.target_r, atr * 3.0)
            else:
                recent_high = max(highs[max(0, i - 3): i + 1])
                stop = max(recent_high, tr.creek + 1.5 * atr) * (1 + cfg.stop_buffer_pct)
                risk_unit = abs(stop - closes[i])
                target = closes[i] - max(risk_unit * 2.5, width * cfg.target_r, atr * 3.0)
        else:
            # TEST 信号（在冰线附近测试）：止损在 Spring 极值或冰线下方，目标确保至少 2:1 盈亏比
            if is_long:
                stop = min(tr.spring_extreme or tr.ice, tr.ice) * (1 - cfg.stop_buffer_pct)
                risk_unit = abs(closes[i] - stop)
                target = closes[i] + max(risk_unit * 2.0, width * cfg.target_r)
            else:
                stop = max(tr.spring_extreme or tr.ice, tr.ice) * (1 + cfg.stop_buffer_pct)
                risk_unit = abs(stop - closes[i])
                target = closes[i] - max(risk_unit * 2.0, width * cfg.target_r)

        if risk_unit <= 1e-9:
            continue

        sig_time = df.index[i]
        if exec_df is not None:
            exec_i = target_date_map.get(sig_time)
            if exec_i is None or exec_i + 1 >= len(target_df):
                continue
            entry_idx = exec_i + 1
            # 跨资产映射：按信号源的相对风险百分比等比例映射到执行合约价格
            sig_entry_est = closes[i]
            exec_entry_est = target_df["close"].iloc[exec_i]
            if ev.direction == "long":
                risk_ratio = max(sig_entry_est - stop, atr * 0.5) / max(sig_entry_est, 1e-9)
                target_ratio = max(target - sig_entry_est, atr * 1.0) / max(sig_entry_est, 1e-9)
                mapped_stop = float(exec_entry_est * (1.0 - risk_ratio))
                mapped_target = float(exec_entry_est * (1.0 + target_ratio))
                mapped_risk = float(exec_entry_est * risk_ratio)
            else:
                risk_ratio = max(stop - sig_entry_est, atr * 0.5) / max(sig_entry_est, 1e-9)
                target_ratio = max(sig_entry_est - target, atr * 1.0) / max(sig_entry_est, 1e-9)
                mapped_stop = float(exec_entry_est * (1.0 + risk_ratio))
                mapped_target = float(exec_entry_est * (1.0 - target_ratio))
                mapped_risk = float(exec_entry_est * risk_ratio)
        else:
            entry_idx = i + 1
            mapped_stop = float(stop)
            mapped_target = float(target)
            mapped_risk = float(risk_unit)

        plans.append({
            "signal_index": i,
            "signal_time": ev.time,
            "entry_index": entry_idx,
            "direction": ev.direction,
            "stop": mapped_stop,
            "target": mapped_target,
            "risk_unit": mapped_risk,
            "atr": float(atr),
            "kind": ev.kind,
            "tr_id": ev.tr_id,
            "reason": ENTRY_KINDS[ev.kind],
            "is_cross_asset": exec_df is not None,
        })

    return plans


# ----------------------------------------------------------------------
# 逐 K 回测
# ----------------------------------------------------------------------

def simulate(
    result: WyckoffResult | list[WyckoffResult],
    df,
    contract_size: float = 1.0,
    capital: float = 1_000_000.0,
    config: TradeConfig | None = None,
    exec_df=None,
) -> SimResult:
    """按"信号 t 确认、t+1 开盘成交"逐 K 模拟（支持指数识别 -> 主连撮合桥接）。

    :param df: 信号识别输入数据（如加权指数）
    :param exec_df: 真实撮合执行数据（如主力连续合约）。若 None 则使用 df。
    """
    cfg = config or TradeConfig()
    sim_df = exec_df if exec_df is not None else df
    highs = sim_df["high"].to_numpy(dtype=float)
    lows = sim_df["low"].to_numpy(dtype=float)
    opens = sim_df["open"].to_numpy(dtype=float)
    closes = sim_df["close"].to_numpy(dtype=float)
    index = sim_df.index

    plans = build_plans(result, df, cfg, exec_df=exec_df)
    plans.sort(key=lambda p: p["entry_index"])
    trades: list[Trade] = []
    equity: list[float] = []
    equity_index: list = []

    balance = float(capital)
    plan_by_entry: dict[int, list[dict]] = {}
    for p in plans:
        plan_by_entry.setdefault(p["entry_index"], []).append(p)

    open_trades: list[Trade] = []

    for i in range(len(sim_df)):
        # ---------- 1) 先处理已有持仓的出场 ----------
        active_trades: list[Trade] = []
        atr_i = _atr(highs, lows, closes, i, cfg.atr_window)

        for t in open_trades:
            exit_price = None
            reason = ""

            # 更新浮盈峰值 R
            if t.direction == "long":
                unrealized_r = (closes[i] - t.entry_price) / max(
                    abs(t.entry_price - t.stop), 1e-9
                )
            else:
                unrealized_r = (t.entry_price - closes[i]) / max(
                    abs(t.stop - t.entry_price), 1e-9
                )
            if unrealized_r > t.peak_r:
                t.peak_r = unrealized_r

            # 浮盈达标后，移动止损追踪
            if cfg.trail_atr_mult > 0 and t.peak_r >= cfg.trail_after_r:
                if t.direction == "long":
                    t.trail_stop = max(t.trail_stop, closes[i] - atr_i * cfg.trail_atr_mult)
                    t.trail_stop = max(t.trail_stop, t.stop)
                else:
                    t.trail_stop = min(t.trail_stop, closes[i] + atr_i * cfg.trail_atr_mult)
                    t.trail_stop = min(t.trail_stop, t.stop)

            if t.direction == "long":
                if lows[i] <= t.stop:
                    exit_price, reason = t.stop, "止损"
                elif highs[i] >= t.target:
                    exit_price, reason = t.target, "止盈"
                elif t.trail_stop > t.stop and lows[i] <= t.trail_stop:
                    exit_price, reason = t.trail_stop, "移动止损"
            else:
                if highs[i] >= t.stop:
                    exit_price, reason = t.stop, "止损"
                elif lows[i] <= t.target:
                    exit_price, reason = t.target, "止盈"
                elif t.trail_stop < t.stop and highs[i] >= t.trail_stop:
                    exit_price, reason = t.trail_stop, "移动止损"

            if exit_price is None and (i - t.entry_index) >= cfg.max_hold_bars:
                exit_price, reason = closes[i], "时间止损"

            if exit_price is not None:
                _close_trade(t, i, index[i], float(exit_price), reason,
                             contract_size, cfg)
                balance += t.net_pnl
                trades.append(t)
            else:
                active_trades.append(t)

        open_trades = active_trades

        # ---------- 2) 再处理新开仓 / 反手 / 金字塔加仓 ----------
        if i in plan_by_entry:
            for plan in plan_by_entry[i]:
                entry_price_raw = opens[i]
                slip = entry_price_raw * cfg.slippage_pct
                entry_price = (
                    entry_price_raw + slip if plan["direction"] == "long"
                    else entry_price_raw - slip
                )
                stop = plan["stop"]

                # 反手逻辑：若新信号与当前持仓反向（如多头遇到破冰 FTI）
                has_opposite = any(t.direction != plan["direction"] for t in open_trades)
                if has_opposite and cfg.allow_reversal:
                    # 将所有反向持仓立即市价平仓
                    for t in open_trades:
                        _close_trade(t, i, index[i], entry_price_raw, "反手平仓", contract_size, cfg)
                        balance += t.net_pnl
                        trades.append(t)
                    open_trades = []

                # 金字塔与仓位控制检查
                if len(open_trades) >= cfg.max_concurrent_trades:
                    continue

                if open_trades:
                    # 已有同向持仓：加仓前检查底仓是否浮盈达标（绝不逆势亏损加仓）
                    base_trade = open_trades[0]
                    if base_trade.peak_r < cfg.pyramid_min_r:
                        continue
                    # 保护底仓：将底仓止损上调至保本价（成本线上方）
                    if base_trade.direction == "long":
                        base_trade.stop = max(base_trade.stop, base_trade.entry_price * 1.001)
                    else:
                        base_trade.stop = min(base_trade.stop, base_trade.entry_price * 0.999)

                # 跳空与有效性检查
                atr_entry = plan.get("atr", atr_i)
                if plan["direction"] == "long":
                    gap = entry_price_raw - closes[i - 1] if i > 0 else 0.0
                    if gap > cfg.max_gap_atr * atr_entry or entry_price <= stop:
                        continue
                else:
                    gap = closes[i - 1] - entry_price_raw if i > 0 else 0.0
                    if gap > cfg.max_gap_atr * atr_entry or entry_price >= stop:
                        continue

                risk_unit = abs(entry_price - stop)
                if risk_unit <= 1e-9:
                    continue

                risk_amount = balance * cfg.risk_per_trade
                size = risk_amount / (risk_unit * contract_size)
                max_size = balance * cfg.max_position_notional / max(entry_price * contract_size, 1e-9)
                size = min(size, max_size)
                if size <= 0:
                    continue

                new_trade = Trade(
                    direction=plan["direction"],
                    entry_index=i,
                    entry_time=index[i],
                    entry_price=entry_price,
                    stop=stop,
                    target=plan["target"],
                    size=size,
                    risk_amount=risk_amount,
                    tr_id=plan["tr_id"],
                    signal_kind=plan["kind"],
                    is_cross_asset=bool(plan.get("is_cross_asset", False)),
                )
                new_trade.trail_stop = stop
                open_trades.append(new_trade)
                break  # 每个 bar 最多新开 1 笔

        equity.append(balance)
        equity_index.append(index[i])

    # 未平仓强制按最后收盘价结算
    for open_trade in open_trades:
        _close_trade(
            open_trade, len(df) - 1, index[-1], float(closes[-1]),
            "期末平仓", contract_size, cfg,
        )
        balance += open_trade.net_pnl
        trades.append(open_trade)

    return SimResult(
        trades=trades,
        equity=equity,
        equity_index=equity_index,
        config=cfg,
        capital=capital,
    )


def _close_trade(
    t: Trade,
    i: int,
    time,
    price: float,
    reason: str,
    contract_size: float,
    cfg: TradeConfig,
) -> None:
    slip = price * cfg.slippage_pct
    fill = price - slip if t.direction == "long" else price + slip

    if t.direction == "long":
        gross = (fill - t.entry_price) * t.size * contract_size
        turnover = (t.entry_price + fill) * t.size * contract_size
    else:
        gross = (t.entry_price - fill) * t.size * contract_size
        turnover = (t.entry_price + fill) * t.size * contract_size

    commission = turnover * cfg.commission_rate + t.size * cfg.fixed_fee_per_lot
    t.exit_index = i
    t.exit_time = time
    t.exit_price = fill
    t.exit_reason = reason
    t.gross_pnl = gross
    t.net_pnl = gross - commission
    t.bars_held = i - t.entry_index
    t.r_multiple = t.net_pnl / max(t.risk_amount, 1e-9)

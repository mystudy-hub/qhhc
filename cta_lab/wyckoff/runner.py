"""维科夫模块的运行器：从本地数据库取数 -> 识别 -> 回测 -> 汇总。

    from cta_lab.wyckoff import run_backtest
    r = run_backtest("rb.SHFE")
    print(r.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..contracts import get_spec
from ..store import init_database


def load_df(
    vt_symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
    db=None,
) -> pd.DataFrame:
    """从本地 vnpy 数据库读取日线为 DataFrame。

    列：open / high / low / close / volume / open_interest
    索引：DatetimeIndex（升序）
    """
    from vnpy.trader.constant import Exchange, Interval

    if "." not in vt_symbol:
        raise ValueError("vt_symbol 需形如 rb.SHFE")
    symbol, exch = vt_symbol.split(".", 1)
    exchange = Exchange(exch)

    database = db or init_database(db_name="futures_lab.db")
    bars = database.load_bar_data(
        symbol,
        exchange,
        Interval.DAILY,
        start or datetime(1990, 1, 1),
        end or datetime(2100, 1, 1),
    )
    if not bars:
        raise RuntimeError(f"本地无 {vt_symbol} 日线数据，请先 python -m cta_lab.dataio {symbol}")

    df = pd.DataFrame(
        {
            "open": [b.open_price for b in bars],
            "high": [b.high_price for b in bars],
            "low": [b.low_price for b in bars],
            "close": [b.close_price for b in bars],
            "volume": [b.volume for b in bars],
            "open_interest": [b.open_interest for b in bars],
        },
        index=pd.DatetimeIndex([b.datetime for b in bars]),
    )
    df.index.name = "datetime"
    return df


def _pro_trade_kinds(signals) -> tuple[str, ...]:
    """把旧版 `TradeConfig.trade_signals`（事件展示名）展开为 Pro 引擎的 kind 集合。

    旧引擎与 Pro 引擎事件命名不同（旧：TEST/LPS/FTI；Pro：SPRING3/TEST/LPS/JAC/SOS/FTI），
    若不显式映射，CLI/Web 上的「纯核心 / 禁空」控制会被静默丢弃。
    """
    alias: dict[str, tuple[str, ...]] = {
        "SPRING3": ("SPRING3",),
        "SPRING": ("SPRING3",),
        "UTAD_#3": ("SPRING3",),
        "TEST": ("TEST",),
        "TEST_SELL": ("TEST",),
        "LPS": ("LPS",),
        "LPSY": ("LPS",),
        "BUEC": ("LPS",),
        "JAC": ("JAC",),
        "SOS": ("SOS",),
        "FTI": ("FTI",),
        "JAC_BREAKOUT": ("FTI",),
    }
    out: list[str] = []
    for s in signals or ():
        for k in alias.get(str(s).upper(), ()):
            if k not in out:
                out.append(k)
    return tuple(out)


@dataclass
class WyckoffBacktest:
    """一次完整回测的结果。"""

    vt_symbol: str
    df: object
    acc: object                  # WyckoffResult（吸筹）
    dist: object | None          # WyckoffResult（派发）
    sim_long: object             # SimResult
    sim_short: object | None
    sim_both: object             # 合并后的 SimResult
    contract_size: float = 1.0
    capital: float = 1_000_000.0
    is_bridged: bool = False     # 是否启用了“指数识别 -> 主连执行”桥接
    signal_df: object = None     # 信号端数据（加权指数）
    exec_df: object = None       # 执行端数据（主力连续）
    is_pro: bool = False         # 是否使用 Pro 专业版全套阶段引擎
    pro_result: dict | None = None # Pro 引擎的原生回测字典（含 vbt 组合与高精度明细）

    # ---- 汇总 ------------------------------------------------------
    def stats(self) -> dict:
        return self.sim_both.statistics()

    def summary(self) -> str:
        s = self.stats()
        acc_ev = self.acc.event_counts()
        dist_ev = self.dist.event_counts() if self.dist else {}

        lines = [
            "=" * 70,
            f"  维科夫识别与回测   {self.vt_symbol}",
            f"  数据区间 {self.df.index[0]:%Y-%m-%d} ~ {self.df.index[-1]:%Y-%m-%d}"
            f"  共 {len(self.df)} 根",
            "=" * 70,
            "",
            "【结构识别】",
            f"  吸筹区间 {len(self.acc.ranges)} 个  事件 {sum(acc_ev.values())} 个",
            f"    {acc_ev if acc_ev else '无'}",
        ]
        if self.dist is not None:
            lines += [
                f"  派发区间 {len(self.dist.ranges)} 个  事件 {sum(dist_ev.values())} 个",
                f"    {dist_ev if dist_ev else '无'}",
            ]

        lines += [
            "",
            "【交易结果】",
            f"  成交笔数      {s['trades']:>12d}",
            f"  胜率          {s['win_rate']:>11.2f}%",
            f"  平均 R        {s['avg_r']:>12.3f}",
            f"  盈亏比(因子)  {s['profit_factor']:>12.3f}",
            f"  单笔期望      {s['expectancy']:>12,.0f}",
            f"  总盈亏        {s['total_pnl']:>12,.0f}",
            f"  总收益率      {s['total_return']:>11.2f}%",
            f"  最大回撤      {s['max_drawdown_pct']:>11.2f}%",
            f"  夏普          {s['sharpe']:>12.3f}",
            f"  平均持仓      {s['avg_bars']:>12.1f} 根",
            "=" * 70,
        ]
        return "\n".join(lines)

    def trades_df(self) -> pd.DataFrame:
        rows = [t.as_dict() for t in self.sim_both.trades]
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def events_df(self) -> pd.DataFrame:
        rows = self.acc.timeline()
        if self.dist is not None:
            rows += self.dist.timeline()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def build_synthetic_index(df: pd.DataFrame, max_gap_atr: float = 2.0) -> pd.DataFrame:
    """基于主连高频价差平滑，生成无换月跳空的连续指数序列（Panama Back-adjusted）。

    对冲掉换月升贴水大缺口，使价格序列平滑连续，保护冰线和小溪线形态。
    """
    if len(df) < 5:
        return df.copy()

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
    tr[0] = highs[0] - lows[0]
    atr = pd.Series(tr).rolling(20, min_periods=1).mean().to_numpy()

    cum_gap = 0.0
    adj_opens = np.zeros_like(opens)
    adj_highs = np.zeros_like(highs)
    adj_lows = np.zeros_like(lows)
    adj_closes = np.zeros_like(closes)
    adj_opens[0] = opens[0]
    adj_highs[0] = highs[0]
    adj_lows[0] = lows[0]
    adj_closes[0] = closes[0]

    for i in range(1, len(df)):
        gap = opens[i] - closes[i - 1]
        if abs(gap) > max_gap_atr * atr[i]:
            cum_gap += gap
        adj_opens[i] = opens[i] - cum_gap
        adj_highs[i] = highs[i] - cum_gap
        adj_lows[i] = lows[i] - cum_gap
        adj_closes[i] = closes[i] - cum_gap

    idx_df = pd.DataFrame(
        {
            "open": adj_opens,
            "high": adj_highs,
            "low": adj_lows,
            "close": adj_closes,
            "volume": df["volume"].values,
            "open_interest": df["open_interest"].values,
        },
        index=df.index,
    )
    idx_df.index.name = "datetime"
    return idx_df


def load_index_or_synthetic(
    vt_symbol: str,
    df_main: pd.DataFrame,
    start: datetime | None = None,
    end: datetime | None = None,
    db=None,
) -> pd.DataFrame:
    """优先加载真实加权指数，若未下载则自动构造平滑合成指数。"""
    symbol, exch = vt_symbol.split(".", 1)
    index_vt = f"{symbol}_index.{exch}"
    try:
        df_idx = load_df(index_vt, start, end, db=db)
        if len(df_idx) >= 10:
            return df_idx
    except Exception:
        pass
    return build_synthetic_index(df_main)


def run_backtest(
    vt_symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
    capital: float = 1_000_000.0,
    trade_config=None,
    engine_config=None,
    include_distribution: bool = True,
    bridge_index: bool = True,
    use_pro: bool = True,
    use_weekly_filter: bool = False,
    db=None,
) -> WyckoffBacktest:
    """一键回测（默认推荐使用全套五阶段 Pro 专业版引擎）。"""
    df_main = load_df(vt_symbol, start, end, db=db)
    symbol = vt_symbol.split(".")[0]
    try:
        spec = get_spec(symbol)
        size = spec.size
        limit_pct = spec.limit_pct
    except KeyError:
        size = 1.0
        limit_pct = 0.0

    # 1) Pro 专业版引擎执行路径（默认推荐，胜率 58%+，盈亏比 2.5+，九大检验过滤）
    if use_pro:
        from .pro import (
            BacktestConfigPro,
            DistributionProEngine,
            WyckoffProConfig,
            WyckoffProEngine,
            run_wyckoff_backtest,
        )
        from .trading import SimResult, Trade

        # 周期模式映射：macro=日线跨度，micro/swing=60m 波段，intraday=短线
        # 注：旧版 WyckoffConfig 的数值参数属于另一套状态机语义，不得注入 Pro 引擎（否则会互相矛盾）
        raw_mode = "macro"
        if engine_config is not None and hasattr(engine_config, "mode"):
            raw_mode = str(getattr(engine_config, "mode", "macro")).lower()
        tf = "1d" if raw_mode in ("macro", "1d", "daily") else ("60m" if raw_mode in ("micro", "swing") else "1m")
        pcfg = WyckoffProConfig.from_timeframe(tf)
        # 结构识别用无跳空平滑指数，成交仍在主连真实价格上（换月缺口不得污染冰线/小溪与信号）
        signal_df = (
            load_index_or_synthetic(vt_symbol, df_main, start, end, db=db)
            if bridge_index
            else df_main
        )
        if len(signal_df) != len(df_main) or not signal_df.index.equals(df_main.index):
            signal_df = signal_df.reindex(df_main.index).ffill().bfill()
        acc = WyckoffProEngine(pcfg).process(signal_df)
        # 复用吸筹端已算好的 VSA/Weis 特征，避免双向引擎重复批计算
        dist = (
            DistributionProEngine(pcfg).process(signal_df, vsa_df=acc.df)
            if include_distribution else None
        )

        bt_kwargs = {}
        if trade_config is not None:
            # TradeConfig 与 BacktestConfigPro 字段名不同，必须显式映射（否则参数被静默丢弃）
            for src, dst in (
                ("risk_per_trade", "risk_per_trade"),
                ("commission_rate", "fees"),
                ("slippage_pct", "slippage"),
                ("max_hold_bars", "max_hold_bars"),
                ("allow_short", "allow_short"),
            ):
                if hasattr(trade_config, src):
                    bt_kwargs[dst] = getattr(trade_config, src)
            if hasattr(trade_config, "trade_signals"):
                kinds = _pro_trade_kinds(getattr(trade_config, "trade_signals"))
                if kinds:
                    bt_kwargs["trade_kinds"] = kinds

        pbt_cfg = BacktestConfigPro(
            init_cash=capital,
            use_weekly_filter=use_weekly_filter,
            limit_pct=limit_pct,
            **bt_kwargs,
        )
        bt_dict = run_wyckoff_backtest(
            df_main, acc, dist,
            bt_cfg=pbt_cfg,
            contract_size=size,
            prefer_vectorbt=False,
            signal_df=signal_df if bridge_index else None,
        )

        trades_objs: list[Trade] = []
        for _, row in bt_dict["trades"].iterrows():
            t = Trade(
                direction=str(row["direction"]),
                entry_index=int(row["entry_index"]),
                entry_time=row["entry_time"],
                entry_price=float(row["entry_price"]),
                stop=float(row["stop"]),
                target=float(row["target"]),
                size=float(row["size"]),
                risk_amount=float(row["risk_capital"]),
                exit_index=int(row["exit_index"]),
                exit_time=row["exit_time"],
                exit_price=float(row["exit_price"]),
                exit_reason=str(row["exit_reason"]),
                net_pnl=float(row["net_pnl"]),
                r_multiple=float(row["r_multiple"]),
                bars_held=int(row["bars_held"]),
                signal_kind=str(row["signal"]),
                partial_size=float(row.get("partial_size", 0.0) or 0.0),
            )
            trades_objs.append(t)

        sim_both = SimResult(
            trades=trades_objs,
            capital=capital,
            equity=bt_dict["equity"].tolist() if "equity" in bt_dict and hasattr(bt_dict["equity"], "tolist") else [],
            equity_index=list(df_main.index),
        )
        return WyckoffBacktest(
            vt_symbol=vt_symbol,
            df=df_main,
            acc=acc,
            dist=dist,
            sim_long=sim_both,
            sim_short=None,
            sim_both=sim_both,
            contract_size=size,
            capital=capital,
            is_bridged=bridge_index,
            signal_df=signal_df,
            exec_df=df_main,
            is_pro=True,
            pro_result=bt_dict,
        )

    # 2) 旧版 V2.1 引擎回退路径
    from .engine import (
        DistributionConfig,
        DistributionEngine,
        WyckoffConfig,
        WyckoffEngine,
    )
    from .trading import TradeConfig, simulate

    tcfg = trade_config or TradeConfig()
    ecfg = engine_config or WyckoffConfig.create_macro()

    # 确定信号数据与执行数据
    if bridge_index:
        signal_df = load_index_or_synthetic(vt_symbol, df_main, start, end, db=db)
        exec_df = df_main
    else:
        signal_df = df_main
        exec_df = None

    acc = WyckoffEngine(ecfg).process(signal_df)

    # 派发用独立参数
    dcfg = DistributionConfig(
        lookback=ecfg.lookback,
        vol_z_thresh=ecfg.vol_z_thresh * 0.87,
        min_cause_bars=max(10, ecfg.min_cause_bars - 5),
        max_spring_bars=ecfg.max_spring_bars,
        zone_buffer_pct=ecfg.zone_buffer_pct,
        ar_min_move_atr=ecfg.ar_min_move_atr * 0.75,
        ar_retrace_atr=ecfg.ar_retrace_atr,
        ar_min_rebound_pct=ecfg.ar_min_rebound_pct * 0.75,
        use_oi_confirm=ecfg.use_oi_confirm,
        fti_require_volume=ecfg.fti_require_volume,
        fti_vol_floor=ecfg.fti_vol_floor,
        oi_lookback=ecfg.oi_lookback,
    )
    dist = DistributionEngine(dcfg).process(signal_df) if include_distribution else None

    # 关键：多空信号放进**同一次模拟**里按时间顺序持仓
    if dist is not None:
        sim_both = simulate([acc, dist], signal_df, contract_size=size,
                            capital=capital, config=tcfg, exec_df=exec_df)
    else:
        sim_both = simulate(acc, signal_df, contract_size=size,
                            capital=capital, config=tcfg, exec_df=exec_df)

    sim_long = sim_both
    sim_short = None

    return WyckoffBacktest(
        vt_symbol=vt_symbol,
        df=df_main,
        acc=acc,
        dist=dist,
        sim_long=sim_long,
        sim_short=sim_short,
        sim_both=sim_both,
        contract_size=size,
        capital=capital,
        is_bridged=bridge_index,
        signal_df=signal_df,
        exec_df=df_main,
    )


def run_both(vt_symbol: str, **kwargs) -> WyckoffBacktest:
    """run_backtest 的别名（多空都跑）。"""
    kwargs.setdefault("include_distribution", True)
    return run_backtest(vt_symbol, **kwargs)


def scan(
    symbols: list[str],
    start: datetime | None = None,
    end: datetime | None = None,
    capital: float = 1_000_000.0,
    db=None,
    verbose: bool = True,
) -> pd.DataFrame:
    """多品种扫描，返回按夏普排序的汇总表。"""
    rows = []
    for idx, vt in enumerate(symbols, 1):
        try:
            r = run_backtest(vt, start=start, end=end, capital=capital, db=db)
            s = r.stats()
            rows.append({
                "vt_symbol": vt,
                "trades": s["trades"],
                "win_rate": round(s["win_rate"], 2),
                "avg_r": round(s["avg_r"], 3),
                "profit_factor": round(s["profit_factor"], 3),
                "total_return": round(s["total_return"], 2),
                "max_dd": round(s["max_drawdown_pct"], 2),
                "sharpe": round(s["sharpe"], 3),
                "events": len(r.acc.events)
                + (len(r.dist.events) if r.dist is not None else 0),
            })
            if verbose:
                print(f"  [{idx}/{len(symbols)}] {vt:<12s} "
                      f"笔数={s['trades']:3d} 胜率={s['win_rate']:5.1f}% "
                      f"R={s['avg_r']:5.2f} 收益={s['total_return']:7.2f}% "
                      f"回撤={s['max_drawdown_pct']:7.2f}% 夏普={s['sharpe']:6.3f}")
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  [{idx}/{len(symbols)}] {vt:<12s} 失败: {type(exc).__name__}: {exc}")

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("sharpe", ascending=False).reset_index(drop=True)
    return out

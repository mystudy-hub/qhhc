"""组合层风控与资金曲线模拟。

单品种回测只能回答"这个信号在这个品种上有没有正向期望"，无法回答
"把 80 个品种同时跑起来，账户会怎么走"。本模块补上这一层：

1. **组合热度上限（Portfolio Heat）**：同时持有的未平仓风险总和不超过账户的固定比例，
   避免宏观共振日 6~8 个品种同时被反向扫损造成断崖式回撤；
2. **同板块同向并发上限（Sector Cap）**：同一板块同一方向最多持有 N 个品种，
   压降板块内高度相关的伪分散；
3. **顺序复利权益曲线**：每笔交易按「入场时账户权益 × 单笔风险比例」确定风险金额，
   平仓时按 R 倍数结算，输出日频权益、年化、最大回撤、夏普/索提诺。

所有选择规则只用**入场时刻已知的信息**（入场时间先后），不使用未来收益排序，
因此不引入前视偏差。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..contracts import sector_of


@dataclass
class PortfolioConfig:
    """组合层参数。"""

    capital: float = 1_000_000.0
    risk_per_trade: float = 0.0025   # 单笔风险占当前权益比例
    max_concurrent: int = 8          # 最大并发持仓数
    max_per_sector_dir: int = 2      # 同板块同向最大并发数（0 表示不限制）
    max_heat: float = 0.06           # 在途总风险上限（占权益），0 表示不限制


@dataclass
class PortfolioResult:
    equity: pd.Series
    taken: pd.DataFrame
    skipped: int
    stats: dict

    def summary(self) -> str:
        s = self.stats
        return (
            f"成交 {s['trades']} 笔 / 跳过 {self.skipped} 笔 | "
            f"年化 {s['cagr_pct']:+.2f}% | 最大回撤 {s['max_drawdown_pct']:.2f}% | "
            f"终值 {s['final_multiple']:.3f}x | 夏普 {s['sharpe']:.3f} | 索提诺 {s['sortino']:.3f} | "
            f"avgR {s['avg_r']:+.3f}"
        )


def _empty_stats() -> dict:
    return {
        "trades": 0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "final_multiple": 1.0,
        "sharpe": 0.0, "sortino": 0.0, "avg_r": 0.0, "years": 0.0,
    }


def run_portfolio(trades: pd.DataFrame, cfg: PortfolioConfig | None = None) -> PortfolioResult:
    """对多品种逐笔交易做组合级资金曲线模拟。

    :param trades: 必须包含 entry_time / exit_time / r_multiple / direction / symbol 列
    :param cfg: 组合参数
    """
    cfg = cfg or PortfolioConfig()
    if trades is None or len(trades) == 0:
        return PortfolioResult(pd.Series(dtype=float), pd.DataFrame(), 0, _empty_stats())

    df = trades.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    if "sector" not in df.columns:
        df["sector"] = df["symbol"].map(sector_of)
    df = df.sort_values("entry_time", kind="stable").reset_index(drop=True)

    equity = float(cfg.capital)
    curve: list[tuple[pd.Timestamp, float]] = []
    open_book: list[dict] = []   # {exit_time, risk_amount, r_multiple, sector, direction, symbol}
    taken: list[dict] = []
    skipped = 0

    def _release(now: pd.Timestamp) -> None:
        nonlocal equity, open_book
        still: list[dict] = []
        for pos in open_book:
            if pos["exit_time"] <= now:
                equity += pos["risk_amount"] * pos["r_multiple"]
                curve.append((pos["exit_time"], equity))
            else:
                still.append(pos)
        open_book = still

    for _, row in df.iterrows():
        _release(row["entry_time"])

        # 组合热度：在途风险占权益比例
        heat = sum(p["risk_amount"] for p in open_book) / max(equity, 1e-9)
        if cfg.max_heat > 0 and open_book and heat + cfg.risk_per_trade > cfg.max_heat:
            skipped += 1
            continue
        if cfg.max_concurrent > 0 and len(open_book) >= cfg.max_concurrent:
            skipped += 1
            continue
        if cfg.max_per_sector_dir > 0:
            same = sum(
                1 for p in open_book
                if p["sector"] == row["sector"] and p["direction"] == row["direction"]
            )
            if same >= cfg.max_per_sector_dir:
                skipped += 1
                continue

        risk_amount = equity * cfg.risk_per_trade
        pos = {
            "exit_time": row["exit_time"],
            "risk_amount": risk_amount,
            "r_multiple": float(row["r_multiple"]),
            "sector": row["sector"],
            "direction": row["direction"],
            "symbol": row["symbol"],
            "entry_time": row["entry_time"],
        }
        open_book.append(pos)
        taken.append(pos)

    # 收尾：平掉所有剩余持仓
    for pos in sorted(open_book, key=lambda p: p["exit_time"]):
        equity += pos["risk_amount"] * pos["r_multiple"]
        curve.append((pos["exit_time"], equity))
    open_book = []

    if not curve:
        return PortfolioResult(pd.Series(dtype=float), pd.DataFrame(), skipped, _empty_stats())

    s = pd.Series([c[1] for c in curve], index=pd.DatetimeIndex([c[0] for c in curve]))
    s = s.groupby(level=0).last()                      # 同日多笔结算 → 取当日最终权益
    days = pd.bdate_range(s.index.min(), s.index.max())
    daily = s.reindex(days).ffill().bfill()

    # 权益曲线可能只有单个结算点（样本极短），此时年化无意义：退化用简单收益率并给年限设下限
    span_days = (daily.index[-1] - daily.index[0]).days
    years = max(span_days / 365.25, 1.0 / 12.0)
    final_mult = float(daily.iloc[-1] / cfg.capital)
    if span_days < 30 or final_mult <= 0:
        cagr = (final_mult - 1.0) * 100.0
    else:
        cagr = (final_mult ** (1.0 / years) - 1.0) * 100.0
    dd = float((daily / daily.cummax() - 1.0).min() * 100.0)
    rets = daily.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(252)) if len(rets) > 5 and rets.std(ddof=0) > 0 else 0.0
    downside = rets[rets < 0].std(ddof=0) if len(rets[rets < 0]) > 1 else 0.0
    sortino = float(rets.mean() / downside * np.sqrt(252)) if downside and downside > 0 else 0.0

    rs = np.array([p["r_multiple"] for p in taken], dtype=float)
    stats = {
        "trades": len(taken),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(dd, 2),
        "final_multiple": round(final_mult, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "avg_r": round(float(rs.mean()) if len(rs) else 0.0, 4),
        "years": round(years, 2),
    }
    taken_df = pd.DataFrame(taken).drop(columns=["risk_amount"], errors="ignore")
    return PortfolioResult(daily, taken_df, skipped, stats)


def filter_portfolio_clustered_trades(
    trades_df: pd.DataFrame,
    max_per_sector_dir: int = 2,
    max_total_concurrent: int = 8,
) -> pd.DataFrame:
    """轻量级组合簇拥过滤（不做资金曲线，只筛交易）。

    按入场时间顺序扫描候选交易，限制同一时刻：
    1. 同一板块、同一方向的在途持仓数不超过 ``max_per_sector_dir``；
    2. 全市场总并发不超过 ``max_total_concurrent``。

    用于快速评估"如果加了板块并发约束，还剩多少可交易机会"，
    完整资金曲线请用 :func:`run_portfolio`。
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame() if trades_df is None else trades_df.copy()

    df_sorted = trades_df.sort_values("entry_time", kind="stable").reset_index(drop=True)
    accepted: list[int] = []
    active: list[dict] = []

    for idx, row in df_sorted.iterrows():
        entry_t = pd.to_datetime(row["entry_time"])
        exit_t = pd.to_datetime(row["exit_time"])
        sec = str(row.get("sector") or sector_of(str(row.get("symbol", ""))))
        direction = str(row.get("direction", "long"))

        active = [a for a in active if a["exit_time"] > entry_t]
        if len(active) >= max_total_concurrent:
            continue
        if sum(1 for a in active if a["sector"] == sec and a["direction"] == direction) >= max_per_sector_dir:
            continue

        accepted.append(idx)
        active.append({"exit_time": exit_t, "sector": sec, "direction": direction})

    return df_sorted.iloc[accepted].reset_index(drop=True)

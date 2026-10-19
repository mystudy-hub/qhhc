"""全品种扫描：跑遍本地数据库中所有可测品种，输出逐品种绩效与逐笔明细。

用法：
    .venv/Scripts/python.exe scripts/scan_universe.py [输出目录]

输出：
    <dir>/per_symbol.csv   逐品种绩效
    <dir>/trades.csv       逐笔明细（含 sector，供组合层模拟）
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import pandas as pd

from cta_lab.contracts import sector_of
from cta_lab.wyckoff.runner import run_backtest

DB = ROOT / ".vntrader" / "futures_lab.db"
MIN_BARS = 300


def list_symbols(db_path: Path = DB) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "select symbol, exchange, count(*) n from dbbardata group by symbol, exchange"
    ).fetchall()
    conn.close()
    return sorted({
        f"{sym}.{exch}"
        for sym, exch, n in rows
        if not sym.endswith("_index") and n >= MIN_BARS
    })


def scan(out_dir: Path, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = list_symbols()
    if verbose:
        print(f"可测合约 {len(symbols)} 个（非指数，>= {MIN_BARS} 根）", flush=True)

    per_rows: list[dict] = []
    trade_frames: list[pd.DataFrame] = []
    t0 = time.time()

    for k, vt in enumerate(symbols, 1):
        try:
            bt = run_backtest(vt, use_pro=True)
            st = bt.stats()
            td = bt.trades_df()
            per_rows.append({
                "symbol": vt,
                "sector": sector_of(vt),
                "bars": len(bt.df),
                "trades": st["trades"],
                "win": st["win_rate"],
                "avg_r": st["avg_r"],
                "pf": st["profit_factor"],
                "ret": st["total_return"],
                "mdd": st["max_drawdown_pct"],
                "sharpe": st["sharpe"],
            })
            if len(td):
                keep = [c for c in ("entry_time", "exit_time", "r_multiple", "direction", "signal") if c in td.columns]
                t = td[keep].copy()
                t["symbol"] = vt
                t["sector"] = sector_of(vt)
                trade_frames.append(t)
            if verbose:
                print(
                    f"[{k:2d}/{len(symbols)}] {vt:12s} 笔={st['trades']:3d} "
                    f"胜={st['win_rate']:5.1f}% avgR={st['avg_r']:+6.3f} "
                    f"收={st['total_return']:+7.2f}%",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[{k:2d}/{len(symbols)}] {vt:12s} 失败: {type(exc).__name__}: {exc}", flush=True)

    per = pd.DataFrame(per_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    per.to_csv(out_dir / "per_symbol.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")
    if verbose:
        print(f"\n完成，用时 {time.time() - t0:.0f}s；品种 {len(per)}，成交 {len(trades)} 笔 → {out_dir}")
    return per, trades


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports" / "current"
    scan(target)

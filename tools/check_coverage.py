"""全品种日线覆盖度实测（支持 em / sina 双源对比）。

用法::

    python _coverage.py                 # 用新浪源测全部 82 个品种
    python tools/check_coverage.py --source em     # 用东财源
    python tools/check_coverage.py --quick         # 只测 10 个抽样品种
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vnpy.trader.constant import Exchange, Interval

from cta_lab.contracts import EXCHANGE_CN_NAME, FUTURES
from cta_lab.downloader import DownloadError


@dataclass
class Row:
    symbol: str
    name: str
    exchange: str
    exchange_cn: str
    ok: bool
    bars: int = 0
    first: str = ""
    last: str = ""
    error: str = ""
    seconds: float = 0.0


def load_fetcher(source: str):
    if source == "sina":
        from cta_lab.sina_source import fetch_bars as f
        return lambda sym, ex: f(sym, ex, Interval.DAILY)
    from cta_lab.downloader import fetch_bars as f
    return lambda sym, ex: f(sym, ex, Interval.DAILY)


def test_one(fetch, symbol: str, name: str, exchange: Exchange, sleep: float) -> Row:
    t0 = time.time()
    row = Row(
        symbol=symbol,
        name=name,
        exchange=exchange.value,
        exchange_cn=EXCHANGE_CN_NAME.get(exchange, exchange.value),
        ok=False,
    )
    try:
        bars = fetch(symbol, exchange)
    except DownloadError as exc:
        row.error = str(exc).splitlines()[0][:80]
        row.seconds = time.time() - t0
        return row
    except Exception as exc:  # noqa: BLE001
        row.error = f"{type(exc).__name__}: {str(exc)[:70]}"
        row.seconds = time.time() - t0
        return row

    row.seconds = time.time() - t0
    if not bars:
        row.error = "返回空"
        return row

    row.ok = True
    row.bars = len(bars)
    row.first = bars[0].date_str
    row.last = bars[-1].date_str
    time.sleep(sleep)
    return row


def main() -> int:
    source = "sina"
    if "--source" in sys.argv:
        source = sys.argv[sys.argv.index("--source") + 1]
    quick = "--quick" in sys.argv
    sleep = 0.35 if source == "sina" else 0.6

    fetch = load_fetcher(source)

    print("=" * 78)
    print(f"  全品种日线覆盖度实测   源 = {source}")
    print("=" * 78)

    specs = list(FUTURES.values())
    if quick:
        picks = ["rb", "cu", "i", "TA", "au", "IF", "lc", "ec", "m", "SA"]
        specs = [s for s in specs if s.symbol in picks]

    rows: list[Row] = []
    for idx, spec in enumerate(specs, 1):
        row = test_one(fetch, spec.symbol, spec.name, spec.exchange, sleep)
        rows.append(row)
        if row.ok:
            print(f"  [{idx:2d}/{len(specs)}] OK   {row.symbol:<6s} {row.name:<8s} "
                  f"{row.exchange:<6s} {row.bars:5d} 根  "
                  f"{row.first} -> {row.last}")
        else:
            print(f"  [{idx:2d}/{len(specs)}] FAIL {row.symbol:<6s} {row.name:<8s} "
                  f"{row.exchange:<6s} {row.error}")

    ok_rows = [r for r in rows if r.ok]
    bad_rows = [r for r in rows if not r.ok]

    print()
    print("=" * 78)
    print("  汇总")
    print("=" * 78)
    print(f"  测试 {len(rows)} 个品种：成功 {len(ok_rows)} / 失败 {len(bad_rows)}"
          f"  覆盖率 {len(ok_rows)/max(len(rows),1):.1%}")

    if ok_rows:
        total = sum(r.bars for r in ok_rows)
        print(f"  合计 {total:,} 根，平均 {total/len(ok_rows):.0f} 根/品种")
        oldest = min(ok_rows, key=lambda r: r.first)
        print(f"  最早起点: {oldest.symbol} ({oldest.exchange}) {oldest.first}")
        latest_start = max(ok_rows, key=lambda r: r.first)
        print(f"  最晚起点: {latest_start.symbol} ({latest_start.exchange}) {latest_start.first}")
        lasts = sorted({r.last for r in ok_rows})
        print(f"  最新日期: {lasts[-1]}（{len(lasts)} 种不同截止日）")

        print("\n  按交易所统计:")
        for ex in EXCHANGE_CN_NAME:
            sub = [r for r in ok_rows if r.exchange == ex.value]
            allsub = [r for r in rows if r.exchange == ex.value]
            if allsub:
                print(f"    {EXCHANGE_CN_NAME[ex]:6s} {len(sub)}/{len(allsub)} 成功"
                      + ("  ✔" if len(sub) == len(allsub) else ""))

    if bad_rows:
        print(f"\n  失败清单（{len(bad_rows)}）:")
        for r in bad_rows:
            print(f"    {r.symbol:<6s} {r.name:<8s} {r.exchange:<6s} {r.error}")

    out = Path(f"_coverage_{source}.json")
    out.write_text(
        json.dumps(
            {
                "generated": datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "tested": len(rows),
                "ok": len(ok_rows),
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  明细 -> {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

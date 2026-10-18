"""数据落地编排：下载 -> 转换 -> 入库。

对外主要接口：
    sync_symbol(symbol, exchange, ...)    同步单个品种
    sync_many(symbols, ...)               批量同步
    sync_all(...)                         同步全部已登记品种

数据源选择
----------
通过 `source` 参数指定：
    "sina"    新浪财经（**日线默认**：回溯 2005、带结算价、频控宽松、82/82 全品种可用）
    "em"      东方财富（唯一提供 5m/15m/30m 分钟线，但 IP 频控很严，易被临时封）
    "tqsdk"   天勤 TqSdk（需账号，日线回溯 2016，分钟线较短）
    "auto"    自动降级：em -> sina -> tqsdk（em 被封时每品种要空耗约 28s，慎用）
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime

from vnpy.trader.constant import Exchange, Interval

from .contracts import FutureSpec, get_spec
from .downloader import DownloadError, fetch_bars as fetch_bars_em
from .exchange import raw_list_to_bars
from .store import init_database, list_overview, save_bars

#: 支持的数据源名
SOURCES = ("em", "sina", "tqsdk", "auto")


def _force_utf8_console() -> None:
    """Windows 控制台默认 GBK，输出中文需改为 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


@dataclass
class SyncResult:
    """单个品种同步结果。"""

    symbol: str
    exchange: Exchange
    interval: Interval
    ok: bool
    bars: int = 0
    first: datetime | None = None
    last: datetime | None = None
    error: str = ""
    source: str = ""

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"

    def __str__(self) -> str:
        if not self.ok:
            return f"[FAIL] {self.vt_symbol:12s} {self.error}"
        span = ""
        if self.first and self.last:
            span = f"{self.first:%Y-%m-%d} -> {self.last:%Y-%m-%d}"
        tag = f"({self.source})" if self.source else ""
        return f"[ OK ] {self.vt_symbol:12s} {self.bars:6d} 根  {span} {tag}"


# ---------------------------------------------------------------------------
# 单品种同步
# ---------------------------------------------------------------------------

def _fetch_em(symbol, exchange, interval, start, end, adjust):
    """东方财富源（免 token）。"""
    return fetch_bars_em(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start=start,
        end=end,
        main_continuous=True,
        adjust=adjust,
    )


def _fetch_sina(symbol, exchange, interval, start, end):
    """新浪源（日线，带结算价）。"""
    from .sina_source import fetch_bars as fetch_bars_sina

    return fetch_bars_sina(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start=start,
        end=end,
        main_continuous=True,
    )


def _fetch_tqsdk(symbol, exchange, interval, start, end):
    """天勤源（需账号）。"""
    from .tqsdk_source import fetch_bars as fetch_bars_tq

    return fetch_bars_tq(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start=start,
        end=end,
        main_continuous=True,
    )


def _fetch(
    source: str,
    symbol: str,
    exchange: Exchange,
    interval: Interval,
    start: datetime | None,
    end: datetime | None,
    adjust: int,
    verbose: bool = True,
) -> tuple[list, str, str]:
    """按 source 取数，返回 (raws, 实际用的源, 错误信息)。

    source='auto' 时先试东财，失败再试天勤。
    """
    order: list[str]
    if source == "auto":
        order = ["em", "sina", "tqsdk"]
    else:
        order = [source]

    last_error = ""
    for name in order:
        try:
            if name == "em":
                raws = _fetch_em(symbol, exchange, interval, start, end, adjust)
            elif name == "sina":
                raws = _fetch_sina(symbol, exchange, interval, start, end)
            elif name == "tqsdk":
                raws = _fetch_tqsdk(symbol, exchange, interval, start, end)
            else:
                raise ValueError(f"未知数据源 {name!r}，可选 {SOURCES}")
        except Exception as exc:  # noqa: BLE001 - 降级需要捕获所有异常
            last_error = f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:110]}"
            if verbose and len(order) > 1:
                print(f"    {name} 失败，尝试下一个源 ...")
            continue

        if raws:
            return raws, name, ""
        last_error = f"{name}: 返回空数据"
        if verbose and len(order) > 1:
            print(f"    {name} 返回空，尝试下一个源 ...")

    return [], "", last_error


def sync_symbol(
    symbol: str,
    exchange: Exchange | None = None,
    interval: Interval = Interval.DAILY,
    start: datetime | None = None,
    end: datetime | None = None,
    adjust: int = 1,
    source: str = "sina",
    db=None,
    verbose: bool = True,
) -> SyncResult:
    """下载并写入单个品种。

    参数
    ----
    exchange : 省略时自动从 contracts 表推断
    source   : "em"(东财) / "tqsdk"(天勤) / "auto"(自动降级)
    """
    spec: FutureSpec
    try:
        spec = get_spec(symbol)
        if exchange is None:
            exchange = spec.exchange
    except KeyError:
        # 未登记品种(例如某个具体合约 rb2610):需要显式给 exchange
        if exchange is None:
            raise ValueError(
                f"品种 {symbol!r} 未在 contracts 中登记,必须显式指定 exchange"
            ) from None
        spec = FutureSpec(symbol, symbol, exchange, 1, 1)

    result = SyncResult(symbol=symbol, exchange=exchange, interval=interval, ok=False)

    raws, used_source, error = _fetch(
        source=source,
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start=start,
        end=end,
        adjust=adjust,
        verbose=verbose,
    )

    if not raws:
        result.error = error or "下载失败"
        if verbose:
            print(result)
        return result

    bars = raw_list_to_bars(raws, spec, interval)
    save_bars(bars, db=db)

    result.ok = True
    result.source = used_source
    result.bars = len(bars)
    result.first = bars[0].datetime
    result.last = bars[-1].datetime
    if verbose:
        print(result)
    return result


# ---------------------------------------------------------------------------
# 批量同步
# ---------------------------------------------------------------------------

def sync_many(
    symbols: list[str],
    interval: Interval = Interval.DAILY,
    sleep: float = 0.35,
    source: str = "sina",
    db=None,
    verbose: bool = True,
) -> list[SyncResult]:
    """批量同步,单品种失败不中断整体流程。"""
    results: list[SyncResult] = []
    total = len(symbols)
    for idx, sym in enumerate(symbols, 1):
        if verbose:
            print(f"--- [{idx}/{total}] {sym} ---")
        try:
            res = sync_symbol(
                sym, interval=interval, source=source, db=db, verbose=verbose
            )
        except Exception as exc:  # noqa: BLE001 - 保证批处理不中断
            res = SyncResult(
                symbol=sym,
                exchange=Exchange.SHFE,
                interval=interval,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            if verbose:
                print(f"[FAIL] {sym:12s} {res.error}")
        results.append(res)
        if idx < total:
            time.sleep(sleep)
    return results


def sync_all(
    interval: Interval = Interval.DAILY,
    source: str = "sina",
    db=None,
    verbose: bool = True,
) -> list[SyncResult]:
    """同步 contracts 中登记的全部品种。"""
    from .contracts import all_symbols

    return sync_many(
        all_symbols(), interval=interval, source=source, db=db, verbose=verbose
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(results: list[SyncResult], db=None) -> None:
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    total_bars = sum(r.bars for r in ok)
    print("\n" + "=" * 68)
    print(f"完成: 成功 {len(ok)} / 失败 {len(bad)},共写入 {total_bars:,} 根 K 线")
    if ok and any(r.source for r in ok):
        used: dict[str, int] = {}
        for r in ok:
            used[r.source] = used.get(r.source, 0) + 1
        print("数据源分布: " + ", ".join(f"{k}={v}" for k, v in sorted(used.items())))
    if bad:
        print("失败清单:")
        for r in bad:
            print(f"  - {r.vt_symbol:12s} {r.error}")
    print("=" * 68)

    rows = list_overview(db)
    print(f"\n本地数据库现有 {len(rows)} 组数据:")
    for row in rows:
        print("  " + str(row))


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    用法::

        python -m cta_lab.dataio rb i TA            # 同步指定品种日线
        python -m cta_lab.dataio --all              # 同步全部登记品种
        python -m cta_lab.dataio --list             # 只查看本地数据概况
        python -m cta_lab.dataio rb --source tqsdk  # 用天勤源
        python -m cta_lab.dataio --alt rb --minutes 5   # 5分钟线存列式文件
        python -m cta_lab.dataio --alt-list         # 列出非原生粒度数据
    """
    import argparse

    _force_utf8_console()

    parser = argparse.ArgumentParser(
        prog="python -m cta_lab.dataio",
        description="下载国内期货行情并写入 vnpy 数据库",
    )
    parser.add_argument("symbols", nargs="*", help="品种代码,如 rb i TA au")
    parser.add_argument("--all", action="store_true", help="同步全部登记品种")
    parser.add_argument("--list", action="store_true", help="仅列出本地数据概况")
    parser.add_argument(
        "--alt",
        metavar="SYMBOL",
        help="下载非原生粒度(5m/15m/30m)到列式文件,需配合 --minutes",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=5,
        help="非原生粒度分钟数,配合 --alt 使用(5/15/30,默认 5)",
    )
    parser.add_argument(
        "--alt-list", action="store_true", help="列出已保存的非原生粒度数据"
    )
    parser.add_argument(
        "--no-pragma", action="store_true", help="关闭 SQLite PRAGMA 调优"
    )
    parser.add_argument(
        "--interval",
        default="d",
        choices=["d", "1m", "1h"],
        help="K 线周期（默认 d）",
    )
    parser.add_argument("--db", default="futures_lab.db", help="数据库文件名")
    parser.add_argument("--duckdb", action="store_true", help="使用 DuckDB 存储")
    parser.add_argument("--adjust", type=int, default=1, help="复权标志 0/1")
    parser.add_argument(
        "--source",
        default="sina",
        choices=list(SOURCES),
        help="数据源: sina=新浪(默认,日线最稳) / em=东方财富(有分钟线) "
             "/ tqsdk=天勤 / auto=依次降级",
    )
    args = parser.parse_args(argv)

    interval = {
        "d": Interval.DAILY,
        "1m": Interval.MINUTE,
        "1h": Interval.HOUR,
    }[args.interval]

    db = init_database(
        db_type="duckdb" if args.duckdb else "sqlite",
        db_name=args.db,
        tune_pragmas=not args.no_pragma,
    )

    # ---- 非原生粒度分析通道（不进 vnpy bar 表）----
    if args.alt_list:
        from .altstore import list_alt

        rows = list_alt()
        if not rows:
            print("尚无非原生粒度数据。可用 --alt rb --minutes 5 下载。")
            return 0
        print(f"非原生粒度数据共 {len(rows)} 组:")
        for r in rows:
            print(
                f"  {r['symbol']}.{r['exchange']:6s} {r['minutes']:>2d}m  "
                f"{r['format']:8s} {r['size_kb']:8.1f} KB"
            )
        return 0

    if args.alt:
        from .altstore import ALT_GRANULARITIES, save_alt_bars
        from .downloader import fetch_extra_interval

        if args.minutes not in ALT_GRANULARITIES:
            print(
                f"错误: --minutes 只支持 {sorted(ALT_GRANULARITIES)}。\n"
                "  1m/1h/d 属于 vnpy 原生周期,请用 --interval 走 bar 表。"
            )
            return 1

        spec = get_spec(args.alt)
        print(
            f"下载 {args.alt.upper()}.{spec.exchange.value} "
            f"{args.minutes}m 到列式文件（不入 bar 表）..."
        )
        try:
            raws = fetch_extra_interval(
                args.alt, spec.exchange, args.minutes
            )
        except Exception as exc:  # noqa: BLE001
            print(f"下载失败: {type(exc).__name__}: {exc}")
            return 2

        if not raws:
            print("下载结果为空")
            return 2

        save_alt_bars(raws, args.alt, spec.exchange, args.minutes)
        return 0

    if args.list:
        rows = list_overview(db)
        if not rows:
            print("本地数据库为空。可用 python -m cta_lab.dataio rb 先同步数据。")
            return 0
        print(f"本地数据库共 {len(rows)} 组数据:")
        for row in rows:
            print("  " + str(row))
        return 0

    if args.all:
        results = sync_all(interval=interval, source=args.source, db=db)
    elif args.symbols:
        results = sync_many(
            args.symbols, interval=interval, source=args.source, db=db
        )
    else:
        parser.print_help()
        return 1

    _print_summary(results, db)
    return 0 if all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
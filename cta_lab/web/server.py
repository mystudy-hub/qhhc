"""TradingView 风格 Web 界面 —— 本地 HTTP 服务。

设计
----
    浏览器 (lightweight-charts)  <--HTTP/JSON-->  FastAPI  <--> SQLite (vnpy)

只读为主：唯一的写操作是「跑一次回测」（不落库）。
所有数据来自本地 SQLite，无需联网。

启动::

    python -m cta_lab.web.server
    # 或
    python -m cta_lab.web.server --port 8765 --no-browser
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bootstrap import db_path, ensure_trader_dir
from ..contracts import EXCHANGE_CN_NAME, FUTURES, get_spec
from ..store import init_database, list_overview

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="CTA Lab Chart", docs_url="/api/docs", redoc_url=None)

# 数据库对象在首次请求时初始化（避免 import 时就打开文件）
_db = None


def get_db():
    global _db
    if _db is None:
        ensure_trader_dir()
        _db = init_database(db_name="futures_lab.db")
    return _db


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# API: 品种列表
# ---------------------------------------------------------------------------

@app.get("/api/symbols")
def api_symbols() -> JSONResponse:
    """返回本地已有数据的品种清单。"""
    db = get_db()
    rows = list_overview(db)

    items = []
    for row in rows:
        try:
            spec = get_spec(row.symbol)
        except KeyError:
            spec = None
        items.append({
            "symbol": row.symbol,
            "exchange": row.exchange.value,
            "vt_symbol": row.vt_symbol,
            "name": spec.name if spec else row.symbol,
            "exchange_cn": EXCHANGE_CN_NAME.get(row.exchange, row.exchange.value),
            "interval": row.interval.value,
            "count": row.count,
            "start": row.start.strftime("%Y-%m-%d"),
            "end": row.end.strftime("%Y-%m-%d"),
            "size": spec.size if spec else None,
            "pricetick": spec.pricetick if spec else None,
        })

    items.sort(key=lambda x: (x["exchange"], x["symbol"]))
    return JSONResponse({"symbols": items, "total": len(items)})


# ---------------------------------------------------------------------------
# API: K 线
# ---------------------------------------------------------------------------

@app.get("/api/bars")
def api_bars(
    vt_symbol: str = Query(..., description="如 rb.SHFE"),
    interval: str = Query("d", description="d / 1m / 1h"),
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
) -> JSONResponse:
    """返回单品种 K 线（含持仓量）。

    时间格式：
        日线   -> 'YYYY-MM-DD'（lightweight-charts 原生支持）
        分钟线 -> UNIX 秒（带北京时间偏移），由前端按 UTC 显示
    """
    from datetime import datetime

    from vnpy.trader.constant import Exchange, Interval

    if "." not in vt_symbol:
        raise HTTPException(400, "vt_symbol 需形如 rb.SHFE")
    symbol, exchange_str = vt_symbol.split(".", 1)
    try:
        exchange = Exchange(exchange_str)
    except ValueError:
        raise HTTPException(400, f"未知交易所 {exchange_str}") from None

    interval_map = {
        "d": Interval.DAILY,
        "1m": Interval.MINUTE,
        "1h": Interval.HOUR,
    }
    if interval not in interval_map:
        raise HTTPException(400, f"不支持的周期 {interval}")
    iv = interval_map[interval]

    def parse(text: str | None, default: datetime) -> datetime:
        if not text:
            return default
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"日期格式错误: {text}") from None

    start_dt = parse(start, datetime(1990, 1, 1))
    end_dt = parse(end, datetime(2100, 1, 1))

    db = get_db()
    bars = db.load_bar_data(symbol, exchange, iv, start_dt, end_dt)
    if not bars:
        raise HTTPException(404, f"本地无 {vt_symbol} {interval} 数据")

    spec = FUTURES.get(symbol) or next(
        (s for k, s in FUTURES.items() if k.lower() == symbol.lower()), None
    )

    out = []
    for b in bars:
        if iv == Interval.DAILY:
            t: object = b.datetime.strftime("%Y-%m-%d")
        else:
            # 分钟线用 UNIX 秒；vnpy 内部是上海时区 aware
            t = int(b.datetime.timestamp())
        out.append({
            "time": t,
            "open": b.open_price,
            "high": b.high_price,
            "low": b.low_price,
            "close": b.close_price,
            "volume": b.volume,
            "oi": b.open_interest,
        })

    return JSONResponse({
        "vt_symbol": vt_symbol,
        "symbol": symbol,
        "exchange": exchange.value,
        "name": spec.name if spec else symbol,
        "interval": interval,
        "count": len(out),
        "bars": out,
    })


# ---------------------------------------------------------------------------
# API: 多品种对比（归一化）
# ---------------------------------------------------------------------------

@app.get("/api/compare")
def api_compare(
    symbols: str = Query(..., description="逗号分隔，如 rb.SHFE,i.DCE"),
    interval: str = Query("d"),
) -> JSONResponse:
    """返回归一化到 100 的收盘价序列，用于叠加对比。"""
    from datetime import datetime

    from vnpy.trader.constant import Exchange, Interval

    interval_map = {"d": Interval.DAILY, "1m": Interval.MINUTE, "1h": Interval.HOUR}
    iv = interval_map.get(interval)
    if iv is None:
        raise HTTPException(400, f"不支持的周期 {interval}")

    db = get_db()
    series = []

    for item in [s.strip() for s in symbols.split(",") if s.strip()]:
        if "." not in item:
            continue
        symbol, exchange_str = item.split(".", 1)
        try:
            exchange = Exchange(exchange_str)
        except ValueError:
            continue

        bars = db.load_bar_data(
            symbol, exchange, iv, datetime(1990, 1, 1), datetime(2100, 1, 1)
        )
        if not bars:
            continue

        base = bars[0].close_price or 1.0
        spec = FUTURES.get(symbol) or next(
            (s for k, s in FUTURES.items() if k.lower() == symbol.lower()), None
        )
        points = []
        for b in bars:
            t = (
                b.datetime.strftime("%Y-%m-%d")
                if iv == Interval.DAILY
                else int(b.datetime.timestamp())
            )
            points.append({"time": t, "value": round(b.close_price / base * 100, 4)})

        series.append({
            "vt_symbol": item,
            "name": spec.name if spec else symbol,
            "base": base,
            "count": len(points),
            "points": points,
        })

    if not series:
        raise HTTPException(404, "没有可比对的数据")
    return JSONResponse({"interval": interval, "series": series})



# ---------------------------------------------------------------------------
# API: 维科夫结构识别
# ---------------------------------------------------------------------------

@app.get("/api/wyckoff")
def api_wyckoff(
    vt_symbol: str = Query(..., description="如 rb.SHFE"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    pure_wyckoff: bool = Query(False, description="是否仅交易纯维科夫信号(TEST/LPS)"),
    mode: str = Query("macro", description="macro(宏观日线跨度) 或 micro(微观短线)"),
    bridge_index: bool = Query(True, description="是否启用无跳空指数识别与主连真实撮合桥接"),
) -> JSONResponse:
    """返回维科夫结构事件、交易区间、右侧信号与逐 K 回测进出场（支持宏观/微观模式及指数桥接）。"""
    from datetime import datetime

    from ..wyckoff import TradeConfig, run_backtest
    from ..wyckoff.engine import WyckoffConfig

    def parse(text, fallback):
        if not text:
            return fallback
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return fallback

    tcfg = TradeConfig(
        trade_signals=("TEST", "LPS") if pure_wyckoff else ("TEST", "LPS", "FTI")
    )
    ecfg = (
        WyckoffConfig.create_micro()
        if mode == "micro"
        else WyckoffConfig.create_macro()
    )

    try:
        result = run_backtest(
            vt_symbol,
            start=parse(start, None),
            end=parse(end, None),
            engine_config=ecfg,
            trade_config=tcfg,
            bridge_index=bridge_index,
            db=get_db(),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from None

    df = result.df

    def pack(res, side: str) -> dict:
        return {
            "side": side,
            "events": [
                {
                    "time": str(e.time)[:10],
                    "index": e.index,
                    "kind": e.kind,
                    "name": e.name,
                    "price": round(float(e.price), 4),
                    "direction": e.direction,
                    "note": e.note,
                }
                for e in res.events
            ],
            "ranges": [
                {
                    "id": r.id,
                    "direction": r.direction,
                    "start": str(r.start_time)[:10],
                    "end": str(df.index[r.end_index])[:10] if r.end_index is not None else str(df.index[-1])[:10],
                    "phase": r.phase,
                    "ice": round(float(r.ice), 4),
                    "creek": round(float(r.creek), 4),
                    "end_reason": r.end_reason,
                    "event_names": [e.name for e in r.events],
                }
                for r in res.ranges
            ],
        }

    acc_pack = pack(result.acc, "accumulation")
    acc_pack["signals"] = [
        {"time": str(e.time)[:10], "kind": e.kind,
         "direction": e.direction, "price": round(float(e.price), 4)}
        for e in result.acc.events if e.kind in ("TEST", "LPS", "FTI")
    ]

    sim = result.sim_both
    sim_trades = [
        {
            "entry_index": t.entry_index,
            "entry_time": str(t.entry_time)[:10],
            "entry_price": round(t.entry_price, 4),
            "stop": round(t.stop, 4),
            "target": round(t.target, 4),
            "exit_index": t.exit_index,
            "exit_time": str(t.exit_time)[:10] if t.exit_time else "",
            "exit_price": round(t.exit_price, 4),
            "direction": t.direction,
            "exit_reason": t.exit_reason,
            "pnl": round(t.net_pnl, 2),
            "r": round(t.r_multiple, 3),
            "bars_held": t.bars_held,
            "signal": t.signal_kind,
        }
        for t in sim.trades
    ]

    return JSONResponse({
        "vt_symbol": vt_symbol,
        "bars": len(df),
        "pure_wyckoff": pure_wyckoff,
        "mode": mode,
        "is_bridged": getattr(result, "is_bridged", False),
        "accumulation": acc_pack,
        "distribution": pack(result.dist, "distribution"),
        "backtest": {
            "trades": sim_trades,
            "stats": sim.statistics(),
        },
    })


# ---------------------------------------------------------------------------
# API: 回测
# ---------------------------------------------------------------------------

@app.get("/api/strategies")
def api_strategies() -> JSONResponse:
    from ..strategies import PARAM_GRIDS, STRATEGY_REGISTRY

    items = []
    for name, (cls, default) in STRATEGY_REGISTRY.items():
        doc = (cls.__doc__ or "").strip().splitlines()
        items.append({
            "name": name,
            "class": cls.__name__,
            "doc": doc[0] if doc else "",
            "default": default,
            "grid": PARAM_GRIDS.get(name, {}),
        })
    return JSONResponse({"strategies": items})


@app.post("/api/backtest")
def api_backtest(payload: dict) -> JSONResponse:
    """执行一次回测，返回成交点 + 绩效 + 权益曲线。

    请求体::

        {
          "vt_symbol": "rb.SHFE",
          "strategy": "Donchian",
          "params": {"entry_window": 20, "exit_window": 10, "fixed_size": 5},
          "start": "2018-01-01",
          "end": "2026-10-01",
          "capital": 1000000
        }
    """
    from datetime import datetime

    from ..ai import diagnose, robustness_score
    from ..runner import BacktestConfig, run_backtest
    from ..strategies import get_strategy

    vt_symbol = str(payload.get("vt_symbol", "")).strip()
    if "." not in vt_symbol:
        raise HTTPException(400, "vt_symbol 需形如 rb.SHFE")

    strategy_name = str(payload.get("strategy", "Donchian"))
    try:
        strategy_cls, defaults = get_strategy(strategy_name)
    except (KeyError, ImportError) as exc:
        raise HTTPException(400, f"未知策略 {strategy_name}: {exc}") from None

    params = {**defaults, **(payload.get("params") or {})}

    def parse(text, fallback):
        if not text:
            return fallback
        try:
            return datetime.strptime(str(text)[:10], "%Y-%m-%d")
        except ValueError:
            return fallback

    config = BacktestConfig(
        vt_symbol=vt_symbol,
        start=parse(payload.get("start"), datetime(2018, 1, 1)),
        end=parse(payload.get("end"), datetime(2026, 10, 1)),
        capital=int(payload.get("capital") or 1_000_000),
        db_name="futures_lab.db",
    )

    try:
        result = run_backtest(
            strategy_cls, params, config, db=get_db(),
            quiet=True, export=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"回测失败: {type(exc).__name__}: {exc}") from None

    # 成交点 -> 图表标记
    interval = config.interval
    markers = []
    for t in result.trades:
        is_long = t.direction.value == "多"
        is_open = t.offset.value == "开"
        # 开仓：多头向上箭头 / 空头向下箭头；平仓：反向
        if is_open:
            position = "belowBar" if is_long else "aboveBar"
            shape = "arrowUp" if is_long else "arrowDown"
            text = "买" if is_long else "卖"
        else:
            position = "aboveBar" if is_long else "belowBar"
            shape = "arrowDown" if is_long else "arrowUp"
            text = "平"

        time_value = (
            t.datetime.strftime("%Y-%m-%d")
            if interval.value == "d"
            else int(t.datetime.timestamp())
        )
        markers.append({
            "time": time_value,
            "position": position,
            "shape": shape,
            "color": "#26a69a" if is_long else "#ef5350",
            "text": f"{text}{int(t.volume)}",
            "price": t.price,
            "offset": t.offset.value,
            "direction": t.direction.value,
        })

    # 权益曲线
    equity = []
    if result.daily_df is not None and len(result.daily_df) > 0:
        for idx, value in result.daily_df["balance"].items():
            equity.append({
                "time": idx.strftime("%Y-%m-%d"),
                "value": round(float(value), 2),
            })

    stats = {k: _jsonable(v) for k, v in result.statistics.items()}
    trade_stats = {k: _jsonable(v) for k, v in result.trade_stats().items()}
    score = robustness_score(result.statistics, trade_stats)
    notes = diagnose(result.statistics, trade_stats, config)

    return JSONResponse({
        "vt_symbol": vt_symbol,
        "strategy": strategy_name,
        "params": params,
        "start": config.start.strftime("%Y-%m-%d"),
        "end": config.end.strftime("%Y-%m-%d"),
        "stats": stats,
        "trade_stats": trade_stats,
        "score": score,
        "notes": notes,
        "markers": markers,
        "equity": equity,
        "trade_count": len(result.trades),
    })


def _jsonable(value):
    """numpy/pandas 标量 -> 原生 JSON 类型。"""
    if value is None or isinstance(value, (str, bool)):
        return value
    item = getattr(value, "item", None)
    if callable(item) and getattr(value, "shape", None) == ():
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    return str(value)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cta_lab.web.server",
        description="TradingView 风格本地图表服务",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    # 预初始化，尽早暴露数据库问题
    db = get_db()
    rows = list_overview(db)

    url = f"http://{args.host}:{args.port}"
    print("=" * 62)
    print("  CTA Lab Chart  ——  TradingView 风格本地图表")
    print("=" * 62)
    print(f"  数据库  : {db_path()}")
    print(f"  数据组  : {len(rows)}")
    print(f"  地址    : {url}")
    print(f"  API文档 : {url}/api/docs")
    print("  按 Ctrl+C 停止")
    print("=" * 62)

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

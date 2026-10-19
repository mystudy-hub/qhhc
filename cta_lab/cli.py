"""命令行入口：回测 / 寻优 / 多品种扫描。

用法::

    # 单品种回测（默认双均线）
    python -m cta_lab.cli backtest rb

    # 指定策略与参数
    python -m cta_lab.cli backtest rb --strategy Donchian --param entry_window=30

    # 参数网格寻优
    python -m cta_lab.cli optimize rb --strategy DoubleMa --metric sharpe_ratio

    # 多品种批量扫描，找出最适配的品种
    python -m cta_lab.cli scan --strategy Donchian --symbols rb i TA au SA cu m

    # 列出可用策略
    python -m cta_lab.cli list-strategies
"""

from __future__ import annotations

import sys
from datetime import datetime

from vnpy.trader.constant import Interval

from .ai import diagnose, robustness_score
from .runner import BacktestConfig, run_backtest, run_grid
from .store import init_database, list_overview
from .strategies import PARAM_GRIDS, STRATEGY_REGISTRY, get_strategy


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _parse_params(pairs: list[str] | None) -> dict:
    """把 ['fast_window=5', 'slow_window=30'] 解析成 dict。"""
    out: dict = {}
    for item in pairs or []:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            out[key] = int(value)
        except ValueError:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value
    return out


def _build_config(args, vt_symbol: str) -> BacktestConfig:
    interval = {
        "d": Interval.DAILY,
        "1m": Interval.MINUTE,
        "1h": Interval.HOUR,
    }[args.interval]

    return BacktestConfig(
        vt_symbol=vt_symbol,
        start=datetime.strptime(args.start, "%Y-%m-%d"),
        end=datetime.strptime(args.end, "%Y-%m-%d"),
        interval=interval,
        capital=args.capital,
        db_name=args.db,
        rate=args.rate,
        slippage=args.slippage,
    )


def _full_symbol(symbol: str) -> str:
    """'rb' -> 'rb.SHFE'；已带交易所则原样返回。"""
    if "." in symbol:
        return symbol
    from .contracts import get_spec

    return f"{symbol}.{get_spec(symbol).exchange.value}"


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_backtest(args) -> int:
    vt_symbol = _full_symbol(args.symbol)
    config = _build_config(args, vt_symbol)
    strategy_cls, default_setting = get_strategy(args.strategy)
    setting = {**default_setting, **_parse_params(args.param)}

    print(f"策略: {args.strategy}  参数: {setting}")
    print(f"标的: {vt_symbol}  区间: {args.start} ~ {args.end}")

    result = run_backtest(strategy_cls, setting, config, export=True)
    print()
    print(result.summary())

    ts = result.trade_stats()
    score = robustness_score(result.statistics, ts)
    print()
    print(f"稳健性评分: {score['total']} / 100  等级: {score['grade']}")
    print(
        f"  收益={score['return_score']}  风险={score['risk_score']}  "
        f"夏普={score['sharpe_score']}  质量={score['quality_score']}"
    )

    print("\n策略诊断:")
    for note in diagnose(result.statistics, ts, config):
        print(f"  - {note}")

    if args.llm:
        from .ai import llm_review

        print(f"\n调用 LLM ({args.llm_model}) 深度解读 ...")
        try:
            review = llm_review(
                result,
                api_key=args.llm,
                base_url=args.llm_base,
                model=args.llm_model,
            )
            print("\n" + "=" * 62)
            print(review)
            print("=" * 62)
        except Exception as exc:  # noqa: BLE001
            print(f"LLM 调用失败: {type(exc).__name__}: {exc}")

    return 0


def cmd_optimize(args) -> int:
    vt_symbol = _full_symbol(args.symbol)
    config = _build_config(args, vt_symbol)
    strategy_cls, default_setting = get_strategy(args.strategy)

    if args.grid:
        # 自定义网格: --grid fast_window=5,10,15 --grid slow_window=20,40
        grid: dict[str, list] = {}
        for item in args.grid:
            key, values = item.split("=", 1)
            grid[key.strip()] = [
                int(v) if v.strip().lstrip("-").isdigit() else float(v)
                for v in values.split(",")
            ]
    else:
        grid = PARAM_GRIDS.get(args.strategy, {})
        if not grid:
            print(f"策略 {args.strategy} 没有预置网格，请用 --grid 指定")
            return 1

    print(f"标的: {vt_symbol}  网格: {grid}")
    rows = run_grid(strategy_cls, grid, config, metric=args.metric, quiet=True)

    print("\n" + "=" * 78)
    print(f"寻优结果（按 {args.metric} 降序），前 15 名:")
    print("=" * 78)
    print(f"{'参数':<46s} {'年化%':>9s} {'回撤%':>9s} {'夏普':>8s}")
    print("-" * 78)
    for setting, stats in rows[:15]:
        param_str = " ".join(f"{k}={v}" for k, v in setting.items())
        print(
            f"{param_str:<46s} "
            f"{stats.get('annual_return', 0):>9.2f} "
            f"{stats.get('max_ddpercent', 0):>9.2f} "
            f"{stats.get('sharpe_ratio', 0):>8.3f}"
        )

    if rows:
        best_setting, best_stats = rows[0]
        print("\n最佳参数:", best_setting)
        print(f"  年化 {best_stats.get('annual_return', 0):.2f}%  "
              f"回撤 {best_stats.get('max_ddpercent', 0):.2f}%  "
              f"夏普 {best_stats.get('sharpe_ratio', 0):.3f}")
        print("\n注意: 网格寻优会过拟合历史。请用样本外区间或滚动窗口复核。")

    return 0


def cmd_scan(args) -> int:
    """多品种扫描：同一策略在不同品种上的表现对比。"""
    strategy_cls, default_setting = get_strategy(args.strategy)
    setting = {**default_setting, **_parse_params(args.param)}

    symbols = args.symbols
    print(f"策略 {args.strategy} {setting} 扫描 {len(symbols)} 个品种\n")
    print(f"{'品种':<12s} {'年化%':>9s} {'回撤%':>9s} {'夏普':>8s} "
          f"{'回合':>6s} {'胜率%':>7s} {'评分':>7s}")
    print("-" * 72)

    results = []
    for sym in symbols:
        vt_symbol = _full_symbol(sym)
        config = _build_config(args, vt_symbol)
        try:
            res = run_backtest(
                strategy_cls, setting, config, quiet=True, export=False
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{sym:<12s} 失败: {type(exc).__name__}: {str(exc)[:40]}")
            continue

        ts = res.trade_stats()
        score = robustness_score(res.statistics, ts)
        results.append((sym, res, ts, score))

        print(
            f"{sym:<12s} "
            f"{res.statistics.get('annual_return', 0):>9.2f} "
            f"{res.statistics.get('max_ddpercent', 0):>9.2f} "
            f"{res.statistics.get('sharpe_ratio', 0):>8.3f} "
            f"{ts.get('round_trips', 0):>6d} "
            f"{ts.get('win_rate', 0):>7.2f} "
            f"{score['total']:>7.2f}"
        )

    if results:
        results.sort(key=lambda r: r[3]["total"], reverse=True)
        print("\n按稳健性评分排序:")
        for sym, res, ts, score in results[:5]:
            print(f"  {sym:<8s} {score['total']:.2f}  ({score['grade']})")

    return 0


def cmd_wyckoff(args) -> int:
    """维科夫结构识别 + 交易回测。"""
    from cta_lab.wyckoff import (
        TradeConfig,
        WyckoffConfig,
        run_backtest as wyckoff_backtest,
        scan as wyckoff_scan,
    )

    if getattr(args, "micro", False):
        ecfg = WyckoffConfig.create_micro(
            lookback=args.lookback if args.lookback != 60 else 20,
            vol_z_thresh=args.vol_z if args.vol_z != 2.0 else 1.5,
            min_cause_bars=args.min_cause if args.min_cause != 35 else 15,
            max_spring_bars=args.spring_bars,
        )
    else:
        ecfg = WyckoffConfig.create_macro(
            lookback=args.lookback,
            vol_z_thresh=args.vol_z,
            min_cause_bars=args.min_cause,
            max_spring_bars=args.spring_bars,
        )
    signals = (
        ("SPRING3", "TEST", "LPS")
        if args.pure_wyckoff
        else ("SPRING3", "TEST", "LPS", "JAC", "SOS", "FTI")
    )
    tcfg = TradeConfig(
        risk_per_trade=args.risk,
        target_r=args.target_r,
        max_hold_bars=args.max_hold,
        allow_short=not args.no_short,
        trade_signals=signals,
    )

    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
    bridge_index = not getattr(args, "no_bridge", False)

    if args.scan:
        symbols = [_full_symbol(x) for x in args.scan]
        df = wyckoff_scan(
            symbols, start=start, end=end,
            capital=args.capital,
        )
        if not df.empty:
            print()
            print("按夏普排序 前 20:")
            print(df.head(20).to_string(index=False))
            print()
            print(f"  有交易品种 {int((df['trades']>0).sum())}/{len(df)}，"
                  f"总成交 {int(df['trades'].sum())} 笔，"
                  f"中位胜率 {df['win_rate'].median():.2f}%，"
                  f"中位 avgR {df['avg_r'].median():.3f}")
        return 0

    vt = _full_symbol(args.symbol)
    use_pro = not getattr(args, "legacy", False)
    use_weekly_filter = getattr(args, "weekly_filter", False)
    result = wyckoff_backtest(
        vt, start=start, end=end,
        capital=args.capital,
        engine_config=ecfg,
        trade_config=tcfg,
        include_distribution=not args.no_dist,
        bridge_index=bridge_index,
        use_pro=use_pro,
        use_weekly_filter=use_weekly_filter,
    )
    print()
    print(result.summary())

    if args.events:
        ev = result.events_df()
        if len(ev):
            print()
            print("=== 结构事件 ===")
            print(ev.to_string(index=False))

    if args.trades:
        tr = result.trades_df()
        if len(tr):
            print()
            print("=== 成交明细 ===")
            print(tr.to_string(index=False))

    return 0


def cmd_list_strategies(args) -> int:
    print("本项目的自包含策略:")
    for name, (cls, setting) in STRATEGY_REGISTRY.items():
        print(f"  {name:<18s} {cls.__doc__.splitlines()[0] if cls.__doc__ else ''}")
        print(f"  {'':18s} 默认参数: {setting}")
        grid = PARAM_GRIDS.get(name)
        if grid:
            print(f"  {'':18s} 寻优网格: {grid}")
    print("\n也可直接使用 vnpy 官方策略（--strategy 名称）:")
    print("  doublema, bollchannel, atrrsi, turtle, dualtrust, kingkeltner")
    return 0


def cmd_list_data(args) -> int:
    db = init_database(db_name=args.db)
    rows = list_overview(db)
    if not rows:
        print("本地数据库为空。先执行: python -m cta_lab.dataio rb i TA")
        return 0
    print(f"本地数据库共 {len(rows)} 组数据:")
    for row in rows:
        print("  " + str(row))
    return 0


def cmd_build_index(args) -> int:
    """批量生成无跳空加权连续指数并写入 SQLite。"""
    from cta_lab.contracts import all_symbols, get_spec
    from cta_lab.store import init_database
    from cta_lab.wyckoff.runner import load_df, build_synthetic_index
    from vnpy.trader.constant import Interval
    from vnpy.trader.object import BarData

    db = init_database(db_name=args.db)
    if args.all:
        symbols = all_symbols()
    elif args.symbols:
        symbols = args.symbols
    else:
        print("请指定 --symbols rb i cu 或 --all")
        return 1

    success_cnt = 0
    total_bars = 0
    for sym in symbols:
        try:
            spec = get_spec(sym)
            vt = f"{spec.symbol}.{spec.exchange.value}"
            df_main = load_df(vt, db=db)
            if len(df_main) < 10:
                continue
            df_idx = build_synthetic_index(df_main)
            bars = []
            index_code = f"{spec.symbol}_index"
            for dt, row in df_idx.iterrows():
                pydt = dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt
                bars.append(BarData(
                    symbol=index_code,
                    exchange=spec.exchange,
                    datetime=pydt,
                    interval=Interval.DAILY,
                    open_price=round(float(row["open"]), 2),
                    high_price=round(float(row["high"]), 2),
                    low_price=round(float(row["low"]), 2),
                    close_price=round(float(row["close"]), 2),
                    volume=float(row["volume"]),
                    open_interest=float(row.get("open_interest", 0.0) or 0.0),
                    gateway_name="CTA_LAB",
                ))
            db.save_bar_data(bars)
            success_cnt += 1
            total_bars += len(bars)
            print(f"  [OK] {spec.name}指数 ({index_code}.{spec.exchange.value}) {len(bars)} 根日线已写入数据库")
        except Exception:
            pass

    print(f"\n完成！成功构建并入库 {success_cnt} 个品种的加权平滑指数，累计 {total_bars} 根 K 线。")
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cta_lab.cli",
        description="国内期货 CTA 回测 / 寻优 / 扫描工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--strategy", default="DoubleMa", help="策略名")
        p.add_argument("--start", default="2018-01-01", help="回测起始日")
        p.add_argument("--end", default="2026-10-01", help="回测结束日")
        p.add_argument(
            "--interval", default="d", choices=["d", "1m", "1h"], help="K 线周期"
        )
        p.add_argument("--capital", type=int, default=1_000_000, help="初始资金")
        p.add_argument("--db", default="futures_lab.db", help="数据库文件名")
        p.add_argument("--rate", type=float, default=None, help="手续费率(覆盖默认)")
        p.add_argument("--slippage", type=float, default=None, help="滑点(覆盖默认)")
        p.add_argument(
            "--param", action="append", help="策略参数，如 --param fast_window=5"
        )

    p_bt = sub.add_parser("backtest", help="单品种回测")
    p_bt.add_argument("symbol", help="品种代码，如 rb 或 rb.SHFE")
    add_common(p_bt)
    p_bt.add_argument("--llm", default=None, help="LLM api_key，启用深度解读")
    p_bt.add_argument("--llm-base", default="https://api.openai.com/v1")
    p_bt.add_argument("--llm-model", default="gpt-4o-mini")
    p_bt.set_defaults(func=cmd_backtest)

    p_opt = sub.add_parser("optimize", help="参数网格寻优")
    p_opt.add_argument("symbol", help="品种代码")
    add_common(p_opt)
    p_opt.add_argument("--metric", default="sharpe_ratio", help="优化目标指标")
    p_opt.add_argument(
        "--grid", action="append", help="自定义网格，如 --grid fast_window=5,10,15"
    )
    p_opt.set_defaults(func=cmd_optimize)

    p_scan = sub.add_parser("scan", help="多品种扫描")
    p_scan.add_argument("--symbols", nargs="+", required=True, help="品种列表")
    add_common(p_scan)
    p_scan.set_defaults(func=cmd_scan)

    p_wy = sub.add_parser("wyckoff", help="维科夫结构识别与回测")
    p_wy.add_argument("symbol", nargs="?", default="rb", help="品种，如 rb 或 rb.SHFE")
    p_wy.add_argument("--scan", nargs="+", metavar="SYMBOL", help="多品种扫描")
    p_wy.add_argument("--start", default=None, help="起始日 YYYY-MM-DD")
    p_wy.add_argument("--end", default=None, help="结束日 YYYY-MM-DD")
    p_wy.add_argument("--capital", type=int, default=1_000_000)
    p_wy.add_argument("--legacy", action="store_true", help="使用旧版 V2.1 引擎（默认使用 Pro 专业版全套五阶段引擎）")
    p_wy.add_argument("--weekly-filter", action="store_true", help="启用 MTF 周线宏观过滤器（顺周线大势屏蔽逆势信号，追求极致胜率）")
    p_wy.add_argument("--macro", action="store_true", default=True, help="宏观日线维科夫跨度（默认，年均1~2个大区间）")
    p_wy.add_argument("--micro", action="store_true", help="微观短线维科夫跨度（更密集识别局部小震荡）")
    p_wy.add_argument("--no-bridge", action="store_true", help="禁用指数桥接（直接在主连上识别形态）")
    p_wy.add_argument("--lookback", type=int, default=60, help="新高低窗口（默认宏观60根）")
    p_wy.add_argument("--vol-z", type=float, default=2.0, help="恐慌簇量能阈值（默认宏观2.0）")
    p_wy.add_argument("--min-cause", type=int, default=35, help="Phase B 最小横盘根数（默认宏观35根）")
    p_wy.add_argument("--spring-bars", type=int, default=8, help="弹簧收复窗口")
    p_wy.add_argument("--risk", type=float, default=0.01, help="单笔风险比例")
    p_wy.add_argument("--target-r", type=float, default=1.0, help="目标 R 倍数")
    p_wy.add_argument("--max-hold", type=int, default=60, help="时间止损(根)")
    p_wy.add_argument("--no-short", action="store_true", help="只做多")
    p_wy.add_argument("--no-dist", action="store_true", help="不识别派发形态")
    p_wy.add_argument("--pure-wyckoff", action="store_true", help="纯维科夫模式：只交易 TEST 与 LPS 核心点（过滤 FTI 破冰）")
    p_wy.add_argument("--events", action="store_true", help="打印结构事件")
    p_wy.add_argument("--trades", action="store_true", help="打印成交明细")
    p_wy.set_defaults(func=cmd_wyckoff)

    p_ls = sub.add_parser("list-strategies", help="列出可用策略")
    p_ls.set_defaults(func=cmd_list_strategies)

    p_ld = sub.add_parser("list-data", help="列出本地数据")
    p_ld.add_argument("--db", default="futures_lab.db")
    p_ld.set_defaults(func=cmd_list_data)

    p_bi = sub.add_parser("build-index", help="根据主连数据一键生成无跳空加权连续指数并存库")
    p_bi.add_argument("--symbols", nargs="+", help="指定品种列表，如 rb i cu")
    p_bi.add_argument("--all", action="store_true", help="为本地全部品种生成加权连续指数")
    p_bi.add_argument("--db", default="futures_lab.db")
    p_bi.set_defaults(func=cmd_build_index)

    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
"""环境自检：确认 vnpy 与数据源都可用。

执行::

    python check_env.py
"""

from __future__ import annotations

import sys
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# 必须在导入任何 vnpy 模块之前执行：建立 <项目根>/.vntrader，
# 使 vnpy 的 TRADER_DIR == 项目根目录。
# ---------------------------------------------------------------------------
from cta_lab.bootstrap import TRADER_DIR, db_path, ensure_trader_dir

ensure_trader_dir()


def title(text: str) -> None:
    print("\n" + "=" * 66)
    print(f"  {text}")
    print("=" * 66)


def ok(text: str) -> None:
    print(f"  [ OK ] {text}")


def fail(text: str) -> None:
    print(f"  [FAIL] {text}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    problems = 0

    title("1. Python 解释器")
    print(f"  版本: {sys.version.split()[0]}")
    print(f"  路径: {sys.executable}")
    if sys.version_info >= (3, 10):
        ok("版本满足 vnpy 4.x 要求 (>=3.10)")
    else:
        fail("需要 Python 3.10 或更高版本")
        problems += 1

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        ok(f"运行在虚拟环境中 ({sys.prefix})")
    else:
        print("  [WARN] 未在虚拟环境中运行，建议先激活 .venv")

    title("2. 核心依赖")
    try:
        import vnpy
        ok(f"vnpy {vnpy.__version__}")
    except Exception as exc:  # noqa: BLE001
        fail(f"vnpy 导入失败: {exc}")
        problems += 1

    for mod_name in ("numpy", "pandas", "talib"):
        try:
            mod = __import__(mod_name)
            ok(f"{mod_name} {getattr(mod, '__version__', '?')}")
        except Exception as exc:  # noqa: BLE001
            fail(f"{mod_name} 导入失败: {exc}")
            problems += 1

    title("3. vnpy 应用模块")
    for mod_name in (
        "vnpy_ctastrategy",
        "vnpy_ctabacktester",
        "vnpy_datamanager",
        "vnpy_sqlite",
    ):
        try:
            __import__(mod_name)
            ok(mod_name)
        except Exception as exc:  # noqa: BLE001
            fail(f"{mod_name} 导入失败: {exc}")
            problems += 1

    title("4. 回测引擎")
    try:
        from vnpy_ctastrategy.backtesting import BacktestingEngine
        from vnpy_ctastrategy.base import BacktestingMode
        from vnpy_ctastrategy import CtaTemplate
        from vnpy.trader.constant import Interval
        ok(f"BacktestingEngine 可用，支持周期: {[i.value for i in Interval]}")
        print(f"  回测模式: {[m.name for m in BacktestingMode]}")
    except Exception as exc:  # noqa: BLE001
        fail(f"回测引擎导入失败: {exc}")
        problems += 1

    title("5. 时区设置")
    try:
        from vnpy.trader.database import DB_TZ
        ZoneInfo("Asia/Shanghai")
        ok(f"DB_TZ = {DB_TZ}")
        local_hint = "（vnpy 存库统一用上海时区，非本地时区会偏移 8 小时）"
        print(f"  {local_hint}")
    except Exception as exc:  # noqa: BLE001
        fail(f"时区检查失败: {exc}")
        problems += 1

    title("6. 本地数据库")
    try:
        from cta_lab.store import init_database, list_overview

        db = init_database(db_name="futures_lab.db")
        rows = list_overview(db)
        ok(f"数据库驱动: {type(db).__name__}，现有 {len(rows)} 组数据")
        print(f"       路径: {db_path()}")
        for row in rows[:10]:
            print(f"       {row}")
        if len(rows) > 10:
            print(f"       ... 其余 {len(rows) - 10} 组")
    except Exception as exc:  # noqa: BLE001
        fail(f"数据库检查失败: {exc}")
        problems += 1

    title("7. 行情数据源（东方财富）")
    try:
        from cta_lab.downloader import fetch_bars
        from vnpy.trader.constant import Exchange, Interval

        bars = fetch_bars("rb", Exchange.SHFE, Interval.DAILY)
        if bars:
            ok(
                f"螺纹钢主连日线 {len(bars)} 根 "
                f"({bars[0].date_str} -> {bars[-1].date_str})"
            )
            b = bars[-1]
            print(
                f"       最后: {b.date_str}  O={b.open} H={b.high} "
                f"L={b.low} C={b.close} V={b.volume:.0f} OI={b.open_interest:.0f}"
            )
        else:
            fail("数据源返回空数据")
            problems += 1
    except Exception as exc:  # noqa: BLE001
        fail(f"数据源连接失败: {type(exc).__name__}: {exc}")
        problems += 1

    title("8. 品种登记表")
    try:
        from cta_lab.contracts import EXCHANGE_CN_NAME, by_exchange

        total = 0
        for ex, cn in EXCHANGE_CN_NAME.items():
            n = len(by_exchange(ex))
            total += n
            print(f"  {cn:6s} {n:2d} 个品种")
        ok(f"共登记 {total} 个品种")
    except Exception as exc:  # noqa: BLE001
        fail(f"品种表检查失败: {exc}")
        problems += 1

    title("结论")
    if problems == 0:
        print("  环境就绪，可以开始使用。")
        print("\n  下一步:")
        print("    ./.venv/Scripts/python -m cta_lab.dataio rb i TA au   # 下载数据")
        print("    ./.venv/Scripts/python -m cta_lab.cli backtest rb     # 回测")
    else:
        print(f"  发现 {problems} 个问题，请先修复上面的 [FAIL] 项。")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
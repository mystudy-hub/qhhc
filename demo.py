"""一键演示：从下载数据到回测 + AI 分析的全流程。

执行::

    python demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# 必须在导入任何 vnpy 模块之前执行：建立 <项目根>/.vntrader，
# 使 vnpy 的 TRADER_DIR == 项目根目录（否则会退化成用户主目录）。
# ---------------------------------------------------------------------------
from cta_lab.bootstrap import TRADER_DIR, db_path, ensure_trader_dir

ensure_trader_dir()


def hr(text: str = "") -> None:
    if text:
        print(f"\n{'=' * 70}\n  {text}\n{'=' * 70}")
    else:
        print("-" * 70)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    from vnpy.trader.constant import Exchange

    from cta_lab.ai import diagnose, robustness_score
    from cta_lab.contracts import get_spec
    from cta_lab.dataio import sync_many
    from cta_lab.runner import BacktestConfig, run_backtest
    from cta_lab.store import init_database, list_overview
    from cta_lab.strategies import get_strategy

    # ------------------------------------------------------------------
    hr("步骤 1/4  初始化数据库")
    db = init_database(db_name="futures_lab.db")
    print(f"  驱动: {type(db).__name__}")
    print(f"  路径: {db_path()}")

    # ------------------------------------------------------------------
    hr("步骤 2/4  下载行情数据（东方财富，免 token）")
    symbols = ["rb", "i", "au"]
    for sym in symbols:
        spec = get_spec(sym)
        print(f"  将同步 {sym} ({spec.name}, {spec.exchange.value})")
    print()
    sync_many(symbols, db=db)

    hr("当前本地数据")
    for row in list_overview(db):
        print("  " + str(row))

    # ------------------------------------------------------------------
    hr("步骤 3/4  回测：唐奇安通道突破 @ 螺纹钢")
    strategy_cls, default_setting = get_strategy("Donchian")
    config = BacktestConfig(
        vt_symbol="rb.SHFE",
        start=datetime(2018, 1, 1),
        end=datetime(2026, 10, 1),
        capital=1_000_000,
    )
    print(f"  策略: {strategy_cls.__name__}")
    print(f"  参数: {default_setting}")

    result = run_backtest(
        strategy_cls, default_setting, config, db=db, export=True
    )
    print()
    print(result.summary())

    # ------------------------------------------------------------------
    hr("步骤 4/4  AI 分析（本地规则引擎，无需联网）")
    ts = result.trade_stats()
    score = robustness_score(result.statistics, ts)

    print(f"  稳健性评分: {score['total']} / 100   等级: {score['grade']}")
    print(
        f"    收益能力 {score['return_score']}  |  "
        f"风险控制 {score['risk_score']}  |  "
        f"风险调整 {score['sharpe_score']}  |  "
        f"交易质量 {score['quality_score']}"
    )

    print("\n  策略诊断:")
    for note in diagnose(result.statistics, ts, config):
        print(f"    - {note}")

    print("\n  要启用 LLM 深度解读（可选）:")
    print("    python -m cta_lab.cli backtest rb --strategy Donchian \\")
    print("        --llm $YOUR_API_KEY --llm-base https://api.deepseek.com/v1 \\")
    print("        --llm-model deepseek-chat")

    hr("完成")
    print("  结果文件放在 ./results/ 目录")
    print("  （绩效 JSON / 逐日 CSV / 交易明细 CSV / 权益曲线 SVG）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
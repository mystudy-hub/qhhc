"""CTA Lab —— 基于 vnpy 4.x 的本地期货回测 / AI 分析工具包。

模块划分：
    cta_lab.bootstrap   : vnpy 运行目录引导（**最先执行**）
    cta_lab.contracts   : 交易所与品种元数据（乘数、最小变动价位）
    cta_lab.downloader  : 东方财富期货行情下载（免 token、免第三方库）
    cta_lab.exchange    : 东方财富行情 -> vnpy BarData / ContractData
    cta_lab.store       : 写入 vnpy 数据库（SqliteDatabase / DuckDB）
    cta_lab.dataio      : 数据落地编排（下载 -> 入库）
    cta_lab.runner      : headless 回测运行器（无 GUI）
    cta_lab.strategies  : 自包含 CTA 策略库
    cta_lab.ai          : 交易层面统计 + 稳健性评分 + 策略诊断 + LLM 解读
    cta_lab.cli         : 命令行入口
"""

# ---------------------------------------------------------------------------
# 关键：在任何人 import vnpy 之前先把 .vntrader/ 建好，
# 否则 vnpy 会把运行目录退化成用户主目录（详见 bootstrap 文档）。
# ---------------------------------------------------------------------------
from .bootstrap import ensure_trader_dir as _ensure_trader_dir

_ensure_trader_dir()

__version__ = "1.0.0"

__all__ = ["__version__"]
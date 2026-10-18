"""策略目录 —— vnpy GUI 会自动扫描此目录。

`MainEngine.__init__` 会执行 `os.chdir(TRADER_DIR)`，`CtaBacktester` 随后
用 `Path.cwd()/"strategies"` 扫描策略。项目根目录下存在 `.vntrader/` 时
（由 `cta_lab.bootstrap` 保证），`TRADER_DIR` 就是项目根目录，
因此 GUI 会直接扫描到这个目录。

目录内的文件是**发现垫片**：真正的实现统一放在 `cta_lab/strategies.py`，
避免两处维护、保证命令行与 GUI 行为一致。

新增自己的策略（两种方式）：
    1. 推荐：在 cta_lab/strategies.py 实现并注册到 STRATEGY_REGISTRY，
       然后在此目录加一个同名垫片文件（GUI 才能看到）。
    2. 直接在此目录写完整策略文件（GUI 立即可见；命令行需用
       --strategy 指定类名，或在 cta_lab/strategies.py 里注册）。
"""

from cta_lab.strategies import (  # noqa: F401
    AtrChannelStrategy,
    BollingerRevertStrategy,
    DonchianBreakoutStrategy,
    MaCrossStrategy,
)

__all__ = [
    "MaCrossStrategy",
    "DonchianBreakoutStrategy",
    "AtrChannelStrategy",
    "BollingerRevertStrategy",
]

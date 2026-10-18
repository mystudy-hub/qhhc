"""vnpy 运行目录引导（**必须在导入任何 vnpy 子模块之前执行**）。

背景
----
`vnpy.trader.utility` 在 **被 import 的那一刻** 就决定了两个目录：

.. code-block:: python

    def _get_trader_dir(temp_name):
        cwd = Path.cwd()
        temp_path = cwd / temp_name
        if temp_path.exists():          # 当前目录下有 .vntrader/
            return cwd, temp_path        # TRADER_DIR=cwd, TEMP_DIR=cwd/.vntrader
        home = Path.home()
        temp_path = home / temp_name
        return home, temp_path           # TRADER_DIR=home, TEMP_DIR=~/.vntrader

而 `MainEngine.__init__` 里有 `os.chdir(TRADER_DIR)`，
`CtaBacktester` 又用 `Path.cwd()/"strategies"` 扫描策略。

于是产生两个连锁问题：

1. 项目根目录没有 `.vntrader/` 时，`TRADER_DIR` 会退化成**用户主目录**，
   GUI 一启动就把工作目录切到 `~/`，并在 `~/strategies/` 找策略 —— 必然找不到。
2. 数据库则会落在 `~/.vntrader/`，与项目分离。

解法
----
在项目根目录建一个 `.vntrader/` 目录。这样：

    TRADER_DIR = <项目根目录>
    TEMP_DIR   = <项目根目录>/.vntrader

带来三个好处：
    - GUI 工作目录 = 项目根目录 → 直接扫描 `<项目根>/strategies/`
    - 数据库落在 `<项目根>/.vntrader/`，整个环境可随项目一起打包/删除
    - 不再污染用户主目录

本模块只依赖标准库，绝不能 import vnpy。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: 项目根目录（本文件位于 <root>/cta_lab/bootstrap.py）
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: 期望的 vnpy 运行目录
TRADER_DIR: Path = PROJECT_ROOT / ".vntrader"

#: 项目使用的数据库文件名
DB_NAME = "futures_lab.db"

_LEGACY_DB = Path.home() / ".vntrader" / DB_NAME


def _vnpy_already_loaded() -> bool:
    return "vnpy.trader.utility" in sys.modules


def ensure_trader_dir(verbose: bool = False) -> Path:
    """确保项目根目录下存在 `.vntrader/`，并迁移旧数据库（若有）。

    返回 TRADER_DIR。
    """
    created = not TRADER_DIR.exists()
    TRADER_DIR.mkdir(parents=True, exist_ok=True)

    # 迁移：以前数据落在 ~/.vntrader/futures_lab.db
    target_db = TRADER_DIR / DB_NAME
    if not target_db.exists() and _LEGACY_DB.exists():
        try:
            shutil.copy2(_LEGACY_DB, target_db)
            if verbose:
                print(f"已迁移历史数据库: {_LEGACY_DB} -> {target_db}")
        except OSError as exc:
            print(f"[WARN] 迁移旧数据库失败(可忽略): {exc}")

    if _vnpy_already_loaded():
        # 已经晚了：TRADER_DIR 已被 vnpy 固化
        print(
            "[WARN] vnpy 已先于 bootstrap 被导入，"
            f"TRADER_DIR 可能仍指向 {Path.home()}。\n"
            "       请确保 `from cta_lab.bootstrap import ensure_trader_dir` "
            "出现在任何 vnpy 导入之前。",
            file=sys.stderr,
        )

    if verbose and created:
        print(f"已创建 vnpy 运行目录: {TRADER_DIR}")

    return TRADER_DIR


def db_path() -> Path:
    """项目数据库的完整路径。"""
    return TRADER_DIR / DB_NAME


def strategies_dir() -> Path:
    """GUI 扫描的策略目录（= TRADER_DIR/strategies）。"""
    return TRADER_DIR.parent / "strategies"


__all__ = [
    "PROJECT_ROOT",
    "TRADER_DIR",
    "DB_NAME",
    "ensure_trader_dir",
    "db_path",
    "strategies_dir",
]
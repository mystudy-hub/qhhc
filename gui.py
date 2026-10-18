"""启动 vnpy 图形界面（仅 CTA 回测 + 数据管理，不加载任何交易接口）。

执行::

    .venv\\Scripts\\python gui.py     （或双击 scripts\\run_gui.bat）

启动后菜单栏 -> 「功能」->「CTA回测」/「数据管理」。

两个 vnpy 陷阱已在此处理好
--------------------------
1. `MainEngine.__init__` 会 `os.chdir(TRADER_DIR)`。若项目根目录没有
   `.vntrader/`，`TRADER_DIR` 会退化成用户主目录，GUI 便去 `~/strategies`
   找策略（必然为空）。**解决**：导入 vnpy 之前先执行
   `cta_lab.bootstrap.ensure_trader_dir()`，把 `.vntrader/` 建在项目根目录。
2. `MainEngine.add_app()` 不会调用 `engine.init_engine()`，必须显式调用，
   否则回测引擎不加载策略、也没有数据服务。
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# 必须最先执行：建立 <项目根>/.vntrader，使 TRADER_DIR == 项目根目录
# ---------------------------------------------------------------------------
from cta_lab.bootstrap import TRADER_DIR, db_path, ensure_trader_dir

ensure_trader_dir(verbose=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    # 指定本项目使用的数据库（早于任何 vnpy 数据库访问）
    from cta_lab.store import init_database

    init_database(db_name="futures_lab.db")

    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine
    from vnpy.trader.ui import MainWindow, create_qapp
    from vnpy_ctabacktester import CtaBacktesterApp
    from vnpy_datamanager import DataManagerApp

    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    backtester_engine = main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)

    # 关键：add_app 不会自动初始化引擎
    backtester_engine.init_engine()

    window = MainWindow(main_engine, event_engine)
    window.showMaximized()

    names = sorted(backtester_engine.get_strategy_class_names())
    print("VeighNa Trader 已启动。")
    print(f"  运行目录  : {TRADER_DIR}")
    print(f"  数据库    : {db_path()}")
    print(f"  可回测策略: {len(names)} 个")
    for name in names:
        print(f"    - {name}")
    print("  菜单栏 -> 「功能」->「CTA回测」/「数据管理」")

    qapp.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
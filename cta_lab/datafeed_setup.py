"""配置并验证 vnpy 数据服务（当前支持天勤 TqSdk）。

用法::

    # 1) 写入账号到 .vntrader/vt_setting.json
    python -m cta_lab.datafeed_setup --username 你的手机号 --password 你的密码

    # 2) 验证数据服务是否可用（会真正联网拉一小段数据）
    python -m cta_lab.datafeed_setup --verify

    # 3) 查看当前配置（密码会打码）
    python -m cta_lab.datafeed_setup --show

    # 4) 清除配置
    python -m cta_lab.datafeed_setup --clear

天勤账号免费注册: https://account.shinnytech.com/
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .bootstrap import TRADER_DIR

# vnpy 全局设置文件
SETTING_FILE: Path = TRADER_DIR / "vt_setting.json"

# 支持的数据服务适配器 -> PyPI 包名
DATAFEEDS: dict[str, str] = {
    "tqsdk": "vnpy_tqsdk",
    "rqdata": "vnpy_rqdata",
    "wind": "vnpy_wind",
    "tinysoft": "vnpy_tinysoft",
    "xt": "vnpy_xt",
    "tts": "vnpy_tts",
}


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_setting() -> dict:
    if SETTING_FILE.exists():
        try:
            return json.loads(SETTING_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_setting(data: dict) -> None:
    SETTING_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTING_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
    )


def configure(
    name: str = "tqsdk",
    username: str = "",
    password: str = "",
    verbose: bool = True,
) -> Path:
    """把数据服务写入 vnpy 全局配置。"""
    if name not in DATAFEEDS:
        raise ValueError(f"未知数据服务 {name!r}，可选: {sorted(DATAFEEDS)}")

    data = load_setting()
    data["datafeed.name"] = name
    data["datafeed.username"] = username
    data["datafeed.password"] = password
    save_setting(data)

    if verbose:
        print(f"已写入配置: {SETTING_FILE}")
        print(f"  datafeed.name     = {name}")
        print(f"  datafeed.username = {username or '(空)'}")
        print(f"  datafeed.password = {'*' * len(password) if password else '(空)'}")
        print(f"\n注意: 运行回测的程序需重启才能读到新配置。")
        print(f"依赖包: {DATAFEEDS[name]}（已安装: ", end="")
        try:
            __import__(DATAFEEDS[name])
            print("是)")
        except Exception:  # noqa: BLE001
            print("否，需 pip install " + DATAFEEDS[name] + ")")
    return SETTING_FILE


def clear(verbose: bool = True) -> None:
    """清空数据服务配置。"""
    data = load_setting()
    data["datafeed.name"] = ""
    data["datafeed.username"] = ""
    data["datafeed.password"] = ""
    save_setting(data)
    if verbose:
        print(f"已清空数据服务配置: {SETTING_FILE}")


def show() -> None:
    """显示当前数据服务配置（密码打码）。"""
    data = load_setting()
    name = data.get("datafeed.name", "")
    user = data.get("datafeed.username", "")
    pwd = data.get("datafeed.password", "")
    print(f"配置文件: {SETTING_FILE}")
    print(f"  datafeed.name     = {name or '(未配置)'}")
    print(f"  datafeed.username = {user or '(空)'}")
    print(f"  datafeed.password = {'*' * len(pwd) if pwd else '(空)'}")
    if name:
        pkg = DATAFEEDS.get(name, f"vnpy_{name}")
        try:
            __import__(pkg)
            print(f"  依赖包 {pkg}: 已安装")
        except Exception:  # noqa: BLE001
            print(f"  依赖包 {pkg}: 未安装")


def verify(
    symbol: str = "rb",
    exchange: str = "SHFE",
    days: int = 30,
    verbose: bool = True,
) -> bool:
    """真正联网验证数据服务能否返回数据。"""
    # 让 vnpy 读到刚写入的配置
    from vnpy.trader.setting import SETTINGS

    data = load_setting()
    for key, value in data.items():
        SETTINGS[key] = value

    # 清空 get_datafeed 缓存，强制按新配置重建
    from vnpy.trader import datafeed as dfm

    dfm.datafeed = None

    datafeed = dfm.get_datafeed()
    print(f"数据服务对象: {type(datafeed).__name__}")
    if type(datafeed).__name__ == "BaseDatafeed":
        print("  -> 未加载任何数据服务，请先执行 configure（--username/--password）")
        return False

    from vnpy.trader.constant import Exchange, Interval
    from vnpy.trader.object import HistoryRequest

    req = HistoryRequest(
        symbol=symbol,
        exchange=Exchange(exchange),
        start=datetime.now() - timedelta(days=days),
        end=datetime.now(),
        interval=Interval.DAILY,
    )
    print(f"请求: {symbol}.{exchange} 日线，最近 {days} 天")

    try:
        bars = datafeed.query_bar_history(req, output=print)
    except Exception as exc:  # noqa: BLE001
        print(f"请求异常: {type(exc).__name__}: {exc}")
        return False

    if not bars:
        print("返回空数据（账号未生效？品种代码不对？或该周期无权限）")
        return False

    print(f"成功返回 {len(bars)} 根 K 线")
    for bar in bars[-3:]:
        print(
            f"  {bar.datetime:%Y-%m-%d} 开{bar.open_price} 高{bar.high_price} "
            f"低{bar.low_price} 收{bar.close_price} 量{bar.volume:.0f} "
            f"持仓{bar.open_interest:.0f}"
        )
    return True


def main(argv: list[str] | None = None) -> int:
    _utf8()
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cta_lab.datafeed_setup",
        description="配置/验证 vnpy 历史数据服务",
    )
    parser.add_argument("--name", default="tqsdk", choices=sorted(DATAFEEDS),
                        help="数据服务名（默认 tqsdk）")
    parser.add_argument("--username", help="账号（天勤=快期账号/手机号）")
    parser.add_argument("--password", help="密码")
    parser.add_argument("--show", action="store_true", help="显示当前配置")
    parser.add_argument("--clear", action="store_true", help="清空配置")
    parser.add_argument("--verify", action="store_true", help="联网验证")
    parser.add_argument("--symbol", default="rb", help="验证用品种（默认 rb）")
    parser.add_argument("--exchange", default="SHFE", help="交易所（默认 SHFE）")
    parser.add_argument("--days", type=int, default=30, help="验证回溯天数")
    args = parser.parse_args(argv)

    if args.show:
        show()
        return 0

    if args.clear:
        clear()
        return 0

    if args.username is not None or args.password is not None:
        configure(
            name=args.name,
            username=args.username or "",
            password=args.password or "",
        )
        if not args.verify:
            return 0

    if args.verify:
        ok = verify(
            symbol=args.symbol,
            exchange=args.exchange,
            days=args.days,
        )
        return 0 if ok else 1

    show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

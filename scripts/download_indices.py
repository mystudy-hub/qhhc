"""下载全市场活跃商品期货加权指数历史日线到本地 vnpy 数据库。

数据源：天勤 TqSdk (KQ.i@<EXCHANGE>.<symbol>)
存储：vnpy 数据库 (symbol_index.<EXCHANGE>)
自动过滤：持仓量过低或已暂停/停止交易的死水品种（如动力煤 ZC、普麦 PM、强麦 WH、早籼稻 RI、粳稻 JR、线材 wr、胶合板 bb 等）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 确保能导入 cta_lab
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from cta_lab.bootstrap import TRADER_DIR
from cta_lab.contracts import all_symbols, get_spec
from cta_lab.store import init_database, save_bars
from cta_lab.tqsdk_source import _UPPERCASE_EXCHANGES

CN_TZ = ZoneInfo("Asia/Shanghai")
SETTING_FILE = TRADER_DIR / "vt_setting.json"


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_credentials() -> tuple[str, str]:
    if not SETTING_FILE.exists():
        raise RuntimeError(f"未找到配置文件: {SETTING_FILE}")
    try:
        data = json.loads(SETTING_FILE.read_text(encoding="utf-8"))
        user = data.get("datafeed.username", "").strip()
        pwd = data.get("datafeed.password", "").strip()
        if not user or not pwd:
            raise RuntimeError("天勤账号未配置，请先运行 configure_datafeed.bat")
        return user, pwd
    except Exception as exc:
        raise RuntimeError(f"读取天勤配置失败: {exc}") from exc


def get_tq_index_symbol(symbol: str, exchange: Exchange) -> str:
    """返回天勤加权指数合约代码。"""
    use_upper = exchange in _UPPERCASE_EXCHANGES
    sym_code = symbol.upper() if use_upper else symbol.lower()
    return f"KQ.i@{exchange.value}.{sym_code}"


def main() -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="下载中国期货全品种加权指数日K线")
    parser.add_argument("--min-oi", type=float, default=500, help="最小持仓量门槛，低于此值视为不再交易品种（默认 500）")
    parser.add_argument("--data-length", type=int, default=10000, help="单品种拉取根数上限（默认 10000，覆盖最长历史）")
    parser.add_argument("--interval", default="d", choices=["d", "1h", "1m"], help="K线周期（默认 d 日线）")
    args = parser.parse_args()

    interval_map = {
        "d": (Interval.DAILY, 86400),
        "1h": (Interval.HOUR, 3600),
        "1m": (Interval.MINUTE, 60),
    }
    interval_enum, duration_sec = interval_map[args.interval]

    username, password = load_credentials()
    print(f"==================================================")
    print(f" 天勤期货全品种加权指数下载器 (TqSdk)")
    print(f" 账号: {username}")
    print(f" 周期: {interval_enum.value} (duration={duration_sec}s)")
    print(f" 活跃门槛: 持仓量 > {args.min_oi}")
    print(f"==================================================")

    from tqsdk import TqApi, TqAuth

    print("正在连接天勤行情服务...")
    api = TqApi(auth=TqAuth(username, password))

    init_database()

    # 1. 建立全品种天勤代码映射
    symbols = all_symbols()
    tq_map = {}
    for s in symbols:
        spec = get_spec(s)
        tq_sym = get_tq_index_symbol(spec.symbol, spec.exchange)
        tq_map[s] = (spec, tq_sym)

    # 2. 批量订阅行情并检测活跃度
    print(f"\n[1/3] 正在对全市场 {len(tq_map)} 个品种进行实时流动性检测...")
    quotes = {s: api.get_quote(tq_sym) for s, (spec, tq_sym) in tq_map.items()}
    for _ in range(15):
        api.wait_update()

    active_list = []
    inactive_list = []

    for s, (spec, tq_sym) in tq_map.items():
        q = quotes[s]
        oi = getattr(q, "open_interest", 0) or 0
        lp = getattr(q, "last_price", 0) or 0
        vol = getattr(q, "volume", 0) or 0

        is_inactive = False
        reasons = []
        if oi < args.min_oi:
            is_inactive = True
            reasons.append(f"持仓过低 (OI={oi:.0f})")
        if lp <= 0 or math.isnan(lp):
            is_inactive = True
            reasons.append(f"无有效报价")

        if is_inactive:
            inactive_list.append((spec, tq_sym, oi, vol, ", ".join(reasons)))
        else:
            active_list.append((spec, tq_sym, oi, vol, lp))

    # 打印被过滤的品种
    print(f"\n[2/3] 流动性筛选结果:")
    print(f"  -> 活跃可交易品种: {len(active_list)} 个")
    print(f"  -> 不再交易/死水品种（已过滤跳过）: {len(inactive_list)} 个")
    for spec, tq_sym, oi, vol, reason in inactive_list:
        print(f"     [跳过] {spec.name:8s} ({spec.symbol}.{spec.exchange.value:6s}) - 原因: {reason}")

    # 3. 逐个下载活跃品种的加权指数数据
    print(f"\n[3/3] 开始拉取加权指数 K 线并落库 (共 {len(active_list)} 个活跃品种)...")
    success_count = 0
    total_bars_saved = 0
    start_time = time.time()

    for idx, (spec, tq_sym, oi, vol, lp) in enumerate(active_list, 1):
        index_code = f"{spec.symbol}_index"
        vt_index = f"{index_code}.{spec.exchange.value}"
        try:
            klines = api.get_kline_serial(tq_sym, duration_sec, data_length=args.data_length)

            # 等待数据填充稳定
            deadline = time.time() + 15
            prev_valid = -1
            stable_rounds = 0
            while time.time() < deadline:
                api.wait_update()
                valid = int((klines["datetime"] > 0).sum())
                if valid == prev_valid and valid > 0:
                    stable_rounds += 1
                    if stable_rounds >= 4:
                        break
                else:
                    stable_rounds = 0
                    prev_valid = valid

            filled = klines[klines["datetime"] > 0]
            if len(filled) == 0:
                print(f"  [{idx:2d}/{len(active_list)}] [EMPTY] {spec.name:6s} ({vt_index}) 未返回有效数据")
                continue

            bars: list[BarData] = []
            for row in filled.itertuples(index=False):
                ts = int(getattr(row, "datetime")) / 1e9
                dt = datetime.fromtimestamp(ts, tz=CN_TZ).replace(tzinfo=None)
                close_price = round(float(getattr(row, "close")), 4)
                v = float(getattr(row, "volume") or 0)
                bars.append(
                    BarData(
                        symbol=index_code,
                        exchange=spec.exchange,
                        datetime=dt,
                        interval=interval_enum,
                        open_price=round(float(getattr(row, "open")), 4),
                        high_price=round(float(getattr(row, "high")), 4),
                        low_price=round(float(getattr(row, "low")), 4),
                        close_price=close_price,
                        volume=v,
                        turnover=round(close_price * v, 2),
                        open_interest=float(getattr(row, "close_oi") or 0),
                        gateway_name="TQSDK",
                    )
                )

            save_bars(bars)
            first_dt = bars[0].datetime.strftime("%Y-%m-%d")
            last_dt = bars[-1].datetime.strftime("%Y-%m-%d")
            print(f"  [{idx:2d}/{len(active_list)}] [ OK ] {spec.name:6s} ({vt_index:18s}) 写入 {len(bars):5d} 根 ({first_dt} -> {last_dt})")
            success_count += 1
            total_bars_saved += len(bars)
        except Exception as exc:
            print(f"  [{idx:2d}/{len(active_list)}] [ERR ] {spec.name:6s} ({vt_index}): {exc}")

    api.close()
    elapsed = time.time() - start_time
    print(f"\n==================================================")
    print(f" 全部下载完成!")
    print(f"   成功品种数: {success_count} / {len(active_list)}")
    print(f"   总写入K线:  {total_bars_saved:,} 根")
    print(f"   耗时:       {elapsed:.1f} 秒")
    print(f"==================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""天勤 TqSdk 免费版历史行情源。

为什么不用官方 `vnpy_tqsdk` 适配器
----------------------------------
官方适配器(`vnpy_tqsdk 3.8.6.0`,56 行)调用的是::

    TqApi.get_kline_data_series(...)

实测该接口**仅专业版可用**,免费账号会直接报错::

    Exception: get_kline_data_series 数据获取方式仅限专业版用户使用,
    如需购买专业版或者申请试用,请访问 https://www.shinnytech.com/tqsdk-buy/

免费额度必须走 `TqApi.get_kline_serial()`,它的语义是
「拉取历史 + 订阅实时」,填充是**异步**的,需要多轮 `wait_update()` 直到稳定。

本模块实现免费版可用的取数逻辑,并处理三个适配问题:

1. **合约代码格式**(实测)
      主连   : KQ.m@SHFE.rb     (8 个交易所均可用)
      具体   : SHFE.rb2601 / DCE.i2601 / INE.sc2612 / GFEX.lc2601
      郑商所 : 必须是 3 位月份 —— CZCE.TA601 ✅,CZCE.TA2601 ❌
2. **字段差异**:tqsdk 返回 open/high/low/close/volume/open_oi/close_oi,
   没有成交额(turnover),这里按 close*volume 估算或置 0。
3. **时间**:`datetime` 列是**纳秒时间戳(UTC)**,需转 Asia/Shanghai。

实测免费额度边界(2026-10,螺纹钢主连)
--------------------------------------
    日线     : 2016-01 至今(约 2617 根)
    1 小时   : 约 436 天
    5 分钟   : 约 219 天
    1 分钟   : 约 49 天

即:**日线可做长历史回测,分钟线只能做近期日内研究**。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from vnpy.trader.constant import Exchange, Interval

from .downloader import RawBar
from .bootstrap import TRADER_DIR

CN_TZ = ZoneInfo("Asia/Shanghai")

# vnpy Interval -> tqsdk duration_seconds
INTERVAL_TO_SECONDS: dict[Interval, int] = {
    Interval.MINUTE: 60,
    Interval.HOUR: 3600,
    Interval.DAILY: 86400,
}

# 各交易所主连前缀(天勤约定:KQ.m@<交易所>.<品种>)
_MAIN_PREFIX = "KQ.m@"

# 合约代码字母为**大写**的交易所(实测)：
#   郑商所 CZCE : CZCE.TA601   中金所 CFFEX : CFFEX.IF2512
# 其余交易所均为小写：SHFE.rb2601 / DCE.i2601 / INE.sc2612 / GFEX.lc2601
_UPPERCASE_EXCHANGES = frozenset({Exchange.CZCE, Exchange.CFFEX})

# 估算每根 K 线覆盖的"自然日"密度，用于推算需要请求多少根
_BARS_PER_DAY: dict[Interval, float] = {
    Interval.MINUTE: 240.0,   # 日内约 240 分钟(含夜盘)
    Interval.HOUR: 6.0,       # 每天约 6 根小时线
    Interval.DAILY: 0.72,     # 约 5/7 为交易日
}

# 免费版单次请求上限（实测 10000 可返回，更大无意义）
MAX_DATA_LENGTH = 10_000

_UA_SETTING = TRADER_DIR / "vt_setting.json"


class TqAuthMissing(RuntimeError):
    """未配置天勤账号。"""


def _load_credentials() -> tuple[str, str]:
    """从 vnpy 全局配置读天勤账号。"""
    if not _UA_SETTING.exists():
        raise TqAuthMissing(
            f"未找到配置文件 {_UA_SETTING}\n"
            "请先执行: python -m cta_lab.datafeed_setup --username 账号 --password 密码"
        )
    try:
        data = json.loads(_UA_SETTING.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise TqAuthMissing(f"读取配置失败: {exc}") from exc

    user = str(data.get("datafeed.username", "")).strip()
    pwd = str(data.get("datafeed.password", "")).strip()
    if not user or not pwd:
        raise TqAuthMissing(
            "天勤账号未配置。请执行:\n"
            "    python -m cta_lab.datafeed_setup "
            "--username 你的快期账号 --password 你的密码"
        )
    return user, pwd


# ---------------------------------------------------------------------------
# 合约代码
# ---------------------------------------------------------------------------

def to_tq_symbol(
    symbol: str,
    exchange: Exchange,
    main_continuous: bool = True,
) -> str:
    """vnpy(品种/合约 + 交易所)-> 天勤合约代码。

    主连：``rb`` + SHFE      -> ``KQ.m@SHFE.rb``
    具体：``rb2601`` + SHFE  -> ``SHFE.rb2601``
    郑商所：``TA601`` + CZCE -> ``CZCE.TA601``（3 位月份）
    """
    ex = exchange.value
    sym = symbol.strip()
    use_upper = exchange in _UPPERCASE_EXCHANGES

    if main_continuous and not any(ch.isdigit() for ch in sym):
        product = sym.upper() if use_upper else sym.lower()
        return f"{_MAIN_PREFIX}{ex}.{product}"

    # 具体合约：品种字母大写(郑商所/中金所)或小写(其他)
    letters = "".join(ch for ch in sym if ch.isalpha())
    digits = "".join(ch for ch in sym if ch.isdigit())
    letters = letters.upper() if use_upper else letters.lower()

    digits = _normalize_czce_month(exchange, digits)
    return f"{ex}.{letters}{digits}"


def _normalize_czce_month(exchange: Exchange, digits: str) -> str:
    """郑商所在天勤需要 3 位月份(YMM)，如 2601 -> 601。"""
    if exchange != Exchange.CZCE:
        return digits
    if len(digits) == 4:
        return digits[1:]        # 2601 -> 601
    return digits


def _estimate_length(interval: Interval, start: datetime, end: datetime) -> int:
    """估算覆盖 [start, end] 需要请求多少根 K 线。"""
    days = max((end - start).days, 1)
    need = int(days * _BARS_PER_DAY.get(interval, 1.0) * 1.25) + 50
    return max(min(need, MAX_DATA_LENGTH), 60)


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------

def _drive_until_stable(api, klines, wait_seconds: int, quiet: bool) -> int:
    """反复 wait_update 直到 K 线填充稳定，返回有效行数。"""
    deadline = time.time() + wait_seconds
    prev = -1
    stable = 0
    while time.time() < deadline:
        api.wait_update()
        valid = int((klines["datetime"] > 0).sum())
        if valid == prev:
            stable += 1
            # 连续多轮无增长且已有数据 => 历史已填充完
            if stable >= 6 and valid > 0:
                break
        else:
            stable = 0
            prev = valid
    return int((klines["datetime"] > 0).sum())


def fetch_bars(
    symbol: str,
    exchange: Exchange,
    interval: Interval = Interval.DAILY,
    start: datetime | None = None,
    end: datetime | None = None,
    main_continuous: bool = True,
    quiet: bool = True,
    wait_seconds: int = 90,
) -> list[RawBar]:
    """从天勤拉取历史 K 线（免费版可用）。

    与 `cta_lab.downloader.fetch_bars` 保持相同的入参/返回约定，
    便于在数据层互换或做降级。
    """
    from tqsdk import TqApi, TqAuth

    if interval not in INTERVAL_TO_SECONDS:
        raise ValueError(
            f"天勤源不支持周期 {interval.value}；"
            f"可选 {[i.value for i in INTERVAL_TO_SECONDS]}"
        )

    user, pwd = _load_credentials()
    tq_symbol = to_tq_symbol(symbol, exchange, main_continuous)
    duration = INTERVAL_TO_SECONDS[interval]

    start = start or datetime(2000, 1, 1)
    end = end or datetime.now()
    length = _estimate_length(interval, start, end)

    if not quiet:
        print(f"[tqsdk] 请求 {tq_symbol}  duration={duration}s  length={length}")

    # tqsdk 关闭时会打印大量 asyncio 噪音，这里临时压制 stderr
    saved_stderr = sys.stderr
    null = open("nul" if sys.platform == "win32" else "/dev/null", "w")
    sys.stderr = null
    api = None
    try:
        api = TqApi(auth=TqAuth(user, pwd))
        klines = api.get_kline_serial(tq_symbol, duration, data_length=length)
        valid = _drive_until_stable(api, klines, wait_seconds, quiet)

        if valid == 0:
            raise RuntimeError(
                f"天勤未返回数据: {tq_symbol}。"
                "请检查合约代码、周期权限（1 分钟仅回溯约 50 天）"
            )

        filled = klines[klines["datetime"] > 0]
        bars: list[RawBar] = []
        for row in filled.itertuples(index=False):
            ts = int(getattr(row, "datetime")) / 1e9
            dt = datetime.fromtimestamp(ts, tz=CN_TZ).replace(tzinfo=None)

            if dt < start or dt > end:
                continue

            close_price = float(getattr(row, "close"))
            volume = float(getattr(row, "volume") or 0)
            bars.append(
                RawBar(
                    datetime=dt,
                    open=float(getattr(row, "open")),
                    close=close_price,
                    high=float(getattr(row, "high")),
                    low=float(getattr(row, "low")),
                    volume=volume,
                    # 天勤不提供成交额，按 收盘价*成交量 估算
                    turnover=close_price * volume,
                    open_interest=float(getattr(row, "close_oi") or 0),
                )
            )
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:  # noqa: BLE001
                pass
        sys.stderr = saved_stderr
        null.close()

    bars.sort(key=lambda b: b.datetime)
    return bars


def probe(symbol: str = "rb", exchange: Exchange = Exchange.SHFE) -> dict:
    """连通性自检：拉一小段日线，返回摘要。"""
    bars = fetch_bars(
        symbol, exchange, Interval.DAILY,
        start=datetime.now() - timedelta(days=20),
        end=datetime.now(),
    )
    if not bars:
        return {"ok": False, "error": "无数据"}
    return {
        "ok": True,
        "bars": len(bars),
        "first": bars[0].date_str,
        "last": bars[-1].date_str,
        "last_close": bars[-1].close,
        "last_oi": bars[-1].open_interest,
    }


__all__ = [
    "TqAuthMissing",
    "to_tq_symbol",
    "fetch_bars",
    "probe",
    "INTERVAL_TO_SECONDS",
]

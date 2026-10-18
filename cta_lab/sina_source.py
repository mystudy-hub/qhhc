"""新浪财经期货日线源（东财被限流时的主力备份）。

为什么需要它
------------
东方财富的行情接口（``push2his`` / ``push2``）有较硬的 IP 级频控：
短时间大量请求后会**直接断连且不给响应**，恢复时间不确定。
实测在此状态下 ``searchapi`` 仍可用，但取不到 K 线。

新浪的 ``InnerFuturesNewService.getDailyKLine`` 是另一个可选源，特点是：

    - 覆盖全部 6 家交易所
    - 历史深度**不输东财、部分品种更深**（沪铜/豆粕可回溯到 2005）
    - **带结算价**（东财没有），持仓量也有
    - 免费、无需 token
    - 但**没有成交额**（turnover），需按收盘价×成交量估算

字段映射（实测）
----------------
    d 日期   o 开   h 高   l 低   c 收
    v 成交量（手）
    p 持仓量（手）
    s 结算价

合约代码规则
------------
    连续/主力：``<品种代码大写>0``      → RB0 / CU0 / TA0 / IF0 / LC0
    具体合约  ：``<品种代码大写><月份>`` → RB2601 / TA601

注意：新浪对指数期货（IF/IH/IC/IM）的连续合约只回溯到 2017-01-17 左右，
比中金所实际上市时间（2010-04）短，做超长回测时需知悉。
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from datetime import datetime

from vnpy.trader.constant import Exchange

from .downloader import RawBar, DownloadError
from .contracts import get_spec

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_BASE = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{sym}=/InnerFuturesNewService.getDailyKLine?symbol={sym}"
)


def to_sina_symbol(
    symbol: str,
    exchange: Exchange,
    main_continuous: bool = True,
) -> str:
    """vnpy 品种/合约 -> 新浪代码。

    主连：``rb`` + SHFE      -> ``RB0``
    具体：``rb2601`` + SHFE  -> ``RB2601``
    郑商所：``TA601`` + CZCE -> ``TA601``（3 位月份，与新浪一致）
    """
    sym = symbol.strip()
    if main_continuous and not any(ch.isdigit() for ch in sym):
        return f"{sym.upper()}0"

    letters = "".join(ch for ch in sym if ch.isalpha()).upper()
    digits = "".join(ch for ch in sym if ch.isdigit())
    return f"{letters}{digits}"


def _http_get(url: str, retries: int = 3, timeout: int = 30) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("gbk", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise DownloadError(f"新浪请求失败({retries}次): {url}\n  原因: {last}")


def _parse_payload(text: str) -> list[dict]:
    """从 JSONP 文本里抠出 JSON 数组。"""
    # 形如: /*<script>...*/ var _RB0=([{...},{...}]);
    m = re.search(r"=\s*\((\[.*\])\)", text, re.S)
    if not m:
        m = re.search(r"=\s*(\[.*\])", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def fetch_bars(
    symbol: str,
    exchange: Exchange,
    interval=None,
    start: datetime | None = None,
    end: datetime | None = None,
    main_continuous: bool = True,
    quiet: bool = True,
) -> list[RawBar]:
    """下载日线（与 cta_lab.downloader.fetch_bars 同签名，便于互换）。"""
    from vnpy.trader.constant import Interval

    if interval is not None and interval != Interval.DAILY:
        raise ValueError(
            f"新浪源只提供日线，不支持 {interval}。"
            "分钟线请用东财（--source em）或天勤（--source tqsdk）。"
        )

    sina_sym = to_sina_symbol(symbol, exchange, main_continuous)
    url = _BASE.format(sym=sina_sym)

    text = _http_get(url)
    rows = _parse_payload(text)
    if not rows:
        raise DownloadError(
            f"新浪无数据: {sina_sym}（品种代码或交易所可能不匹配）"
        )

    bars: list[RawBar] = []
    for row in rows:
        try:
            dt = datetime.strptime(str(row["d"])[:10], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        if start and dt < start:
            continue
        if end and dt > end:
            continue

        close_price = float(row.get("c") or 0)
        volume = float(row.get("v") or 0)
        bars.append(
            RawBar(
                datetime=dt,
                open=float(row.get("o") or 0),
                close=close_price,
                high=float(row.get("h") or 0),
                low=float(row.get("l") or 0),
                volume=volume,
                # 新浪不提供成交额，按 收盘价 x 成交量 估算
                turnover=close_price * volume,
                # p = 持仓量
                open_interest=float(row.get("p") or 0),
            )
        )

    bars.sort(key=lambda b: b.datetime)
    return bars


def probe(symbol: str = "rb", exchange: Exchange = Exchange.SHFE) -> dict:
    """连通性自检。"""
    bars = fetch_bars(symbol, exchange)
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


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        rc = getattr(s, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    r = probe("rb")
    print(r)

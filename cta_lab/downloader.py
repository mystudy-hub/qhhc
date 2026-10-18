"""东方财富期货行情下载器。

为什么用东方财富而不用 AkShare：
    - 免 token、免注册，直接 HTTP 即可
    - 支持国内全部 6 家期货交易所
    - 支持主力连续合约（<品种>m，如 rbm = 螺纹钢主连）
    - 日线历史可回溯到 2009 年（4000+ 根）

已知限制（实测 2026-10）：
    - 1m  : 仅当天约 70 根
    - 5m  : 约 1400 根（1 个月）
    - 15m/30m/1h : 约 250~500 根
    - 1d  : 4274 根（2009-03-30 起）
    - 1w  : 902 根
    分钟线只适合短周期验证，长周期回测请用日线。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from vnpy.trader.constant import Exchange, Interval

from .contracts import get_spec

# ---------------------------------------------------------------------------
# 东方财富 secid 市场号
# ---------------------------------------------------------------------------

MARKET_ID: dict[Exchange, int] = {
    Exchange.SHFE: 113,
    Exchange.DCE: 114,
    Exchange.CZCE: 115,
    Exchange.INE: 142,
    Exchange.CFFEX: 220,
    Exchange.GFEX: 225,
}

# vnpy Interval -> 东财 klt
KLT: dict[Interval, int] = {
    Interval.MINUTE: 1,
    Interval.HOUR: 60,
    Interval.DAILY: 101,
}

# 东财 klt -> (Interval, 波长分钟数)；用于 5m/15m/30m 这类 vnpy **没有**的粒度。
#
# ⚠️ 重要：这些粒度**不能直接入 vnpy bar 表**。
# vnpy 的 Interval 枚举只有 1m/1h/d/w/tick，bar 表唯一索引含 interval 列，
# 若把 5m 数据贴上 '1m' 标签写入，会与真正的 1 分钟线
# 在整点时刻撞唯一索引、互相覆盖。
#
# 正确用法：走 cta_lab.altstore（列式文件，物理隔离）；
# 若确需走 vnpy 回测，请把粒度编码进 symbol（如 rb_5m）后再入表。
KLT_EXTRA: dict[int, tuple[Interval, int]] = {
    5: (Interval.MINUTE, 5),
    15: (Interval.MINUTE, 15),
    30: (Interval.MINUTE, 30),
    102: (Interval.DAILY, 7),   # 周线，聚合为日线意义不大，仅备查
}

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

_SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"


class DownloadError(RuntimeError):
    """行情下载失败。"""


@dataclass
class RawBar:
    """东财原始 K 线（未做合约规格换算）。"""

    datetime: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    turnover: float
    open_interest: float

    @property
    def date_str(self) -> str:
        return self.datetime.strftime("%Y-%m-%d %H:%M")


def _http_get(url: str, retries: int = 4, timeout: int = 30) -> str:
    """带重试的 HTTP GET（东财偶发 SSL EOF，必须重试）。"""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 网络层任何异常都重试
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise DownloadError(f"请求失败({retries}次重试): {url}\n  原因: {last}")


def _parse_dt(text: str) -> datetime:
    """东财时间戳解析：'2026-10-14' 或 '2026-10-13 21:05'。"""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {text!r}")


# ---------------------------------------------------------------------------
# 合约发现
# ---------------------------------------------------------------------------

@dataclass
class ContractInfo:
    """东财合约搜索结果。"""

    symbol: str
    name: str
    exchange: Exchange
    is_main: bool
    market: int


def suggest(keyword: str, count: int = 20) -> list[ContractInfo]:
    """用东财搜索接口按中文名/代码查合约，返回期货类结果。"""
    url = (
        "https://searchapi.eastmoney.com/api/suggest/get?input="
        + urllib.parse.quote(keyword)
        + f"&type=14&token={_SUGGEST_TOKEN}&count={count}"
    )
    data = json.loads(_http_get(url))
    rows = (data.get("QuotationCodeTable") or {}).get("Data") or []

    code_to_ex = {v: k for k, v in MARKET_ID.items()}
    out: list[ContractInfo] = []
    for row in rows:
        if row.get("SecurityTypeName") != "期货":
            continue
        try:
            market = int(row.get("MktNum") or -1)
        except (TypeError, ValueError):
            continue
        exchange = code_to_ex.get(market)
        if exchange is None:
            continue
        symbol = str(row.get("Code") or "")
        out.append(
            ContractInfo(
                symbol=symbol,
                name=str(row.get("Name") or ""),
                exchange=exchange,
                is_main=symbol.lower().endswith("m") and len(symbol) <= 6,
                market=market,
            )
        )
    return out


def list_contracts(exchange: Exchange, page_size: int = 100) -> list[dict]:
    """翻页列出某交易所全部挂牌合约（含主力连续代码）。"""
    market = MARKET_ID[exchange]
    out: list[dict] = []
    page = 1
    while True:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid=f3"
            f"&fs=m:{market}&fields=f12,f13,f14"
        )
        data = json.loads(_http_get(url))
        block = data.get("data") or {}
        diff = block.get("diff") or []
        if not diff:
            break
        for row in diff:
            out.append({"symbol": row.get("f12"), "name": row.get("f14")})
        total = int(block.get("total") or 0)
        if len(out) >= total:
            break
        page += 1
        time.sleep(0.2)
    return out


def list_all_mains() -> list[tuple[Exchange, str, str]]:
    """返回全部交易所的主力连续合约 [(exchange, 代码, 名称)]。"""
    result: list[tuple[Exchange, str, str]] = []
    for exchange in MARKET_ID:
        try:
            rows = list_contracts(exchange)
        except DownloadError:
            continue
        for row in rows:
            code = str(row.get("symbol") or "")
            name = str(row.get("name") or "")
            if code.lower().endswith("m") and len(code) <= 6:
                result.append((exchange, code, name))
        time.sleep(0.3)
    return result


# ---------------------------------------------------------------------------
# K 线下载
# ---------------------------------------------------------------------------

def fetch_bars(
    symbol: str,
    exchange: Exchange,
    interval: Interval = Interval.DAILY,
    start: datetime | None = None,
    end: datetime | None = None,
    main_continuous: bool = True,
    adjust: int = 0,
) -> list[RawBar]:
    """下载 K 线。

    参数
    ----
    symbol            : 品种代码，如 'rb'；若给出 'rbm' 则视作已是主力连续代码
    exchange          : 交易所
    interval          : Interval.DAILY / Interval.MINUTE / Interval.HOUR
    start / end       : 时间范围（本地截取，接口本身按 lmt 返回全量）
    main_continuous   : 是否使用主力连续合约（<品种>m）
    adjust            : 东财 fqt 复权标志；主力连续用 1 可保证价格连续
    """
    market = MARKET_ID[exchange]

    key = symbol.strip()
    if key.lower().endswith("m") and len(key) <= 6:
        em_code = key.lower()
    elif main_continuous:
        spec = get_spec(key)
        em_code = spec.em_symbol
    else:
        em_code = key

    klt = KLT[interval]

    # 字段含义（实测确认）：
    #   f51 日期  f52 开  f53 收  f54 高  f55 低  f56 成交量  f57 成交额
    #   f58 振幅%  f59 涨跌%  f60 涨跌额  f61 换手%  f62 资金流  f63 持仓量
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={market}.{em_code}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
        f"&klt={klt}&fqt={adjust}&beg=0&end=20500101&lmt=1000000"
    )
    data = json.loads(_http_get(url))
    block = data.get("data")
    if not block:
        raise DownloadError(
            f"东财无数据: {market}.{em_code} (klt={klt})。"
            "请确认品种代码与交易所匹配，或换 main_continuous=False 试具体合约。"
        )

    bars: list[RawBar] = []
    for line in block.get("klines") or []:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        dt = _parse_dt(parts[0])
        bar = RawBar(
            datetime=dt,
            open=float(parts[1]),
            close=float(parts[2]),
            high=float(parts[3]),
            low=float(parts[4]),
            volume=float(parts[5] or 0),
            turnover=float(parts[6] or 0),
            # f63 为持仓量（部分品种/无夜盘时段可能省略）
            open_interest=float(parts[12]) if len(parts) > 12 and parts[12] else 0.0,
        )
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        bars.append(bar)

    bars.sort(key=lambda b: b.datetime)
    return bars


def fetch_extra_interval(
    symbol: str,
    exchange: Exchange,
    minutes: int,
    main_continuous: bool = True,
) -> list[RawBar]:
    """下载 vnpy 没有原生支持的 5m/15m/30m 粒度。

    ⚠️ 返回值**仅供 cta_lab.altstore 的列式分析通道使用**，
    不要直接写入 vnpy bar 表，否则会与同品种的 1 分钟线
    撞唯一索引造成数据污染（详见 KLT_EXTRA 注释）。

    正确接法::

        from cta_lab.altstore import save_alt_bars
        bars = fetch_extra_interval("rb", Exchange.SHFE, 5)
        save_alt_bars(bars, "rb", Exchange.SHFE, 5)
    """
    if minutes not in KLT_EXTRA:
        raise ValueError(f"不支持的分钟粒度: {minutes}，可选 {sorted(KLT_EXTRA)}")

    market = MARKET_ID[exchange]
    spec = get_spec(symbol)
    em_code = spec.em_symbol if main_continuous else symbol
    klt = minutes

    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={market}.{em_code}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
        f"&klt={klt}&fqt=1&beg=0&end=20500101&lmt=1000000"
    )
    data = json.loads(_http_get(url))
    block = data.get("data")
    if not block:
        raise DownloadError(f"东财无数据: {market}.{em_code} (klt={klt})")

    out: list[RawBar] = []
    for line in block.get("klines") or []:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        out.append(
            RawBar(
                datetime=_parse_dt(parts[0]),
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5] or 0),
                turnover=float(parts[6] or 0) if len(parts) > 6 else 0.0,
                open_interest=float(parts[12]) if len(parts) > 12 and parts[12] else 0.0,
            )
        )
    out.sort(key=lambda b: b.datetime)
    return out


def to_dataframe(bars: list[RawBar]) -> "object":
    """转成 pandas DataFrame（可选依赖 pandas）。"""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "datetime": b.datetime,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "turnover": b.turnover,
                "open_interest": b.open_interest,
            }
            for b in bars
        ]
    )


if __name__ == "__main__":
    from .contracts import EXCHANGE_CN_NAME

    print("=== 主力连续合约全量枚举 ===")
    mains = list_all_mains()
    by_ex: dict[Exchange, list[str]] = {}
    for ex, code, name in mains:
        by_ex.setdefault(ex, []).append(code)
    for ex, codes in by_ex.items():
        print(f"{EXCHANGE_CN_NAME[ex]:6s} {len(codes):2d}: {sorted(codes)}")

    print("\n=== 下载示例：螺纹钢主连日线 ===")
    bars = fetch_bars("rb", Exchange.SHFE, Interval.DAILY)
    print(f"共 {len(bars)} 根：{bars[0].date_str} -> {bars[-1].date_str}")
    print("最后 3 根:")
    for b in bars[-3:]:
        print(f"  {b.date_str} O={b.open} H={b.high} L={b.low} C={b.close} V={b.volume}")

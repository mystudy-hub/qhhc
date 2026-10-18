"""东方财富原始行情 -> vnpy 对象（BarData / ContractData）。

时区约定（vnpy 官方口径，非常关键）：
    vnpy 内部统一使用 **上海时区** 的 aware datetime 作为 BarData.datetime。
    SqliteDatabase.save_bar_data() 会调用 convert_tz() 转为 DB_TZ(Asia/Shanghai)
    的 naive datetime 后落库；load_bar_data() 再转回 aware。
    因此这里必须挂 Asia/Shanghai tzinfo，否则落库时间会偏移 8 小时。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from vnpy.trader.constant import Exchange, Interval, Product
from vnpy.trader.object import BarData, ContractData

from .contracts import EXCHANGE_FEE, FutureSpec, get_spec
from .downloader import RawBar

CN_TZ = ZoneInfo("Asia/Shanghai")

# 东财不提供 symbol -> Product 的可靠映射，国内期货统一按 FUTURES 处理
DEFAULT_PRODUCT = Product.FUTURES

# 中金所国债期货的"乘数"单位与商品不同（元/点），回测按点数报价即可
GATEWAY_NAME = "EM"


def make_contract_data(
    spec: FutureSpec,
    gateway_name: str = GATEWAY_NAME,
) -> ContractData:
    """构造 ContractData（用于写入数据库 / 校验）。"""
    return ContractData(
        gateway_name=gateway_name,
        symbol=spec.symbol,
        exchange=spec.exchange,
        name=spec.name,
        product=DEFAULT_PRODUCT,
        size=spec.size,
        pricetick=spec.pricetick,
        min_volume=1,
        history_data=True,
    )


def raw_to_bar(
    raw: RawBar,
    spec: FutureSpec,
    interval: Interval = Interval.DAILY,
    gateway_name: str = GATEWAY_NAME,
) -> BarData:
    """单根原始 K 线 -> vnpy BarData。"""
    dt = raw.datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    else:
        dt = dt.astimezone(CN_TZ)

    return BarData(
        gateway_name=gateway_name,
        symbol=spec.symbol,
        exchange=spec.exchange,
        datetime=dt,
        interval=interval,
        volume=raw.volume,
        turnover=raw.turnover,
        open_interest=raw.open_interest,
        open_price=raw.open,
        high_price=raw.high,
        low_price=raw.low,
        close_price=raw.close,
    )


def raw_list_to_bars(
    raws: list[RawBar],
    spec: FutureSpec,
    interval: Interval = Interval.DAILY,
    gateway_name: str = GATEWAY_NAME,
) -> list[BarData]:
    """批量转换，并按时间升序去重（东财偶发重复时间戳）。"""
    bars = [raw_to_bar(r, spec, interval, gateway_name) for r in raws]
    bars.sort(key=lambda b: b.datetime)

    deduped: list[BarData] = []
    seen: set[datetime] = set()
    for bar in bars:
        if bar.datetime in seen:
            continue
        seen.add(bar.datetime)
        deduped.append(bar)
    return deduped


def default_fee(symbol: str, exchange: Exchange) -> tuple[float, float]:
    """按交易所给出默认 (rate, fixed) 手续费。"""
    conf = EXCHANGE_FEE.get(exchange, {"rate": 0.0, "fixed": 0.0})
    return float(conf["rate"]), float(conf["fixed"])


__all__ = [
    "CN_TZ",
    "make_contract_data",
    "raw_to_bar",
    "raw_list_to_bars",
    "default_fee",
    "get_spec",
    "DEFAULT_PRODUCT",
]
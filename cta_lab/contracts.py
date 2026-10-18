"""国内期货交易所与品种元数据。

数据来源：各交易所官网合约细则。
size       : 合约乘数（每手对应的数量）
pricetick  : 最小变动价位
slippage   : 回测默认滑点（价格单位，约等于 1 个 tick）
"""

from __future__ import annotations

from dataclasses import dataclass

from vnpy.trader.constant import Exchange

# ---------------------------------------------------------------------------
# 交易所基础参数
# ---------------------------------------------------------------------------

# 交易所统一下载参数：手续费率（按成交金额）与每手固定手续费
EXCHANGE_FEE: dict[Exchange, dict] = {
    Exchange.SHFE: {"rate": 0.0001, "fixed": 0.0},
    Exchange.INE: {"rate": 0.0001, "fixed": 0.0},
    Exchange.DCE: {"rate": 0.0001, "fixed": 0.0},
    Exchange.CZCE: {"rate": 0.0, "fixed": 3.0},
    Exchange.GFEX: {"rate": 0.0001, "fixed": 0.0},
    Exchange.CFFEX: {"rate": 0.000023, "fixed": 0.0},
}

EXCHANGE_CN_NAME: dict[Exchange, str] = {
    Exchange.SHFE: "上期所",
    Exchange.INE: "能源中心",
    Exchange.DCE: "大商所",
    Exchange.CZCE: "郑商所",
    Exchange.GFEX: "广期所",
    Exchange.CFFEX: "中金所",
}


@dataclass(frozen=True)
class FutureSpec:
    """单个期货品种的合约规格。"""

    symbol: str            # 品种代码（东财主力连续代码为 f"{symbol}m"）
    name: str              # 中文名称
    exchange: Exchange
    size: float            # 合约乘数
    pricetick: float       # 最小变动价位
    margin_rate: float = 0.10   # 保证金率（回测资金占用参考）

    @property
    def em_symbol(self) -> str:
        """东方财富主力连续代码。"""
        return f"{self.symbol.lower()}m"


# ---------------------------------------------------------------------------
# 品种表（按交易所分组，可直接被 downloader 使用）
# ---------------------------------------------------------------------------

_RAW: list[tuple[str, str, Exchange, float, float]] = [
    # ---------------- 上期所 SHFE ----------------
    ("cu", "沪铜", Exchange.SHFE, 5, 10),
    ("al", "沪铝", Exchange.SHFE, 5, 5),
    ("zn", "沪锌", Exchange.SHFE, 5, 5),
    ("pb", "沪铅", Exchange.SHFE, 5, 5),
    ("ni", "沪镍", Exchange.SHFE, 1, 10),
    ("sn", "沪锡", Exchange.SHFE, 1, 10),
    ("au", "沪金", Exchange.SHFE, 1000, 0.02),
    ("ag", "沪银", Exchange.SHFE, 15, 1),
    ("rb", "螺纹钢", Exchange.SHFE, 10, 1),
    ("wr", "线材", Exchange.SHFE, 10, 1),
    ("hc", "热卷", Exchange.SHFE, 10, 1),
    ("ss", "不锈钢", Exchange.SHFE, 5, 5),
    ("fu", "燃料油", Exchange.SHFE, 10, 1),
    ("bu", "沥青", Exchange.SHFE, 10, 1),
    ("ru", "橡胶", Exchange.SHFE, 10, 5),
    ("sp", "纸浆", Exchange.SHFE, 10, 2),
    ("ao", "氧化铝", Exchange.SHFE, 20, 1),
    ("br", "丁二烯橡胶", Exchange.SHFE, 5, 5),
    ("op", "胶版纸", Exchange.SHFE, 10, 2),
    # ---------------- 能源中心 INE ----------------
    ("sc", "原油", Exchange.INE, 1000, 0.1),
    ("lu", "低硫燃料油", Exchange.INE, 10, 1),
    ("nr", "20号胶", Exchange.INE, 10, 5),
    ("bc", "国际铜", Exchange.INE, 5, 10),
    ("ec", "集运欧线", Exchange.INE, 50, 0.1),
    # ---------------- 大商所 DCE ----------------
    ("a", "豆一", Exchange.DCE, 10, 1),
    ("b", "豆二", Exchange.DCE, 10, 1),
    ("c", "玉米", Exchange.DCE, 10, 1),
    ("cs", "玉米淀粉", Exchange.DCE, 10, 1),
    ("m", "豆粕", Exchange.DCE, 10, 1),
    ("y", "豆油", Exchange.DCE, 10, 2),
    ("p", "棕榈油", Exchange.DCE, 10, 2),
    ("fb", "纤维板", Exchange.DCE, 10, 0.5),
    ("bb", "胶合板", Exchange.DCE, 500, 0.05),
    ("jd", "鸡蛋", Exchange.DCE, 10, 1),
    ("l", "塑料", Exchange.DCE, 5, 1),
    ("v", "PVC", Exchange.DCE, 5, 1),
    ("pp", "聚丙烯", Exchange.DCE, 5, 1),
    ("eg", "乙二醇", Exchange.DCE, 10, 1),
    ("j", "焦炭", Exchange.DCE, 100, 0.5),
    ("jm", "焦煤", Exchange.DCE, 60, 0.5),
    ("i", "铁矿石", Exchange.DCE, 100, 0.5),
    ("rr", "粳米", Exchange.DCE, 10, 1),
    ("lh", "生猪", Exchange.DCE, 16, 5),
    ("lg", "原木", Exchange.DCE, 90, 0.5),
    ("eb", "苯乙烯", Exchange.DCE, 5, 1),
    ("pg", "液化石油气", Exchange.DCE, 20, 1),
    ("bz", "纯苯", Exchange.DCE, 5, 1),
    # ---------------- 郑商所 CZCE ----------------
    ("SR", "白糖", Exchange.CZCE, 10, 1),
    ("CF", "棉花", Exchange.CZCE, 5, 5),
    ("CY", "棉纱", Exchange.CZCE, 5, 5),
    ("TA", "PTA", Exchange.CZCE, 5, 2),
    ("MA", "甲醇", Exchange.CZCE, 10, 1),
    ("FG", "玻璃", Exchange.CZCE, 20, 1),
    ("RM", "菜粕", Exchange.CZCE, 10, 1),
    ("OI", "菜油", Exchange.CZCE, 10, 1),
    ("ZC", "动力煤", Exchange.CZCE, 100, 0.2),
    ("SF", "硅铁", Exchange.CZCE, 5, 2),
    ("SM", "锰硅", Exchange.CZCE, 5, 2),
    ("UR", "尿素", Exchange.CZCE, 20, 1),
    ("SA", "纯碱", Exchange.CZCE, 20, 1),
    ("PF", "短纤", Exchange.CZCE, 5, 2),
    ("PK", "花生", Exchange.CZCE, 5, 2),
    ("SH", "烧碱", Exchange.CZCE, 20, 1),
    ("PX", "对二甲苯", Exchange.CZCE, 5, 2),
    ("AP", "苹果", Exchange.CZCE, 10, 1),
    ("CJ", "红枣", Exchange.CZCE, 5, 5),
    ("JR", "粳稻", Exchange.CZCE, 20, 1),
    ("PM", "普麦", Exchange.CZCE, 50, 1),
    ("RI", "早籼稻", Exchange.CZCE, 20, 1),
    ("WH", "强麦", Exchange.CZCE, 20, 1),
    ("RS", "油菜籽", Exchange.CZCE, 10, 1),
    # ---------------- 广期所 GFEX ----------------
    ("si", "工业硅", Exchange.GFEX, 5, 5),
    ("lc", "碳酸锂", Exchange.GFEX, 1, 20),
    ("ps", "多晶硅", Exchange.GFEX, 3, 5),
    # ---------------- 中金所 CFFEX ----------------
    ("IF", "沪深300", Exchange.CFFEX, 300, 0.2),
    ("IH", "上证50", Exchange.CFFEX, 300, 0.2),
    ("IC", "中证500", Exchange.CFFEX, 200, 0.2),
    ("IM", "中证1000", Exchange.CFFEX, 200, 0.2),
    ("T", "10年国债", Exchange.CFFEX, 10000, 0.005),
    ("TF", "5年国债", Exchange.CFFEX, 10000, 0.005),
    ("TS", "2年国债", Exchange.CFFEX, 20000, 0.005),
    ("TL", "30年国债", Exchange.CFFEX, 10000, 0.01),
]

FUTURES: dict[str, FutureSpec] = {
    code: FutureSpec(code, name, ex, size, tick)
    for code, name, ex, size, tick in _RAW
}


def get_spec(symbol: str) -> FutureSpec:
    """按品种代码取规格，大小写不敏感（支持 _index 指数合约派生）。"""
    key = symbol.strip()
    if key in FUTURES:
        return FUTURES[key]
    lower_map = {k.lower(): v for k, v in FUTURES.items()}
    if key.lower() in lower_map:
        return lower_map[key.lower()]

    # 支持加权指数智能派生，如 rb_index -> 螺纹指数
    if key.lower().endswith("_index"):
        base_code = key[:-6]
        if base_code.lower() in lower_map:
            base_spec = lower_map[base_code.lower()]
            return FutureSpec(
                symbol=key,
                name=f"{base_spec.name}指数",
                exchange=base_spec.exchange,
                size=base_spec.size,
                pricetick=base_spec.pricetick,
            )

    raise KeyError(
        f"未知品种 {symbol!r}；已支持: {sorted(FUTURES)}"
    )


def by_exchange(exchange: Exchange) -> list[FutureSpec]:
    """列出某交易所的全部品种。"""
    return [s for s in FUTURES.values() if s.exchange == exchange]


def all_symbols() -> list[str]:
    """全部品种代码。"""
    return sorted(FUTURES)


if __name__ == "__main__":
    print(f"已登记品种 {len(FUTURES)} 个")
    for ex in EXCHANGE_CN_NAME:
        specs = by_exchange(ex)
        print(f"  {EXCHANGE_CN_NAME[ex]:6s} {len(specs):2d} 个: {[s.symbol for s in specs]}")
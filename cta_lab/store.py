"""vnpy 数据库访问层。

职责：
    1. 初始化 vnpy 的 database.driver / database.database 设置
       （必须在导入 vnpy_sqlite 等驱动前完成）
    2. 把 BarData 落库
    3. 查询本地已有数据概况

vnpy 4.x 的数据库配置存在 SETTINGS 里，驱动模块在 import 时读取，所以
初始化必须走 init_database()。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import DB_TZ, BaseDatabase, get_database
from vnpy.trader.object import BarData

from .contracts import EXCHANGE_CN_NAME

# ---------------------------------------------------------------------------
# 存储性能参数
# ---------------------------------------------------------------------------

#: SQLite PRAGMA 调优。
#:
#: 默认值是为「事务型」负载设计的，对回测这种「批量写入 + 大范围读取」
#: 的分析型负载不合适。各项含义：
#:
#:   journal_mode=WAL     持久项。写入不阻塞读取；事务提交不需 fsync 整个库
#:   synchronous=NORMAL   会话项。WAL 下 NORMAL 已能保证不丢库，比 FULL 快很多
#:   cache_size=-65536    会话项。负数单位为 KiB，即 64MB 页缓存（默认仅 2MB）
#:   temp_store=MEMORY    会话项。排序/临时表走内存，不回写磁盘
#:   mmap_size=256MB      会话项。内存映射读，大幅提升大范围扫描速度
PRAGMAS: dict[str, object] = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "cache_size": -65_536,
    "temp_store": "MEMORY",
    "mmap_size": 268_435_456,
}

#: 默认写入分片大小。
#:
#: vnpy 的 save_bar_data 每被调用一次，除了 upsert 数据，**还会用 COUNT(*)
#: 重算一次 Overview**，而这个 COUNT 随已存数据量线性增长。所以：
#:
#:     分片越小 → COUNT(*) 执行次数越多 → 总体越慢
#:     分片越大 → 单次事务内存占用越高
#:
#: 实测（6 万根 1 分钟线，含一次重写；详见 README 基准数据）：
#:     chunk=5000  重写 21.55s
#:     chunk=60000 重写  7.86s   ← 快 2.7 倍
#:
#: 因此默认取一个较大的值。只有当你需要写入千万级以上的单批数据、
#: 且担心单事务内存/WAL 体积时才需调小。
DEFAULT_CHUNK_SIZE: int = 100_000

#: vnpy bar 表**原生支持**的周期。
#:
#: 注意:Interval 枚举只有这 5 个值，**没有 5m/15m/30m**。
#: 非原生粒度若强行以其他周期入表，会撞唯一索引造成数据互相污染。
NATIVE_BAR_INTERVALS: frozenset[Interval] = frozenset({
    Interval.MINUTE,
    Interval.HOUR,
    Interval.DAILY,
    Interval.WEEKLY,
})


def is_native_interval(interval) -> bool:
    """该周期能否安全存入 vnpy 的 bar 表。

    同时接受 Interval 枚举与其字符串值（如 ``'1m'``），因为 vnpy 的
    ``save_bar_data`` 会把调用方对象的 interval 改写成字符串。
    """
    if isinstance(interval, Interval):
        return interval in NATIVE_BAR_INTERVALS
    try:
        return Interval(interval) in NATIVE_BAR_INTERVALS
    except (ValueError, TypeError):
        return False


def as_interval(value) -> Interval:
    """把枚举或字符串统一成 Interval。"""
    return value if isinstance(value, Interval) else Interval(value)


def assert_native_interval(interval) -> None:
    """非原生周期直接报错，避免静默污染回测数据。"""
    if is_native_interval(interval):
        return
    raise ValueError(
        f"周期 {interval!r} 不是 vnpy 原生周期，不能写入 bar 表。\n"
        f"  原生周期: {sorted(i.value for i in NATIVE_BAR_INTERVALS)}\n"
        "  5m/15m/30m 请改用 cta_lab.altstore 的 CSV/Parquet 分析通道，\n"
        "  或把粒度编码进 symbol（例如 rb_5m）后再入表。"
    )


def apply_pragmas(
    db: BaseDatabase | None = None,
    pragmas: dict | None = None,
    verbose: bool = False,
    strict: bool = True,
) -> dict:
    """对底层 peewee 连接应用 PRAGMA 调优，返回实际生效值。

    vnpy_sqlite 在**模块导入时**就建好了连接且未设 PRAGMA，因此这里直接
    改底层 peewee 对象的 PRAGMA 属性并强制重连使其生效。

    兼容性要点（踩过的坑）：
        peewee 3.x 把 pragmas 存在 ``.pragmas``
        peewee 4.x 改名成私有 ``._pragmas``
    写错属性名会**静默失效**，所以这里两个都设，并在重连后回读校验。

    journal_mode 是持久项（写入库文件头），其余是会话项（每次连接生效）。
    """
    peewee_db = getattr(db or get_database(), "db", None)
    if peewee_db is None:
        if verbose:
            print("  当前驱动不是 SQLite，跳过 PRAGMA 调优")
        return {}

    source = PRAGMAS if pragmas is None else pragmas
    # peewee 的 _set_pragmas 按 (key, value) 对迭代，list-of-pairs 最稳妥
    pairs = [(k, v) for k, v in source.items()]

    for attr in ("pragmas", "_pragmas"):
        if attr == "_pragmas" or hasattr(peewee_db, attr):
            setattr(peewee_db, attr, pairs)

    # 强制重连，让 pragmas 在 _add_conn_hooks 中生效
    try:
        peewee_db.close()
    except Exception:  # noqa: BLE001 - 未连接时关闭会抛异常，忽略
        pass
    peewee_db.connect()

    # 回读校验（防止属性名/版本差异导致静默失效）
    effective = {k: read_pragma(db, k) for k in source}
    failed = [
        k for k, want in source.items()
        if not _pragma_matches(effective.get(k), want)
    ]

    if verbose:
        print("  PRAGMA 已应用:")
        for k, v in effective.items():
            flag = "" if _pragma_matches(v, source[k]) else "  <-- 未生效!"
            print(f"    {k:14s} = {str(v):>10s}   (期望 {source[k]}){flag}")

    if failed and strict:
        raise RuntimeError(
            f"PRAGMA 未能生效: {failed}；当前值 {effective}。\n"
            "  常见原因: peewee 版本不同导致属性名变化，"
            "或数据库已被其他连接占用。"
        )
    return effective


def _pragma_matches(actual, expected) -> bool:
    """PRAGMA 回读值可能与期望写法不同（如 WAL/wal、NORMAL/1）。"""
    if actual is None:
        return False
    a, e = str(actual).strip().lower(), str(expected).strip().lower()
    if a == e:
        return True
    # 布尔/枚举别名: NORMAL<->1, FULL<->2, OFF<->0
    alias = {"normal": "1", "full": "2", "off": "0", "memory": "2"}
    return alias.get(e, e) == a or alias.get(a, a) == e


def read_pragma(db: BaseDatabase | None, name: str):
    """读取当前连接的 PRAGMA 实际值。"""
    peewee_db = getattr(db or get_database(), "db", None)
    if peewee_db is None:
        return None
    try:
        cur = peewee_db.cursor()
        return cur.execute(f"PRAGMA {name}").fetchone()[0]
    except Exception:  # noqa: BLE001
        return None


@dataclass
class OverviewRow:
    """本地数据概况行。"""

    symbol: str
    exchange: Exchange
    interval: Interval
    count: int
    start: datetime
    end: datetime

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"

    @property
    def exchange_cn(self) -> str:
        return EXCHANGE_CN_NAME.get(self.exchange, self.exchange.value)

    def __str__(self) -> str:
        return (
            f"{self.vt_symbol:12s} {self.exchange_cn:5s} {self.interval.value:4s} "
            f"{self.count:7d} 根  "
            f"{self.start.strftime('%Y-%m-%d')} -> {self.end.strftime('%Y-%m-%d')}"
        )


def init_database(
    db_type: str = "sqlite",
    db_name: str = "futures_lab.db",
    db_path: str | Path | None = None,
    tune_pragmas: bool = True,
    verbose: bool = False,
) -> BaseDatabase:
    """初始化 vnpy 数据库并返回实例。

    参数
    ----
    db_type  : 'sqlite' 或 'duckdb'
    db_name  : 数据库文件名（默认解析到 vnpy 的 ~/.vntrader/ 目录）
    db_path  : 显式指定完整（绝对）路径，优先级高于 db_name
    tune_pragmas : 是否对 SQLite 应用 PRAGMA 调优（默认是）

    实现要点
    --------
    vnpy 的数据库驱动（vnpy_sqlite / vnpy_duckdb）在**模块被 import 时**
    就把 SETTINGS['database.database'] 读进模块级变量并建好连接。
    如果 driver 模块已经在 sys.modules 里（例如先 import 了 vnpy_sqlite），
    单纯修改 SETTINGS 是不生效的。
    因此这里需要：
        1. 写入 SETTINGS
        2. reload 已加载的驱动模块（若有）
        3. 清空 get_database() 的全局缓存后重新获取
        4. 应用 PRAGMA 调优
    """
    import importlib
    import sys as _sys

    from vnpy.trader.setting import SETTINGS

    if db_path is not None:
        target = Path(db_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS["database.database"] = str(target)
    else:
        SETTINGS["database.database"] = db_name

    # vnpy 4.x 用 database.name 决定驱动模块名（vnpy_<name>）
    SETTINGS["database.name"] = db_type

    # 1) reload 已在内存中的驱动模块，让其重新读取 SETTINGS
    driver_root = f"vnpy_{db_type}"
    for mod_name in list(_sys.modules):
        if mod_name == driver_root or mod_name.startswith(driver_root + "."):
            try:
                importlib.reload(_sys.modules[mod_name])
            except Exception:  # noqa: BLE001 - reload 失败不阻断，后续 get_database 会兜底
                pass

    # 2) 清空 get_database() 的全局缓存
    from vnpy.trader import database as _db_module

    _db_module.database = None

    database = _db_module.get_database()

    # 3) PRAGMA 调优（仅 SQLite 有效）
    if tune_pragmas:
        apply_pragmas(database, verbose=verbose)

    return database


def save_bars(
    bars: list[BarData],
    db: BaseDatabase | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    stream: bool = False,
    protect_caller: bool = True,
) -> int:
    """批量写入 K 线，返回写入根数。

    chunk_size 说明（实测结论，与直觉相反）
    ---------------------------------------
    vnpy 的 save_bar_data 每被调用一次，除了 upsert 数据，还会用 COUNT(*)
    重算一次 Overview，而这个 COUNT 随已存数据量线性增长。因此：

        分片越小 → COUNT(*) 次数越多 → 总体越慢（不是越快！）
        分片越大 → 单次事务内存占用越高

    实测 6 万根 1 分钟线的一次重写：chunk=5000 需 21.55s，chunk=60000 仅 7.86s。
    默认取 DEFAULT_CHUNK_SIZE(100000)，只有写千万级单批数据时才建议调小。

    protect_caller 说明（重要）
    --------------------------
    vnpy 的 ``save_bar_data`` 会**破坏性地改写调用方传入的 BarData 对象**:

        d = bar.__dict__                    # 这是引用，不是副本！
        d["exchange"] = d["exchange"].value  # 枚举 -> 字符串
        d["interval"] = d["interval"].value  # 枚举 -> 字符串
        bar.datetime = convert_tz(...)      # 丢失时区（aware -> naive）
        d.pop("gateway_name")               # 删除属性
        d.pop("vt_symbol")                  # 删除属性

    后果：
        1. 同一批数据二次保存 → 字符串再取 .value 直接报 AttributeError
        2. 保存后用这批 bar 做分析/绘图 → **静默丢时区**，时间偏移 8 小时

    protect_caller=True（默认）会先浅拷贝每个 bar 再交给 vnpy，
    使调用方对象保持干净。开销约 1~2 微秒/根，10 万根约 0.1~0.2 秒。
    只有在你确定不再使用这批对象、且追求极限写入速度时才关掉。
    """
    if not bars:
        return 0

    # 防御：非原生周期不允许入表（避免撞唯一索引互相污染）
    bad = {str(b.interval) for b in bars if not is_native_interval(b.interval)}
    if bad:
        raise ValueError(
            f"待写入数据含非原生周期 {sorted(bad)}；"
            "vnpy bar 表只支持 1m/1h/d/w。\n"
            "  提示：若这是二次保存同一批对象，根因是 vnpy 改写了 interval；"
            "  保持 protect_caller=True 即可避免。"
        )

    database = db or get_database()

    # 保护调用方对象不被 vnpy 改写
    payload = [copy.copy(b) for b in bars] if protect_caller else bars

    total = 0
    for i in range(0, len(payload), chunk_size):
        chunk = payload[i: i + chunk_size]
        database.save_bar_data(chunk, stream=stream)
        total += len(chunk)
    return total


def list_overview(db: BaseDatabase | None = None) -> list[OverviewRow]:
    """列出本地已有的全部 K 线概况，按 vt_symbol 排序。"""
    database = db or get_database()
    rows = []
    for ov in database.get_bar_overview():
        rows.append(
            OverviewRow(
                symbol=ov.symbol,
                exchange=Exchange(ov.exchange),
                interval=Interval(ov.interval),
                count=int(ov.count),
                start=ov.start,
                end=ov.end,
            )
        )
    rows.sort(key=lambda r: (r.exchange.value, r.symbol, r.interval.value))
    return rows


def has_data(
    symbol: str,
    exchange: Exchange,
    interval: Interval = Interval.DAILY,
    db: BaseDatabase | None = None,
) -> bool:
    """检查本地是否已有该品种数据。"""
    target = f"{symbol}.{exchange.value}"
    for row in list_overview(db):
        if row.vt_symbol == target and row.interval == interval:
            return True
    return False


def count_bars(
    symbol: str,
    exchange: Exchange,
    interval: Interval = Interval.DAILY,
    db: BaseDatabase | None = None,
) -> int:
    """统计本地某品种 K 线根数。"""
    target = f"{symbol}.{exchange.value}"
    for row in list_overview(db):
        if row.vt_symbol == target and row.interval == interval:
            return row.count
    return 0


def delete_bars(
    symbol: str,
    exchange: Exchange,
    interval: Interval = Interval.DAILY,
    db: BaseDatabase | None = None,
) -> int:
    """删除本地某品种 K 线，返回删除行数。"""
    database = db or get_database()
    return int(database.delete_bar_data(symbol, exchange, interval))


__all__ = [
    "OverviewRow",
    "PRAGMAS",
    "DEFAULT_CHUNK_SIZE",
    "NATIVE_BAR_INTERVALS",
    "is_native_interval",
    "as_interval",
    "assert_native_interval",
    "apply_pragmas",
    "read_pragma",
    "init_database",
    "save_bars",
    "list_overview",
    "has_data",
    "count_bars",
    "delete_bars",
    "DB_TZ",
]
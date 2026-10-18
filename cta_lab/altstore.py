"""非原生周期的分析专用存储通道。

为什么需要这个模块
------------------
vnpy 的 `Interval` 枚举只有 ``1m / 1h / d / w / tick``，**没有 5m/15m/30m**：

.. code-block:: python

    Interval("5m")  -> ValueError: '5m' is not a valid Interval

而 vnpy bar 表的唯一索引是 ``(symbol, exchange, interval, datetime)``。
如果把 5 分钟线硬塞进 ``interval='1m'``，它的整点时刻（如 ``21:05``）
会和真正的 1 分钟线在同一天、同一时刻**撞唯一索引并被覆盖** ——
两个粒度的数据混在一个桶里互相污染。

因此非原生粒度走本模块：**列式文件，不进 vnpy 的 bar 表**。
好处：
    - 零污染风险（物理隔离）
    - Parquet 字典+增量编码后约 15~25 字节/行，比 SQLite 的 ~115 字节小 5 倍以上
    - 可直接被 pandas / DuckDB / polars 读取做向量化研究

适用场景：日内研究、因子计算、参数扫描。
不适用的场景：**vnpy 回测引擎**（它只认 native interval）。
如果确实要用 5m 做 vnpy 回测，请改用「粒度编码进 symbol」方案
（例如把 symbol 存成 ``rb_5m``，仍用 ``Interval.MINUTE`` 入表）。
"""

from __future__ import annotations

from pathlib import Path

from vnpy.trader.constant import Exchange

from .bootstrap import PROJECT_ROOT
from .downloader import RawBar

#: 非原生粒度根目录（项目内，便于随项目打包/清理）
ALT_ROOT: Path = PROJECT_ROOT / "data" / "alt"

#: 支持的非原生粒度（分钟）
ALT_GRANULARITIES: frozenset[int] = frozenset({5, 15, 30})


def alt_dir(
    symbol: str,
    exchange: Exchange,
    minutes: int,
    root: Path | None = None,
) -> Path:
    """返回该 (品种, 交易所, 粒度) 的数据目录。"""
    base = Path(root) if root else ALT_ROOT
    return base / exchange.value / f"{symbol.lower()}_{minutes}m"


def alt_file(
    symbol: str,
    exchange: Exchange,
    minutes: int,
    root: Path | None = None,
) -> Path:
    """返回数据文件路径；优先 Parquet，无 pyarrow 时退化为 CSV。"""
    directory = alt_dir(symbol, exchange, minutes, root)
    if _has_pyarrow():
        return directory / "bars.parquet"
    return directory / "bars.csv"


def _has_pyarrow() -> bool:
    import importlib.util

    return importlib.util.find_spec("pyarrow") is not None


def _to_frame(bars: list[RawBar]):
    """RawBar 列表 -> DataFrame。"""
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


def save_alt_bars(
    bars: list[RawBar],
    symbol: str,
    exchange: Exchange,
    minutes: int,
    root: Path | None = None,
    merge: bool = True,
    verbose: bool = True,
) -> Path:
    """把非原生粒度 K 线写入列式文件。

    merge=True 时会与已有文件按 datetime 合并去重（后者覆盖前者），
    实现增量更新。
    """
    if minutes not in ALT_GRANULARITIES:
        raise ValueError(
            f"不支持的粒度 {minutes}m，可选 {sorted(ALT_GRANULARITIES)}。\n"
            "  1m/1h/d 属于 vnpy 原生周期，请直接用 store.save_bars() 入库。"
        )
    if not bars:
        raise ValueError("bars 为空")

    import pandas as pd

    path = alt_file(symbol, exchange, minutes, root)
    path.parent.mkdir(parents=True, exist_ok=True)

    df = _to_frame(bars)

    if merge and path.exists():
        old = load_alt_bars(symbol, exchange, minutes, root)
        df = (
            pd.concat([old, df], ignore_index=True)
            .drop_duplicates(subset="datetime", keep="last")
            .sort_values("datetime")
            .reset_index(drop=True)
        )

    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False, encoding="utf-8-sig")

    if verbose:
        size_kb = path.stat().st_size / 1024
        bytes_per_row = path.stat().st_size / max(len(df), 1)
        print(
            f"[alt] {symbol.upper()}.{exchange.value} {minutes}m -> {len(df)} 行  "
            f"{size_kb:.1f} KB  ({bytes_per_row:.1f} 字节/行)  {path}"
        )
    return path


def load_alt_bars(
    symbol: str,
    exchange: Exchange,
    minutes: int,
    root: Path | None = None,
):
    """读取非原生粒度 K 线，返回 DataFrame。"""
    import pandas as pd

    directory = alt_dir(symbol, exchange, minutes, root)
    parquet = directory / "bars.parquet"
    csv = directory / "bars.csv"

    if parquet.exists():
        df = pd.read_parquet(parquet)
    elif csv.exists():
        df = pd.read_csv(csv, parse_dates=["datetime"])
    else:
        raise FileNotFoundError(
            f"没有 {symbol}.{exchange.value} {minutes}m 的数据。\n"
            f"  请先执行: python -m cta_lab.dataio --alt {symbol} --minutes {minutes}"
        )

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def list_alt(root: Path | None = None) -> list[dict]:
    """列出所有已保存的非原生粒度数据。"""
    base = Path(root) if root else ALT_ROOT
    out: list[dict] = []

    if not base.exists():
        return out

    for file in sorted(base.glob("*/*m/bars.*")):
        exchange_name = file.parent.parent.name
        stem = file.parent.name                     # 例如 rb_5m
        if "_" not in stem:
            continue
        symbol, gran = stem.rsplit("_", 1)
        out.append({
            "symbol": symbol.upper(),
            "exchange": exchange_name,
            "minutes": int(gran.rstrip("m")),
            "format": file.suffix.lstrip("."),
            "size_kb": file.stat().st_size / 1024,
            "path": file,
        })
    return out


def format_name(minutes: int) -> str:
    """粒度的人可读名。"""
    return f"{minutes}m"


__all__ = [
    "ALT_ROOT",
    "ALT_GRANULARITIES",
    "alt_dir",
    "alt_file",
    "save_alt_bars",
    "load_alt_bars",
    "list_alt",
    "format_name",
]

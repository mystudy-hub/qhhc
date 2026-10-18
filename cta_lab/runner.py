"""Headless 回测运行器（不依赖任何 GUI）。

封装 vnpy 4.x 的 BacktestingEngine，提供：
    - 从本地数据库自动推断合约规格（乘数/最小变动价位）
    - 一次回测 + 绩效指标输出
    - 参数网格寻优
    - 结果导出（逐日盈亏 CSV + 交易明细 CSV + 权益曲线 SVG）

用法::

    from cta_lab.runner import run_backtest
    run_backtest("DoubleMaStrategy", "rb.SHFE", ...)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import TradeData

from .contracts import EXCHANGE_FEE, FutureSpec, get_spec
from .store import init_database, list_overview

# 延迟导入以避免循环依赖（ai 模块会反向引用 runner.infer_spec）
def trade_statistics(trades, config=None):
    from .ai import trade_statistics as _ts
    return _ts(trades, config)


# ---------------------------------------------------------------------------
# 默认回测参数
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """回测参数集合。"""

    vt_symbol: str = "rb.SHFE"
    start: datetime = datetime(2018, 1, 1)
    end: datetime = datetime(2026, 10, 1)
    interval: Interval = Interval.DAILY

    capital: int = 1_000_000       # 初始资金
    risk_free: float = 0.02        # 无风险利率（年化）
    annual_days: int = 240         # 年化交易日数（国内商品约 240~245）
    half_life: int = 120           # 夏普比率的半衰期参数

    rate: float | None = None      # 手续费率（按成交金额），None = 自动
    slippage: float | None = None  # 滑点（价格单位），None = 自动
    size: float | None = None      # 合约乘数，None = 自动
    pricetick: float | None = None # 最小变动价位，None = 自动

    db_name: str = "futures_lab.db"

    @property
    def symbol(self) -> str:
        return self.vt_symbol.split(".")[0]

    @property
    def exchange(self) -> Exchange:
        return Exchange(self.vt_symbol.split(".")[1])


@dataclass
class BacktestResult:
    """回测输出。"""

    config: BacktestConfig
    statistics: dict[str, Any] = field(default_factory=dict)
    daily_df: Any = None
    trades: list[TradeData] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    output_dir: Path | None = None

    # -- 便捷取值 -----------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.statistics.get(key, default)

    @property
    def total_return(self) -> float:
        """总收益率（百分数）。"""
        return float(self.get("total_return", 0.0))

    @property
    def annual_return(self) -> float:
        """年化收益率（百分数）。"""
        return float(self.get("annual_return", 0.0))

    @property
    def max_drawdown_percent(self) -> float:
        """最大回撤（百分数，负值）。"""
        return float(self.get("max_ddpercent", 0.0))

    @property
    def sharpe_ratio(self) -> float:
        return float(self.get("sharpe_ratio", 0.0))

    @property
    def trade_count(self) -> int:
        return int(self.get("total_trade_count", len(self.trades)))

    def trade_stats(self) -> dict:
        """按开平配对的交易层面统计。"""
        return trade_statistics(self.trades, self.config)

    def summary(self) -> str:
        """人类可读的绩效摘要。

        注意 vnpy 4.x 的口径：
            total_return / annual_return / max_ddpercent 均为**百分数**
            （如 -0.47 表示 -0.47%），不是小数。
        """
        s = self.statistics
        trades = trade_statistics(self.trades, self.config)

        banner: list[str] = []
        if s.get("liquidated"):
            banner = [
                "!" * 62,
                "  ⚠ 回测期间已爆仓（权益跌至 0 以下）",
                "    因此下方收益/回撤/夏普均为 0——那不是「策略没交易」，",
                "    而是 vnpy 在非正权益时静默归零。",
                f"    最低权益 {s.get('min_balance', 0):,.2f}   "
                f"首次爆仓 {s.get('liquidation_date', '')}",
                f"    估算名义杠杆 {s.get('est_leverage', 0)} 倍（本金 {s.get('capital', 0):,.0f}）",
                "    解决：调小 fixed_size，或调高 capital。",
                "!" * 62,
            ]
        elif float(s.get("est_leverage", 0) or 0) > 3:
            banner = [
                "-" * 62,
                f"  ⚠ 名义杠杆约 {s.get('est_leverage')} 倍，偏高。",
                "    vnpy 回测不校验保证金，实盘中可能被强平。",
                "-" * 62,
            ]

        lines = banner + [
            "=" * 62,
            f"  回测报告  {self.config.vt_symbol}  "
            f"{self.config.start:%Y-%m-%d} ~ {self.config.end:%Y-%m-%d}",
            "=" * 62,
            f"  期初资金        {s.get('capital', 0):>16,.0f}",
            f"  期末权益        {s.get('end_balance', 0):>16,.2f}",
            f"  总收益率        {s.get('total_return', 0):>15.2f}%",
            f"  年化收益率      {s.get('annual_return', 0):>15.2f}%",
            f"  最大回撤金额    {s.get('max_drawdown', 0):>16,.2f}",
            f"  最大回撤        {s.get('max_ddpercent', 0):>15.2f}%",
            f"  最大回撤天数    {s.get('max_drawdown_duration', 0):>16d}",
            f"  夏普比率        {s.get('sharpe_ratio', 0):>16.4f}",
            f"  EWM 夏普        {s.get('ewm_sharpe', 0):>16.4f}",
            f"  收益回撤比      {s.get('return_drawdown_ratio', 0):>16.4f}",
            f"  RGR Ratio       {s.get('rgr_ratio', 0):>16.4f}",
            "-" * 62,
            f"  回测交易日      {s.get('total_days', 0):>16d}",
            f"  盈利交易日      {s.get('profit_days', 0):>16d}",
            f"  亏损交易日      {s.get('loss_days', 0):>16d}",
            f"  日均盈亏        {s.get('daily_net_pnl', 0):>16,.2f}",
            f"  总盈亏(净)      {s.get('total_net_pnl', 0):>16,.2f}",
            f"  总手续费        {s.get('total_commission', 0):>16,.2f}",
            f"  总滑点          {s.get('total_slippage', 0):>16,.2f}",
            f"  总成交额        {s.get('total_turnover', 0):>16,.2f}",
            f"  总成交笔数      {s.get('total_trade_count', 0):>16d}",
            "-" * 62,
            "  交易层面（按开平配对计算）",
            f"    完成回合      {trades.get('round_trips', 0):>16d}",
            f"    盈利回合      {trades.get('win_count', 0):>16d}",
            f"    亏损回合      {trades.get('loss_count', 0):>16d}",
            f"    胜率          {trades.get('win_rate', 0):>15.2f}%",
            f"    盈利因子      {trades.get('profit_factor', 0):>16.4f}",
            f"    平均盈利      {trades.get('avg_win', 0):>16,.2f}",
            f"    平均亏损      {trades.get('avg_loss', 0):>16,.2f}",
            f"    盈亏比        {trades.get('payoff_ratio', 0):>16.4f}",
            f"    单笔期望      {trades.get('expectancy', 0):>16,.2f}",
            f"    最大单笔盈利  {trades.get('max_win', 0):>16,.2f}",
            f"    最大单笔亏损  {trades.get('max_loss', 0):>16,.2f}",
            f"    平均持仓天数  {trades.get('avg_holding_days', 0):>16.2f}",
            "=" * 62,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 规格推断
# ---------------------------------------------------------------------------

def infer_spec(config: BacktestConfig) -> tuple[float, float, float, float]:
    """推断 (size, pricetick, rate, slippage)。

    优先级：显式配置 > contracts 登记 > 兜底默认值。
    """
    symbol = config.symbol
    exchange = config.exchange

    size: float | None = config.size
    pricetick: float | None = config.pricetick

    if size is None or pricetick is None:
        try:
            spec: FutureSpec = get_spec(symbol)
            if size is None:
                size = spec.size
            if pricetick is None:
                pricetick = spec.pricetick
        except KeyError:
            # 未登记品种（如具体合约 rb2610）：从合约代码里剥出品种前缀
            base = "".join(ch for ch in symbol if ch.isalpha()).lower()
            try:
                spec = get_spec(base)
                if size is None:
                    size = spec.size
                if pricetick is None:
                    pricetick = spec.pricetick
            except KeyError:
                if size is None:
                    size = 10.0
                if pricetick is None:
                    pricetick = 1.0

    fee = EXCHANGE_FEE.get(exchange, {"rate": 0.0, "fixed": 0.0})
    rate = config.rate if config.rate is not None else float(fee["rate"])

    # 滑点默认取 1 个最小变动价位（保守但有代表性）
    slippage = (
        config.slippage
        if config.slippage is not None
        else float(pricetick)
    )

    return float(size), float(pricetick), float(rate), float(slippage)


def check_local_data(config: BacktestConfig, db=None) -> tuple[bool, str]:
    """检查本地是否有该品种数据，返回 (是否存在, 提示信息)。"""
    rows = list_overview(db)
    target = config.vt_symbol
    for row in rows:
        if row.vt_symbol == target and row.interval == config.interval:
            return True, (
                f"本地已有 {target} {config.interval.value} 数据 "
                f"{row.count} 根（{row.start:%Y-%m-%d} ~ {row.end:%Y-%m-%d}）"
            )
    return False, (
        f"本地缺少 {target} {config.interval.value} 数据，请先执行：\n"
        f"    python -m cta_lab.dataio {config.symbol}"
    )


# ---------------------------------------------------------------------------
# 引擎构建与运行
# ---------------------------------------------------------------------------

def build_engine(
    strategy_class: type,
    setting: dict,
    config: BacktestConfig,
    db=None,
):
    """构建并初始化 BacktestingEngine（不含数据加载）。"""
    from vnpy_ctastrategy.backtesting import BacktestingEngine
    from vnpy_ctastrategy.base import BacktestingMode

    size, pricetick, rate, slippage = infer_spec(config)

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=config.vt_symbol,
        interval=config.interval,
        start=config.start,
        end=config.end,
        rate=rate,
        slippage=slippage,
        size=size,
        pricetick=pricetick,
        capital=config.capital,
        mode=BacktestingMode.BAR,
        risk_free=config.risk_free,
        annual_days=config.annual_days,
        half_life=config.half_life,
    )
    engine.add_strategy(strategy_class, setting)
    return engine


def run_backtest(
    strategy_class: type,
    setting: dict | None = None,
    config: BacktestConfig | None = None,
    db=None,
    *,
    quiet: bool = False,
    export: bool = True,
    output_dir: str | Path | None = None,
) -> BacktestResult:
    """执行一次完整回测。

    参数
    ----
    strategy_class : CtaTemplate 子类
    setting        : 策略参数字典
    config         : 回测配置
    quiet          : True 时抑制引擎日志输出
    export         : 是否导出 CSV / SVG 结果文件
    """
    config = config or BacktestConfig()
    setting = setting or {}

    if db is None:
        db = init_database(db_name=config.db_name)

    ok, msg = check_local_data(config, db)
    if not ok:
        raise RuntimeError(msg)
    if not quiet:
        print(msg)

    engine = build_engine(strategy_class, setting, config, db)

    if quiet:
        engine.output = lambda *a, **k: None  # type: ignore[method-assign]

    engine.load_data()
    engine.run_backtesting()
    daily_df = engine.calculate_result()
    statistics = engine.calculate_statistics(daily_df, output=not quiet)

    # ------------------------------------------------------------------
    # 爆仓 / 杠杆检测
    #
    # vnpy 的回测引擎**不做保证金校验**：只要信号触发就按 fixed_size 下单。
    # 若合约乘数大（如 IF 乘数 300，一手名义约 120 万），5 手就是 600 万
    # 名义金额压在 100 万本金上，一次反向波动就会把权益打成负数。
    #
    # 而 vnpy 的 calculate_statistics 遇到非正权益时，会**静默把所有
    # 统计指标置为 0**（只在一行日志里提一句「回测中出现爆仓」）。
    # 结果就是界面上显示「年化 0.00%」，让人误以为策略是空仓的。
    # 这里把真实情况显式暴露出来。
    # ------------------------------------------------------------------
    stats = dict(statistics or {})
    if daily_df is not None and len(daily_df) > 0 and "balance" in daily_df:
        min_balance = float(daily_df["balance"].min())
        end_balance = float(daily_df["balance"].iloc[-1])
        stats["min_balance"] = min_balance
        stats["liquidated"] = bool(min_balance <= 0 or end_balance <= 0)
        if stats["liquidated"]:
            stats["liquidation_date"] = str(
                daily_df.index[daily_df["balance"] <= 0][0]
            ) if (daily_df["balance"] <= 0).any() else ""
    else:
        stats["liquidated"] = False

    # 估算名义杠杆（按回测首日收盘价 × fixed_size）
    leverage = _estimate_leverage(engine, setting, config)
    stats["est_leverage"] = leverage

    result = BacktestResult(
        config=config,
        statistics=stats,
        daily_df=daily_df,
        trades=list(engine.get_all_trades()),
        logs=list(engine.logs),
    )

    if export:
        out = Path(output_dir) if output_dir else Path.cwd() / "results"
        result.output_dir = export_result(result, out, strategy_class.__name__)

    return result


def _estimate_leverage(engine, setting: dict, config: BacktestConfig) -> float:
    """估算名义杠杆 = 一手名义金额 × 手数 / 本金。

    vnpy 回测不校验保证金，杠杆过高会直接爆仓。
    """
    try:
        size, _, _, _ = infer_spec(config)
        lots = float(setting.get("fixed_size", 1) or 1)
        bars = engine.history_data
        if not bars:
            return 0.0
        price = float(bars[-1].close_price)
        notional = price * size * lots
        return round(notional / max(config.capital, 1), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def run_grid(
    strategy_class: type,
    param_grid: dict[str, list],
    config: BacktestConfig | None = None,
    db=None,
    *,
    metric: str = "sharpe_ratio",
    quiet: bool = True,
) -> list[tuple[dict, dict]]:
    """参数网格穷举，返回 [(参数, 统计指标)]，按 metric 降序。"""
    import itertools

    config = config or BacktestConfig()
    if db is None:
        db = init_database(db_name=config.db_name)

    keys = list(param_grid)
    combos = list(itertools.product(*(param_grid[k] for k in keys)))
    total = len(combos)
    print(f"网格寻优：{total} 组参数，目标指标 = {metric}")

    rows: list[tuple[dict, dict]] = []
    for idx, values in enumerate(combos, 1):
        setting = dict(zip(keys, values))
        try:
            res = run_backtest(
                strategy_class, setting, config, db,
                quiet=quiet, export=False,
            )
            rows.append((setting, res.statistics))
            score = res.statistics.get(metric, float("-inf"))
            print(f"  [{idx:4d}/{total}] {setting}  {metric}={score:.4f}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{idx:4d}/{total}] {setting}  失败: {type(exc).__name__}: {exc}")

    rows.sort(key=lambda r: r[1].get(metric, float("-inf")), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# 结果导出
# ---------------------------------------------------------------------------

_ZH_HEADERS = {
    "date": "日期",
    "trade_count": "成交笔数",
    "turnover": "成交额",
    "commission": "手续费",
    "slippage": "滑点",
    "trading_pnl": "交易盈亏",
    "holding_pnl": "持仓盈亏",
    "total_pnl": "总盈亏",
    "net_pnl": "净盈亏",
    "close_price": "收盘价",
    "pre_close": "昨收",
    "start_pos": "期初持仓",
    "end_pos": "期末持仓",
}


def _jsonable(value):
    """把 numpy / pandas 标量转成原生 JSON 类型。

    注意：vnpy 的 statistics 里 total_days / total_trade_count 等是 numpy
    整数（np.int64），它们不是 Python int 的实例，必须用 numbers 抽象基类
    或 .item() 处理，否则会被降级成字符串。
    """
    if value is None or isinstance(value, (str, bool)):
        return value

    # numpy 标量（np.int64 / np.float64 / np.bool_ ...）
    item = getattr(value, "item", None)
    if callable(item) and getattr(value, "shape", None) == ():
        try:
            return value.item()
        except (ValueError, TypeError):
            pass

    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # 非有限值 JSON 无法表达，转成字符串
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value

    return str(value)


def export_result(
    result: BacktestResult,
    out_dir: Path,
    strategy_name: str,
) -> Path:
    """导出：绩效 JSON / 逐日盈亏 CSV / 交易明细 CSV / 权益曲线 SVG。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"{strategy_name}_{result.config.vt_symbol.replace('.', '_')}_{stamp}"

    # 1) 绩效 JSON
    import json

    stats_path = out_dir / f"{tag}_stats.json"
    serializable = {k: _jsonable(v) for k, v in result.statistics.items()}
    stats_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2) 逐日盈亏 CSV
    daily_path = out_dir / f"{tag}_daily.csv"
    if result.daily_df is not None and len(result.daily_df) > 0:
        df = result.daily_df.copy()
        df.rename(columns=_ZH_HEADERS, inplace=True)
        df.to_csv(daily_path, encoding="utf-8-sig")

    # 3) 交易明细 CSV
    trades_path = out_dir / f"{tag}_trades.csv"
    with trades_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["时间", "合约", "方向", "开平", "价格", "数量", "手续费", "滑点"]
        )
        for t in result.trades:
            writer.writerow([
                t.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                t.vt_symbol,
                t.direction.value,
                t.offset.value,
                t.price,
                t.volume,
                getattr(t, "commission", 0.0),
            ])

    # 4) 权益曲线 SVG（纯 Python 生成，不依赖 matplotlib / GUI）
    if result.daily_df is not None and len(result.daily_df) > 1:
        svg_path = out_dir / f"{tag}_equity.svg"
        _write_equity_svg(result.daily_df, svg_path, result.config)

    print(f"\n结果已导出 -> {out_dir}")
    print(f"  绩效指标: {stats_path.name}")
    if daily_path.exists():
        print(f"  逐日盈亏: {daily_path.name}")
    print(f"  交易明细: {trades_path.name}")
    return out_dir


def _write_equity_svg(daily_df, path: Path, config: BacktestConfig) -> None:
    """用 f-string 手写 SVG 权益曲线（避免重量级绘图依赖）。"""
    balance = daily_df["balance"].astype(float).tolist()
    dates = [d.strftime("%Y-%m-%d") for d in daily_df.index]

    n = len(balance)
    w, h = 1200, 480
    pad_l, pad_r, pad_t, pad_b = 78, 26, 46, 58
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    lo, hi = min(balance), max(balance)
    span = (hi - lo) or 1.0
    lo -= span * 0.06
    hi += span * 0.06
    span = hi - lo

    def sx(i: int) -> float:
        return pad_l + (plot_w * i / max(n - 1, 1))

    def sy(v: float) -> float:
        return pad_t + plot_h * (1 - (v - lo) / span)

    pts = " ".join(f"{sx(i):.2f},{sy(v):.2f}" for i, v in enumerate(balance))

    # 基准线（初始资金）
    base_y = sy(config.capital)

    # 网格与刻度
    grid, labels = [], []
    for k in range(5):
        val = lo + span * k / 4
        y = sy(val)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.2f}" x2="{pad_l + plot_w}" '
            f'y2="{y:.2f}" stroke="#e6e6e6" stroke-width="1"/>'
        )
        labels.append(
            f'<text x="{pad_l - 8}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-size="12" fill="#666">{val:,.0f}</text>'
        )

    # X 轴日期刻度
    ticks = []
    for k in range(6):
        i = int((n - 1) * k / 5)
        x = sx(i)
        ticks.append(
            f'<text x="{x:.2f}" y="{h - pad_b + 20}" text-anchor="middle" '
            f'font-size="12" fill="#666">{dates[i]}</text>'
        )

    ret = (balance[-1] / config.capital - 1) * 100
    color = "#c0392b" if ret >= 0 else "#27ae60"
    title = (
        f"{config.vt_symbol}  权益曲线   "
        f"{dates[0]} ~ {dates[-1]}   收益 {ret:+.2f}%"
    )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
     viewBox="0 0 {w} {h}" font-family="Segoe UI, Microsoft YaHei, sans-serif">
  <rect width="{w}" height="{h}" fill="#ffffff"/>
  <text x="{pad_l}" y="26" font-size="16" font-weight="600" fill="#222">{title}</text>
  {''.join(grid)}
  {''.join(labels)}
  {''.join(ticks)}
  <line x1="{pad_l}" y1="{base_y:.2f}" x2="{pad_l + plot_w}" y2="{base_y:.2f}"
        stroke="#888" stroke-width="1" stroke-dasharray="5 4"/>
  <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}"
        stroke="#bbb"/>
  <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}"
        y2="{pad_t + plot_h}" stroke="#bbb"/>
</svg>'''
    path.write_text(svg, encoding="utf-8")


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "build_engine",
    "check_local_data",
    "export_result",
    "infer_spec",
    "run_backtest",
    "run_grid",
]
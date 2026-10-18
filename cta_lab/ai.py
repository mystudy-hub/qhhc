"""交易层面统计 + 回测结果分析。

vnpy 的 calculate_statistics 只给出账户层面的指标（总盈亏、回撤、夏普），
不给出"按开平配对的单笔交易"统计（胜率、盈亏比、期望值）。本模块补齐这部分。

另外提供：
    - 稳健性评分（多维度打分，用于快速筛策略）
    - 指标 -> 自然语言的本地解读（不调用任何 LLM）
    - 可选的 LLM 深度解读（自行提供 api_key / base_url）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vnpy.trader.constant import Direction, Offset
from vnpy.trader.object import TradeData

# ---------------------------------------------------------------------------
# 开平配对
# ---------------------------------------------------------------------------

@dataclass
class RoundTrip:
    """一个完成的交易回合（一次开仓 -> 对应平仓）。"""

    direction: str          # 'long' / 'short'
    open_time: datetime
    close_time: datetime
    open_price: float
    close_price: float
    volume: float
    pnl: float              # 价格差 * 乘数 * 手数（未扣费）
    holding_days: float

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


def _net_pnl(
    open_price: float,
    close_price: float,
    volume: float,
    size: float,
    rate: float,
    direction_sign: int,
) -> float:
    """计算平仓后的净盈亏（含双边手续费）。"""
    gross = (close_price - open_price) * size * volume * direction_sign
    turnover = (open_price + close_price) * size * volume
    commission = turnover * rate
    return gross - commission


def build_round_trips(
    trades: list[TradeData],
    size: float = 1.0,
    rate: float = 0.0,
    slippage: float = 0.0,
) -> list[RoundTrip]:
    """把成交流水配对成完成的交易回合。

    采用 FIFO 配对：每个平仓成交依次吃掉方向相反的开仓队列。
    同时处理"反手"场景（如 pos>0 时 sell 后立刻 short）。
    """
    # 两个方向的未平仓队列：long 队列存买开，short 队列存卖开
    open_long: deque[dict] = deque()
    open_short: deque[dict] = deque()
    out: list[RoundTrip] = []

    for trade in sorted(trades, key=lambda t: t.datetime):
        is_open = trade.offset == Offset.OPEN

        if trade.direction == Direction.LONG:
            # 买开 或 买平（平空）
            if is_open:
                open_long.append({
                    "time": trade.datetime,
                    "price": trade.price,
                    "volume": trade.volume,
                })
            else:
                _consume(open_short, trade, out, size, rate, direction_sign=-1)
        else:
            # 卖开 或 卖平（平多）
            if is_open:
                open_short.append({
                    "time": trade.datetime,
                    "price": trade.price,
                    "volume": trade.volume,
                })
            else:
                _consume(open_long, trade, out, size, rate, direction_sign=+1)

    return out


def _consume(
    queue: deque[dict],
    close_trade: TradeData,
    out: list[RoundTrip],
    size: float,
    rate: float,
    direction_sign: int,
) -> None:
    """用一个平仓成交按 FIFO 消耗开仓队列。"""
    remaining = close_trade.volume
    while remaining > 1e-9 and queue:
        lot = queue[0]
        matched = min(lot["volume"], remaining)

        pnl = _net_pnl(
            open_price=lot["price"],
            close_price=close_trade.price,
            volume=matched,
            size=size,
            rate=rate,
            direction_sign=direction_sign,
        )
        holding = (close_trade.datetime - lot["time"]).total_seconds() / 86400.0

        out.append(
            RoundTrip(
                direction="long" if direction_sign > 0 else "short",
                open_time=lot["time"],
                close_time=close_trade.datetime,
                open_price=lot["price"],
                close_price=close_trade.price,
                volume=matched,
                pnl=pnl,
                holding_days=holding,
            )
        )

        lot["volume"] -= matched
        remaining -= matched
        if lot["volume"] <= 1e-9:
            queue.popleft()


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def trade_statistics(
    trades: list[TradeData],
    config: Any = None,
    size: float | None = None,
    rate: float | None = None,
) -> dict:
    """基于成交流水计算交易层面统计指标。"""
    if size is None:
        size = getattr(config, "size", None)
    if rate is None:
        rate = getattr(config, "rate", None)

    if size is None or rate is None:
        try:
            from .runner import infer_spec
            inferred_size, _, inferred_rate, _ = infer_spec(config)
            size = size if size is not None else inferred_size
            rate = rate if rate is not None else inferred_rate
        except Exception:  # noqa: BLE001 - 兜底，不影响主流程
            size = size or 10.0
            rate = rate or 0.0

    trips = build_round_trips(trades, size=float(size), rate=float(rate))

    if not trips:
        return {
            "round_trips": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "expectancy": 0.0,
            "max_win": 0.0,
            "max_loss": 0.0,
            "avg_holding_days": 0.0,
            "total_pnl": 0.0,
        }

    wins = [t.pnl for t in trips if t.pnl > 0]
    losses = [t.pnl for t in trips if t.pnl <= 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0

    return {
        "round_trips": len(trips),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(trips) * 100,
        "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
        "avg_win": avg_win,
        "avg_loss": -avg_loss,
        "payoff_ratio": avg_win / avg_loss if avg_loss else float("inf"),
        "expectancy": sum(t.pnl for t in trips) / len(trips),
        "max_win": max(t.pnl for t in trips),
        "max_loss": min(t.pnl for t in trips),
        "avg_holding_days": sum(t.holding_days for t in trips) / len(trips),
        "total_pnl": sum(t.pnl for t in trips),
    }


# ---------------------------------------------------------------------------
# 稳健性评分
# ---------------------------------------------------------------------------

def robustness_score(stats: dict, trade_stats: dict) -> dict:
    """多维度打分（0~100），便于快速横向比较策略。

    维度与权重：
        收益能力 30 / 风险控制 30 / 风险调整收益 25 / 交易质量 15

    注：爆仓策略直接给 0 分并标注，不参与加权。
    """
    if stats.get("liquidated"):
        return {
            "total": 0.0,
            "return_score": 0.0,
            "risk_score": 0.0,
            "sharpe_score": 0.0,
            "quality_score": 0.0,
            "grade": "F（爆仓）",
        }

    def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, v))

    # --- 收益能力（年化收益 0%~30% 满分） ---
    annual = float(stats.get("annual_return", 0.0)) / 100.0
    score_return = clamp(annual / 0.30) * 30

    # --- 风险控制（最大回撤 <=10% 满分，>=50% 零分） ---
    dd = abs(float(stats.get("max_ddpercent", 0.0))) / 100.0
    score_risk = clamp((0.50 - dd) / 0.40) * 30

    # --- 风险调整收益（夏普 -1~3 映射到 0~1） ---
    sharpe = float(stats.get("sharpe_ratio", 0.0))
    score_sharpe = clamp((sharpe + 1.0) / 4.0) * 25

    # --- 交易质量（胜率 与 盈亏比 各占一半） ---
    win_rate = float(trade_stats.get("win_rate", 0.0)) / 100.0
    payoff = float(trade_stats.get("payoff_ratio", 0.0))
    if payoff == float("inf"):
        payoff = 5.0
    score_quality = (clamp(win_rate / 0.55) * 0.5 + clamp(payoff / 3.0) * 0.5) * 15

    total = score_return + score_risk + score_sharpe + score_quality

    return {
        "total": round(total, 2),
        "return_score": round(score_return, 2),
        "risk_score": round(score_risk, 2),
        "sharpe_score": round(score_sharpe, 2),
        "quality_score": round(score_quality, 2),
        "grade": _grade(total),
    }


def _grade(score: float) -> str:
    if score >= 80:
        return "A（优秀）"
    if score >= 65:
        return "B（良好）"
    if score >= 50:
        return "C（中等）"
    if score >= 35:
        return "D（偏弱）"
    return "E（较差）"


# ---------------------------------------------------------------------------
# 本地解读（无需 LLM）
# ---------------------------------------------------------------------------

def diagnose(stats: dict, trade_stats: dict, config: Any = None) -> list[str]:
    """基于规则生成策略诊断意见。"""
    notes: list[str] = []

    # 爆仓优先（vnpy 会把所有指标静默置 0，必须先解释清楚）
    if stats.get("liquidated"):
        notes.append(
            f"⚠ 回测期间已爆仓（最低权益 {float(stats.get('min_balance', 0)):,.0f}，"
            f"首次爆仓 {stats.get('liquidation_date') or '未知'}）。"
            "收益/回撤/夏普全为 0 是 vnpy 对非正权益的归零行为，"
            "不代表策略空仓，这些指标不可用。"
        )
        notes.append(
            f"估算名义杠杆约 {stats.get('est_leverage')} 倍。"
            "vnpy 回测不校验保证金，请调小 fixed_size 或调高 capital 后重跑。"
        )
        notes.append(
            f"已成交 {int(trade_stats.get('round_trips', 0)) * 2} 笔、"
            f"完成 {int(trade_stats.get('round_trips', 0))} 个回合，"
            "说明策略逻辑确实在交易，问题出在仓位规模。"
        )
        notes.append("其余指标在爆仓场景下无意义，已暂不输出。")
        return notes

    # 未爆仓但杠杆偏高
    lev = float(stats.get("est_leverage", 0) or 0)
    if lev > 3:
        notes.append(
            f"⚠ 名义杠杆约 {lev} 倍，偏高。"
            "vnpy 回测不校验保证金，实盘中容易因保证金不足被强平。"
        )

    annual = float(stats.get("annual_return", 0.0))
    dd = abs(float(stats.get("max_ddpercent", 0.0)))
    sharpe = float(stats.get("sharpe_ratio", 0.0))
    rdd = float(stats.get("return_drawdown_ratio", 0.0))
    trades = int(stats.get("total_trade_count", 0))
    win_rate = float(trade_stats.get("win_rate", 0.0))
    payoff = float(trade_stats.get("payoff_ratio", 0.0))
    holding = float(trade_stats.get("avg_holding_days", 0.0))
    round_trips = int(trade_stats.get("round_trips", 0))

    # 收益率
    if annual > 15:
        notes.append(f"年化收益 {annual:.2f}%，收益能力较强。")
    elif annual > 0:
        notes.append(f"年化收益 {annual:.2f}%，为正但偏低，考虑优化入场条件。")
    else:
        notes.append(f"年化收益 {annual:.2f}%（亏损），策略在该区间无正期望。")

    # 回撤
    if dd > 30:
        notes.append(f"最大回撤 {dd:.2f}% 偏大，需加入止损或降低仓位。")
    elif dd > 15:
        notes.append(f"最大回撤 {dd:.2f}% 中等，可接受但要监控。")
    else:
        notes.append(f"最大回撤 {dd:.2f}% 控制良好。")

    # 夏普
    if sharpe > 1.5:
        notes.append(f"夏普比率 {sharpe:.2f}，风险调整后收益优秀。")
    elif sharpe > 0.5:
        notes.append(f"夏普比率 {sharpe:.2f}，风险调整后收益尚可。")
    else:
        notes.append(f"夏普比率 {sharpe:.2f} 偏低，单位风险回报不足。")

    # 收益回撤比
    if rdd > 0:
        notes.append(f"收益回撤比 {rdd:.2f}（每承受 1% 回撤换回 {rdd:.2f}% 收益）。")
    else:
        notes.append("收益回撤比为负，收益无法覆盖回撤。")

    # 交易质量
    round_trips = int(trade_stats.get("round_trips", 0))
    if round_trips < 30:
        notes.append(
            f"完成交易回合仅 {round_trips} 个，样本偏小，"
            "统计结论可信度低，建议延长回测区间。"
        )
    else:
        notes.append(
            f"完成 {round_trips} 个交易回合（{trades} 笔成交），样本量足够。"
        )

    if win_rate >= 50:
        notes.append(f"胜率 {win_rate:.2f}%，属于偏高胜率型。")
    else:
        notes.append(f"胜率 {win_rate:.2f}%，属低胜率型，依赖盈亏比。")

    if payoff == float("inf"):
        notes.append("盈亏比为无穷（无亏损交易），需警惕过拟合或样本不足。")
    elif payoff >= 2:
        notes.append(f"盈亏比 {payoff:.2f}，单笔盈利显著大于亏损，结构健康。")
    elif payoff > 1:
        notes.append(f"盈亏比 {payoff:.2f}，略高于 1，容错空间有限。")
    else:
        notes.append(f"盈亏比 {payoff:.2f} 偏低（亏损大于盈利），需提高入场质量。")

    if holding:
        notes.append(f"平均持仓 {holding:.2f} 天，属于{'短线' if holding < 5 else '中长线'}交易。")

    # 成本敏感度
    total_pnl = float(stats.get("total_net_pnl", 0.0))
    cost = float(stats.get("total_commission", 0.0)) + float(
        stats.get("total_slippage", 0.0)
    )
    if total_pnl > 0 and cost > 0:
        ratio = cost / total_pnl * 100
        if ratio > 50:
            notes.append(
                f"交易成本占净盈利 {ratio:.1f}%，成本侵蚀严重，"
                "该策略不适合高频执行。"
            )
        else:
            notes.append(f"交易成本占净盈利 {ratio:.1f}%，成本可接受。")

    return notes


# ---------------------------------------------------------------------------
# 可选 LLM 深度解读
# ---------------------------------------------------------------------------

def build_llm_prompt(result: Any, max_trades: int = 30) -> str:
    """把回测结果整理成给 LLM 的结构化提示词。"""
    cfg = result.config
    stats = result.statistics
    ts = result.trade_stats()

    recent = sorted(result.trades, key=lambda t: t.datetime)[-max_trades:]
    trade_lines = [
        f"  {t.datetime:%Y-%m-%d} {t.direction.value:5s} {t.offset.value:6s} "
        f"价={t.price} 量={t.volume}"
        for t in recent
    ]

    return f"""你是一名量化策略研究员。以下是一份国内期货 CTA 策略的回测结果，请做专业解读。

## 回测设置
- 合约：{cfg.vt_symbol}
- 区间：{cfg.start:%Y-%m-%d} ~ {cfg.end:%Y-%m-%d}
- 周期：{cfg.interval.value}
- 初始资金：{stats.get('capital', 0):,.0f}
- 手续费/滑点：已按交易所与最小变动价位计入

## 账户层面指标
- 期末权益：{stats.get('end_balance', 0):,.2f}
- 总收益率：{stats.get('total_return', 0):.2f}%
- 年化收益率：{stats.get('annual_return', 0):.2f}%
- 最大回撤：{stats.get('max_ddpercent', 0):.2f}%（持续 {stats.get('max_drawdown_duration', 0)} 天）
- 夏普比率：{stats.get('sharpe_ratio', 0):.4f}
- 收益回撤比：{stats.get('return_drawdown_ratio', 0):.4f}
- 总盈亏：{stats.get('total_net_pnl', 0):,.2f}
- 总手续费：{stats.get('total_commission', 0):,.2f}
- 总滑点：{stats.get('total_slippage', 0):,.2f}
- 总成交笔数：{stats.get('total_trade_count', 0)}

## 交易层面指标（按开平配对）
- 完成回合：{ts.get('round_trips', 0)}
- 胜率：{ts.get('win_rate', 0):.2f}%
- 盈亏比：{ts.get('payoff_ratio', 0):.4f}
- 盈利因子：{ts.get('profit_factor', 0):.4f}
- 单笔期望：{ts.get('expectancy', 0):,.2f}
- 平均持仓天数：{ts.get('avg_holding_days', 0):.2f}

## 最近 {len(recent)} 笔成交
{chr(10).join(trade_lines) if trade_lines else '  （无成交）'}

## 请输出
1. 策略画像：它赚的是什么钱（趋势/震荡/套利/统计偏差），适配什么市场状态。
2. 关键风险：最可能的失效场景与失效信号。
3. 过拟合判断：结合成交笔数、盈亏比、成本占比给出判断依据。
4. 可验证的改进方向：给出 2~3 个具体、可回测验证的建议（不要泛泛而谈）。
5. 一句话结论：是否值得进入下一步（模拟盘 / 更细的样本外验证）。
"""


def llm_review(
    result: Any,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout: int = 120,
) -> str:
    """可选的 LLM 深度解读（需要自备 api_key）。

    仅使用标准库 urllib，兼容任何 OpenAI 格式的 /chat/completions 端点，
    因此也适用于 DeepSeek / Kimi / Qwen / 本地 Ollama 等。
    """
    import json
    import urllib.request

    prompt = build_llm_prompt(result)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的量化策略研究员，只基于给定数据说话，不做收益承诺。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


__all__ = [
    "RoundTrip",
    "build_round_trips",
    "trade_statistics",
    "robustness_score",
    "diagnose",
    "build_llm_prompt",
    "llm_review",
]
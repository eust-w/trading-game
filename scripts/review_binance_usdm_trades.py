#!/usr/bin/env python3
"""Create trade-level review artifacts from Binance USD-M exports."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
ARTIFACTS = Path("data/processed/binance-usdm-30d")
REPORT = Path("docs/postmortems/binance-usdm-trade-review-summary.md")


def dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def money(value: Decimal) -> str:
    return f"{value:.6f}"


def dt_ms(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=CST)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


@dataclass
class SymbolStats:
    net: Decimal
    realized_pnl: Decimal
    commission: Decimal
    funding_fee: Decimal
    insurance_clear: Decimal
    trade_count: int


def load_symbol_stats(path: Path) -> dict[str, SymbolStats]:
    stats: dict[str, SymbolStats] = {}
    for row in read_csv(path):
        symbol = row["symbol"]
        if symbol == "ACCOUNT":
            continue
        stats[symbol] = SymbolStats(
            net=dec(row["net"]),
            realized_pnl=dec(row["realized_pnl"]),
            commission=dec(row["commission"]),
            funding_fee=dec(row["funding_fee"]),
            insurance_clear=dec(row["insurance_clear"]),
            trade_count=int(row["trade_count"]),
        )
    return stats


def classify_review(symbol: str, realized: Decimal, commission: Decimal, symbol_stats: SymbolStats | None) -> tuple[str, str, str]:
    net = realized - commission
    tags: list[str] = []

    if realized == 0:
        result = "OPEN_OR_FLAT_FILL"
        lesson = "未实现盈亏成交，重点检查是否属于计划内建仓/加仓，以及是否提前写好止损。"
        tags.append("entry_cost")
    elif net > 0:
        result = "WIN"
        lesson = "盈利成交，复盘重点是提炼可重复的入场结构和退出规则。"
        tags.append("preserve_edge")
    else:
        result = "LOSS"
        lesson = "亏损成交，复盘重点是原假设何时失效、是否应更早退出、是否重复交易同一错误方向。"
        tags.append("loss_review")

    if symbol_stats:
        if symbol_stats.net < Decimal("-50"):
            tags.append("major_loser_symbol")
            lesson += " 该 symbol 是本期主要亏损来源，后续应执行连续亏损暂停和单币亏损上限。"
        if symbol_stats.insurance_clear < 0:
            tags.append("insurance_clear_risk")
            lesson += " 该 symbol 出现过 INSURANCE_CLEAR，需要优先排查强平/保险清算或极端风控事件。"
        if abs(symbol_stats.commission) > abs(symbol_stats.realized_pnl) * Decimal("0.2") and symbol_stats.trade_count >= 50:
            tags.append("fee_drag")
            lesson += " 该 symbol 手续费占比偏高，说明反复交易正在侵蚀收益。"
        if symbol_stats.trade_count >= 100:
            tags.append("high_frequency")

    if realized < 0:
        tags.append("no_average_down")
    if commission > Decimal("1"):
        tags.append("large_fee_single_fill")

    severity = "normal"
    if realized == 0:
        severity = "cost"
    elif net <= Decimal("-50"):
        severity = "critical"
    elif net <= Decimal("-10"):
        severity = "high"
    elif net < 0:
        severity = "medium"

    return result, severity, "; ".join(dict.fromkeys(tags)) + " | " + lesson


def build_trade_reviews(trades: list[dict[str, str]], symbol_stats: dict[str, SymbolStats]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(sorted(trades, key=lambda item: int(item["time"])), start=1):
        symbol = row["symbol"]
        realized = dec(row["realizedPnl"])
        commission = dec(row["commission"])
        net_after_commission = realized - commission
        result, severity, review = classify_review(symbol, realized, commission, symbol_stats.get(symbol))
        rows.append(
            {
                "seq": index,
                "time_cst": dt_ms(row["time"]).strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol,
                "side": row["side"],
                "position_side": row["positionSide"],
                "price": row["price"],
                "qty": row["qty"],
                "quote_qty": row["quoteQty"],
                "realized_pnl": money(realized),
                "commission": money(commission),
                "net_after_commission": money(net_after_commission),
                "result": result,
                "severity": severity,
                "order_id": row["orderId"],
                "trade_id": row["id"],
                "review": review,
            }
        )
    return rows


def build_order_reviews(trade_reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in trade_reviews:
        grouped[(row["symbol"], row["order_id"])].append(row)

    rows: list[dict[str, Any]] = []
    for (symbol, order_id), fills in grouped.items():
        fills.sort(key=lambda item: item["seq"])
        realized = sum((dec(item["realized_pnl"]) for item in fills), Decimal("0"))
        commission = sum((dec(item["commission"]) for item in fills), Decimal("0"))
        net = sum((dec(item["net_after_commission"]) for item in fills), Decimal("0"))
        notional = sum((dec(item["quote_qty"]) for item in fills), Decimal("0"))
        sides = sorted({item["side"] for item in fills})
        severities = Counter(item["severity"] for item in fills)
        result = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
        rows.append(
            {
                "first_seq": fills[0]["seq"],
                "time_cst": fills[0]["time_cst"],
                "symbol": symbol,
                "order_id": order_id,
                "side": "/".join(sides),
                "fill_count": len(fills),
                "notional": money(notional),
                "realized_pnl": money(realized),
                "commission": money(commission),
                "net_after_commission": money(net),
                "result": result,
                "severity": "critical"
                if net <= Decimal("-50")
                else "high"
                if net <= Decimal("-10")
                else "medium"
                if net < 0
                else "normal",
                "dominant_fill_severity": severities.most_common(1)[0][0],
                "review": order_review_text(symbol, net, realized, commission, len(fills)),
            }
        )
    return sorted(rows, key=lambda item: item["first_seq"])


def order_review_text(symbol: str, net: Decimal, realized: Decimal, commission: Decimal, fill_count: int) -> str:
    if net > 0:
        return f"{symbol} 该订单净盈利，后续复盘应保留入场结构和退出纪律；订单拆成 {fill_count} 笔成交。"
    if realized >= 0 and net < 0:
        return f"{symbol} 该订单毛盈亏不差但扣手续费后为负，说明交易边际过薄；订单拆成 {fill_count} 笔成交。"
    return f"{symbol} 该订单净亏损，应检查入场假设、止损执行、是否追涨杀跌或亏损后重复试错；订单拆成 {fill_count} 笔成交。"


def render_summary(
    trade_reviews: list[dict[str, Any]],
    order_reviews: list[dict[str, Any]],
    symbol_stats: dict[str, SymbolStats],
) -> str:
    total_trades = len(trade_reviews)
    total_orders = len(order_reviews)
    total_net = sum((dec(row["net_after_commission"]) for row in trade_reviews), Decimal("0"))
    realized_total = sum((dec(row["realized_pnl"]) for row in trade_reviews), Decimal("0"))
    commission_total = sum((dec(row["commission"]) for row in trade_reviews), Decimal("0"))
    cost_only = [row for row in trade_reviews if row["result"] == "OPEN_OR_FLAT_FILL"]
    win_trades = [row for row in trade_reviews if dec(row["net_after_commission"]) > 0]
    realized_loss_trades = [
        row for row in trade_reviews if row["result"] == "LOSS" and dec(row["realized_pnl"]) < 0
    ]
    fee_drag_trades = [
        row for row in trade_reviews if row["result"] == "LOSS" and dec(row["realized_pnl"]) >= 0
    ]
    worst_orders = sorted(order_reviews, key=lambda item: dec(item["net_after_commission"]))[:20]
    best_orders = sorted(order_reviews, key=lambda item: dec(item["net_after_commission"]), reverse=True)[:10]
    costly_symbols = sorted(symbol_stats.items(), key=lambda item: item[1].commission)[:10]
    high_freq_symbols = sorted(symbol_stats.items(), key=lambda item: item[1].trade_count, reverse=True)[:10]
    major_losers = sorted(symbol_stats.items(), key=lambda item: item[1].net)[:10]

    lines = [
        "# Binance USD-M 逐笔交易复盘总结",
        "",
        "## 数据口径",
        "",
        "- 明细来源：`data/processed/binance-usdm-30d/trades.csv` 和 `income.csv`。",
        f"- 逐笔成交复盘：`data/processed/binance-usdm-30d/trade_reviews.csv`，共 {total_trades} 笔。",
        f"- 订单聚合复盘：`data/processed/binance-usdm-30d/order_reviews.csv`，共 {total_orders} 个订单。",
        "- 每笔成交用 `realizedPnl - commission` 作为该成交的净结果；资金费、保险清算等不在单笔成交内，已在分币种月报中单独体现。",
        "",
        "## 总体结论",
        "",
        f"- 成交净额合计：{money(total_net)} USDT，不含资金费和保险清算。",
        f"- 已实现盈亏合计：{money(realized_total)} USDT。",
        f"- 成交手续费合计：-{money(commission_total)} USDT。",
        f"- 净盈利平仓成交：{len(win_trades)} 笔；真实亏损平仓成交：{len(realized_loss_trades)} 笔；只产生手续费或毛盈亏非负但扣费后为负的成交：{len(fee_drag_trades)} 笔；建仓/无实现盈亏成交：{len(cost_only)} 笔。",
        "",
        "## 最大亏损订单 Top 20",
        "",
        "| Time | Symbol | Side | Fills | Net | Realized | Fee | Review |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in worst_orders:
        lines.append(
            f"| {row['time_cst']} | `{row['symbol']}` | {row['side']} | {row['fill_count']} | {row['net_after_commission']} | {row['realized_pnl']} | -{row['commission']} | {row['review']} |"
        )

    lines += [
        "",
        "## 最大盈利订单 Top 10",
        "",
        "| Time | Symbol | Side | Fills | Net | Realized | Fee | Review |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in best_orders:
        lines.append(
            f"| {row['time_cst']} | `{row['symbol']}` | {row['side']} | {row['fill_count']} | {row['net_after_commission']} | {row['realized_pnl']} | -{row['commission']} | {row['review']} |"
        )

    lines += [
        "",
        "## 亏损集中币复盘优先级",
        "",
        "| Symbol | Net | Realized | Fee | Funding | Insurance Clear | Trades | 复盘重点 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for symbol, stats in major_losers:
        focus = "先查连续亏损、补仓、止损失效"
        if stats.insurance_clear < 0:
            focus += "；并排查 INSURANCE_CLEAR"
        if abs(stats.commission) > Decimal("5"):
            focus += "；手续费过高"
        lines.append(
            f"| `{symbol}` | {money(stats.net)} | {money(stats.realized_pnl)} | {money(stats.commission)} | {money(stats.funding_fee)} | {money(stats.insurance_clear)} | {stats.trade_count} | {focus} |"
        )

    lines += [
        "",
        "## 手续费和交易频率警报",
        "",
        "### 手续费最高的 symbol",
        "",
        "| Symbol | Fee | Net | Trades | 判断 |",
        "|---|---:|---:|---:|---|",
    ]
    for symbol, stats in costly_symbols:
        judgement = "盈利但成本高，确认是否可复制" if stats.net > 0 else "亏损且成本高，应减少试错频率"
        lines.append(f"| `{symbol}` | {money(stats.commission)} | {money(stats.net)} | {stats.trade_count} | {judgement} |")

    lines += [
        "",
        "### 成交笔数最高的 symbol",
        "",
        "| Symbol | Trades | Net | Fee | 判断 |",
        "|---|---:|---:|---:|---|",
    ]
    for symbol, stats in high_freq_symbols:
        judgement = "高频有效但需验证稳定性" if stats.net > 0 else "高频无效，应降频或暂停"
        lines.append(f"| `{symbol}` | {stats.trade_count} | {money(stats.net)} | {money(stats.commission)} | {judgement} |")

    lines += [
        "",
        "## 逐笔复盘经验",
        "",
        "1. 每笔亏损成交都要回答：原假设何时失效、为什么没有更早退出、是否在同一 symbol 上连续试错。",
        "2. 每笔建仓成交都要回答：止损是否已经写好、如果下一笔变成亏损是否会补仓。",
        "3. 每笔盈利成交都要回答：盈利来自可重复结构，还是来自偶然波动。",
        "4. 订单级别净亏损超过 `10 USDT` 的记录，下周复盘时必须配图看 15m/1h 结构。",
        "5. 同一 symbol 成交笔数过百但净亏损，优先判定为策略效率问题，不继续扩大样本。",
        "",
        "## 下次交易前新增硬规则",
        "",
        "- 如果当前 symbol 已进入亏损集中币列表，下一笔交易必须先写复盘引用。",
        "- 如果订单预计利润不能覆盖两边手续费、滑点和资金费，不开仓。",
        "- 如果单个 symbol 当天连续两次出现净亏损成交，暂停该 symbol。",
        "- 如果上一笔同 symbol 是亏损，下一笔禁止直接补仓，必须重新满足入场条件。",
        "- 出现 `INSURANCE_CLEAR` 的 symbol，在完成单独复盘前不允许作为主动开仓候选。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    trades = read_csv(ARTIFACTS / "trades.csv")
    symbol_stats = load_symbol_stats(ARTIFACTS / "symbol_summary.csv")
    trade_reviews = build_trade_reviews(trades, symbol_stats)
    order_reviews = build_order_reviews(trade_reviews)

    write_csv(
        ARTIFACTS / "trade_reviews.csv",
        trade_reviews,
        [
            "seq",
            "time_cst",
            "symbol",
            "side",
            "position_side",
            "price",
            "qty",
            "quote_qty",
            "realized_pnl",
            "commission",
            "net_after_commission",
            "result",
            "severity",
            "order_id",
            "trade_id",
            "review",
        ],
    )
    write_csv(
        ARTIFACTS / "order_reviews.csv",
        order_reviews,
        [
            "first_seq",
            "time_cst",
            "symbol",
            "order_id",
            "side",
            "fill_count",
            "notional",
            "realized_pnl",
            "commission",
            "net_after_commission",
            "result",
            "severity",
            "dominant_fill_severity",
            "review",
        ],
    )
    REPORT.write_text(render_summary(trade_reviews, order_reviews, symbol_stats))
    (ARTIFACTS / "trade_review_metadata.json").write_text(
        json.dumps(
            {
                "trade_reviews": len(trade_reviews),
                "order_reviews": len(order_reviews),
                "report": str(REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps({"trade_reviews": len(trade_reviews), "order_reviews": len(order_reviews), "report": str(REPORT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

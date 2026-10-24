#!/usr/bin/env python3
"""Generate a read-only Binance USD-M futures monthly trading report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests
from requests import RequestException


DEFAULT_ENV_PATH = Path(
    ".env"
)
OFFICIAL_USDM_BASE = "https://fapi.binance.com"
CST = timezone(timedelta(hours=8))


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


@dataclass
class BinanceClient:
    api_key: str
    api_secret: str
    base_url: str = OFFICIAL_USDM_BASE
    time_offset_ms: int = 0
    max_retries: int = 3

    def sync_time(self) -> None:
        server = self.public_get("/fapi/v1/time")["serverTime"]
        self.time_offset_ms = int(server) - int(time.time() * 1000)

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(
                    self.base_url + path,
                    params=params or {},
                    timeout=20,
                    headers={"User-Agent": "alpha-vault-readonly/1.0"},
                )
                response.raise_for_status()
                return response.json()
            except RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"{path} network failure after {attempt} attempts: {type(exc).__name__}") from exc
                time.sleep(attempt)

    def signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        payload = dict(params or {})
        payload.setdefault("recvWindow", 10000)
        payload["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
        query = urllib.parse.urlencode(payload, doseq=True)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_query = f"{query}&signature={signature}"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(
                    self.base_url + path,
                    params=signed_query,
                    timeout=30,
                    headers={
                        "X-MBX-APIKEY": self.api_key,
                        "User-Agent": "alpha-vault-readonly/1.0",
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:300]}")
                return response.json()
            except RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"{path} network failure after {attempt} attempts: {type(exc).__name__}") from exc
                time.sleep(attempt)
        raise RuntimeError(f"{path} failed unexpectedly")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def parse_dt(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=CST)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CST)


def dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def money(value: Decimal) -> str:
    return f"{value:.6f}"


def fetch_income_window(
    client: BinanceClient,
    start: datetime,
    end: datetime,
    warnings: list[str],
    min_window: timedelta = timedelta(minutes=30),
) -> list[dict[str, Any]]:
    data = client.signed_get(
        "/fapi/v1/income",
        {
            "startTime": ms(start),
            "endTime": ms(end) - 1,
            "limit": 1000,
        },
    )
    if len(data) < 1000:
        return data
    if end - start <= min_window:
        warnings.append(
            f"{start.astimezone(CST).strftime('%Y-%m-%d %H:%M')}..{end.astimezone(CST).strftime('%Y-%m-%d %H:%M')} income still returned 1000 rows."
        )
        return data
    midpoint = start + (end - start) / 2
    return fetch_income_window(client, start, midpoint, warnings, min_window) + fetch_income_window(
        client, midpoint, end, warnings, min_window
    )


def fetch_income_by_day(client: BinanceClient, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    cursor = start
    while cursor < end:
        day_end = min(cursor + timedelta(days=1), end)
        data = client.signed_get(
            "/fapi/v1/income",
            {
                "startTime": ms(cursor),
                "endTime": ms(day_end) - 1,
                "limit": 1000,
            },
        )
        if len(data) >= 1000:
            data = fetch_income_window(client, cursor, day_end, warnings)
        rows.extend(data)
        cursor = day_end
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("tranId"),
            row.get("tradeId"),
            row.get("incomeType"),
            row.get("symbol"),
            row.get("time"),
            row.get("income"),
        )
        deduped[key] = row
    return sorted(deduped.values(), key=lambda item: int(item.get("time", 0))), warnings


def split_windows(start: datetime, end: datetime, max_days: int = 7) -> list[tuple[datetime, datetime]]:
    windows = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(days=max_days), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def fetch_user_trades_window(
    client: BinanceClient,
    symbol: str,
    start: datetime,
    end: datetime,
    warnings: list[str],
    min_window: timedelta = timedelta(hours=1),
) -> list[dict[str, Any]]:
    try:
        data = client.signed_get(
            "/fapi/v1/userTrades",
            {
                "symbol": symbol,
                "startTime": ms(start),
                "endTime": ms(end) - 1,
                "limit": 1000,
            },
        )
    except RuntimeError as exc:
        warnings.append(
            f"{symbol} {start.astimezone(CST).strftime('%Y-%m-%d %H:%M')}..{end.astimezone(CST).strftime('%Y-%m-%d %H:%M')} userTrades skipped: {exc}"
        )
        return []
    if len(data) < 1000:
        return data
    if end - start <= min_window:
        warnings.append(
            f"{symbol} {start.astimezone(CST).strftime('%Y-%m-%d %H:%M')}..{end.astimezone(CST).strftime('%Y-%m-%d %H:%M')} trades still returned 1000 rows."
        )
        return data
    midpoint = start + (end - start) / 2
    return fetch_user_trades_window(client, symbol, start, midpoint, warnings, min_window) + fetch_user_trades_window(
        client, symbol, midpoint, end, warnings, min_window
    )


def fetch_user_trades(client: BinanceClient, symbols: list[str], start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for symbol in symbols:
        for window_start, window_end in split_windows(start, end):
            data = fetch_user_trades_window(client, symbol, window_start, window_end, warnings)
            rows.extend(data)
    deduped = {str(row.get("id")) + ":" + row.get("symbol", ""): row for row in rows}
    return sorted(deduped.values(), key=lambda item: int(item.get("time", 0))), warnings


def summarize(income_rows: list[dict[str, Any]], trade_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "realized_pnl": Decimal("0"),
            "commission": Decimal("0"),
            "funding_fee": Decimal("0"),
            "insurance_clear": Decimal("0"),
            "other_income": Decimal("0"),
            "trade_count": 0,
            "buy_qty": Decimal("0"),
            "sell_qty": Decimal("0"),
            "buy_notional": Decimal("0"),
            "sell_notional": Decimal("0"),
        }
    )
    income_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in income_rows:
        symbol = row.get("symbol") or "ACCOUNT"
        income_type = row.get("incomeType") or "UNKNOWN"
        amount = dec(row.get("income"))
        income_totals[income_type] += amount
        bucket = by_symbol[symbol]
        if income_type == "REALIZED_PNL":
            bucket["realized_pnl"] += amount
        elif income_type == "COMMISSION":
            bucket["commission"] += amount
        elif income_type == "FUNDING_FEE":
            bucket["funding_fee"] += amount
        elif income_type == "INSURANCE_CLEAR":
            bucket["insurance_clear"] += amount
        else:
            bucket["other_income"] += amount

    for row in trade_rows:
        symbol = row.get("symbol") or "UNKNOWN"
        bucket = by_symbol[symbol]
        bucket["trade_count"] += 1
        qty = dec(row.get("qty"))
        quote_qty = dec(row.get("quoteQty"))
        if row.get("side") == "BUY":
            bucket["buy_qty"] += qty
            bucket["buy_notional"] += quote_qty
        elif row.get("side") == "SELL":
            bucket["sell_qty"] += qty
            bucket["sell_notional"] += quote_qty

    symbol_rows = []
    for symbol, bucket in by_symbol.items():
        net = (
            bucket["realized_pnl"]
            + bucket["commission"]
            + bucket["funding_fee"]
            + bucket["insurance_clear"]
            + bucket["other_income"]
        )
        if symbol == "ACCOUNT" and net == 0:
            continue
        symbol_rows.append({"symbol": symbol, "net": net, **bucket})
    symbol_rows.sort(key=lambda item: item["net"], reverse=True)
    return {"symbols": symbol_rows, "income_totals": dict(income_totals)}


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def render_markdown(
    start: datetime,
    end: datetime,
    income_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    warnings: list[str],
) -> str:
    account_rows = [row for row in summary["symbols"] if row["symbol"] == "ACCOUNT"]
    trading_rows = [row for row in summary["symbols"] if row["symbol"] != "ACCOUNT"]
    account_level_net = sum((row["net"] for row in account_rows), Decimal("0"))
    trading_net = sum((row["net"] for row in trading_rows), Decimal("0"))
    total_net = account_level_net + trading_net
    winners = [row for row in trading_rows if row["net"] > 0]
    losers = [row for row in trading_rows if row["net"] < 0]
    top_losers = sorted(losers, key=lambda item: item["net"])[:8]
    top_winners = winners[:8]

    lines = [
        "# Binance USD-M 近 30 天交易复盘",
        "",
        f"- 统计范围：{start.astimezone(CST).strftime('%Y-%m-%d %H:%M:%S %z')} 至 {end.astimezone(CST).strftime('%Y-%m-%d %H:%M:%S %z')}",
        "- 数据来源：Binance USD-M Futures signed read-only endpoints `/fapi/v1/income` and `/fapi/v1/userTrades`",
        f"- income 流水：{len(income_rows)} 条",
        f"- 成交明细：{len(trade_rows)} 条",
        f"- 账户总流水净额：{money(total_net)} USDT",
        f"- 分币种交易净额：{money(trading_net)} USDT",
        f"- 账户级非交易流水：{money(account_level_net)} USDT",
        "",
    ]
    if warnings:
        lines += ["## 数据完整性提醒", ""]
        lines += [f"- {warning}" for warning in warnings]
        lines.append("")

    lines += [
        "## 分币种交易盈亏",
        "",
        "| Symbol | Net USDT | Realized PnL | Commission | Funding | Insurance Clear | Other | Trades | Buy Notional | Sell Notional |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in trading_rows:
        lines.append(
            "| {symbol} | {net} | {realized} | {commission} | {funding} | {insurance} | {other} | {trades} | {buy} | {sell} |".format(
                symbol=row["symbol"],
                net=money(row["net"]),
                realized=money(row["realized_pnl"]),
                commission=money(row["commission"]),
                funding=money(row["funding_fee"]),
                insurance=money(row["insurance_clear"]),
                other=money(row["other_income"]),
                trades=row["trade_count"],
                buy=money(row["buy_notional"]),
                sell=money(row["sell_notional"]),
            )
        )
    lines.append("")

    if account_rows:
        lines += [
            "## 账户级非交易流水",
            "",
            "| Type | Amount USDT | Note |",
            "|---|---:|---|",
        ]
        for row in account_rows:
            lines.append(
                f"| ACCOUNT | {money(row['net'])} | 主要来自 TRANSFER / AUTO_EXCHANGE 等非 symbol 交易流水，不计入单币种交易胜负。 |"
            )
        lines.append("")

    lines += [
        "## 收益来源汇总",
        "",
        "| Income Type | Amount USDT |",
        "|---|---:|",
    ]
    for income_type, amount in sorted(summary["income_totals"].items()):
        lines.append(f"| {income_type} | {money(amount)} |")
    lines.append("")

    lines += ["## 主要盈利币", ""]
    if top_winners:
        for row in top_winners:
            lines.append(
                f"- `{row['symbol']}`：净 {money(row['net'])} USDT，成交 {row['trade_count']} 笔，手续费 {money(row['commission'])}。"
            )
    else:
        lines.append("- 无净盈利币种。")
    lines.append("")

    lines += ["## 主要亏损币", ""]
    if top_losers:
        for row in top_losers:
            lines.append(
                f"- `{row['symbol']}`：净 {money(row['net'])} USDT，realized {money(row['realized_pnl'])}，手续费 {money(row['commission'])}，资金费 {money(row['funding_fee'])}。"
            )
    else:
        lines.append("- 无净亏损币种。")
    lines.append("")

    lines += [
        "## 关键观察",
        "",
        f"- 本期真正的分币种交易净额为 {money(trading_net)} USDT，账户总流水净额 {money(total_net)} USDT 受到充值、划转或自动兑换等账户级流水影响。",
        f"- 手续费合计 {money(summary['income_totals'].get('COMMISSION', Decimal('0')))} USDT，已经是独立的大额成本，说明高频反复开平会显著侵蚀收益。",
        f"- `INSURANCE_CLEAR` 合计 {money(summary['income_totals'].get('INSURANCE_CLEAR', Decimal('0')))} USDT，亏损表中出现该项的币种要优先复盘是否发生强平、保险清算或极端风控事件。",
        "- 盈利主要集中在少数币，亏损也集中在少数币；后续应优先管住大亏币，而不是扩大交易范围。",
        "",
    ]

    lines += [
        "## 教训和经验",
        "",
        "- 优先减少单币连续亏损：同一 symbol 当天连续两次亏损后，应暂停该 symbol，避免在同一个错误假设上反复试单。",
        "- 亏损后禁止盲目补仓：如果首次入场逻辑失效，后续加仓大概率是在放大错误，而不是提高胜率。",
        "- 波动异常时降低仓位或跳过：15m/1h 出现异常放量、长针或 ATR 快速扩大时，应降低到常规仓位 20% 以下，或者直接不做。",
        "- 手续费和资金费要纳入胜负判断：高频来回买卖即使 realized PnL 接近持平，也会被 commission 和 funding 拖成亏损。",
        "- 每笔交易必须先写失效条件：没有明确止损和失效条件的交易，不进入候选池。",
        "- 分币种复盘比总盈亏更重要：盈利币说明当前有效打法，亏损币暴露的是不能再重复的模式。",
        "",
        "## 后续改进",
        "",
        "- 为每个亏损最多的币补一篇单独复盘，重点看入场前 15m/1h 结构、是否追涨杀跌、是否违反暂停规则。",
        "- 在筛选脚本中加入 `same_symbol_loss_pause`、`abnormal_volatility_size_cut`、`no_average_down` 三条硬规则。",
        "- 每周固定导出一次本报告，观察亏损是否集中在少数 symbol 或少数交易时段。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--start", help="Asia/Shanghai date or datetime, default: end minus 30 days")
    parser.add_argument("--end", help="Asia/Shanghai date or datetime, default: now")
    parser.add_argument("--out", default="docs/postmortems/binance-usdm-30d-review.md")
    parser.add_argument("--artifacts-dir", default="data/processed/binance-usdm-30d")
    parser.add_argument("--from-artifacts", action="store_true", help="Render report from existing CSV artifacts without API calls")
    args = parser.parse_args()

    artifacts = Path(args.artifacts_dir)
    if args.from_artifacts:
        metadata = json.loads((artifacts / "metadata.json").read_text())
        start = datetime.fromisoformat(metadata["start"])
        end = datetime.fromisoformat(metadata["end"])
        income_rows = read_csv(artifacts / "income.csv")
        trade_rows = read_csv(artifacts / "trades.csv")
        summary = summarize(income_rows, trade_rows)
        report = render_markdown(start, end, income_rows, trade_rows, summary, metadata.get("warnings", []))
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        print(json.dumps({"report": str(out_path), "artifacts": str(artifacts), "from_artifacts": True}, ensure_ascii=False))
        return

    end = parse_dt(args.end, datetime.now(CST))
    start = parse_dt(args.start, end - timedelta(days=30))
    env = load_env_file(Path(args.env_file))
    api_key = env.get("BINANCE_API_KEY") or os.environ.get("BINANCE_API_KEY")
    api_secret = env.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit("Missing BINANCE_API_KEY or BINANCE_API_SECRET")

    client = BinanceClient(api_key=api_key, api_secret=api_secret)
    client.sync_time()

    income_rows, income_warnings = fetch_income_by_day(client, start, end)
    symbols = sorted(
        {
            row.get("symbol")
            for row in income_rows
            if row.get("symbol") and row.get("incomeType") in {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE", "INSURANCE_CLEAR"}
        }
    )
    trade_rows, trade_warnings = fetch_user_trades(client, symbols, start, end)
    summary = summarize(income_rows, trade_rows)
    warnings = income_warnings + trade_warnings

    write_csv(
        artifacts / "income.csv",
        income_rows,
        ["time", "symbol", "incomeType", "income", "asset", "info", "tranId", "tradeId"],
    )
    write_csv(
        artifacts / "trades.csv",
        trade_rows,
        ["time", "symbol", "side", "positionSide", "price", "qty", "quoteQty", "realizedPnl", "commission", "commissionAsset", "id", "orderId"],
    )
    summary_rows = []
    for row in summary["symbols"]:
        summary_rows.append(
            {
                "symbol": row["symbol"],
                "net": money(row["net"]),
                "realized_pnl": money(row["realized_pnl"]),
                "commission": money(row["commission"]),
                "funding_fee": money(row["funding_fee"]),
                "insurance_clear": money(row["insurance_clear"]),
                "other_income": money(row["other_income"]),
                "trade_count": row["trade_count"],
                "buy_notional": money(row["buy_notional"]),
                "sell_notional": money(row["sell_notional"]),
            }
        )
    write_csv(
        artifacts / "symbol_summary.csv",
        summary_rows,
        [
            "symbol",
            "net",
            "realized_pnl",
            "commission",
            "funding_fee",
            "insurance_clear",
            "other_income",
            "trade_count",
            "buy_notional",
            "sell_notional",
        ],
    )
    (artifacts / "metadata.json").write_text(
        json.dumps(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "income_rows": len(income_rows),
                "trade_rows": len(trade_rows),
                "symbols": symbols,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    report = render_markdown(start, end, income_rows, trade_rows, summary, warnings)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(json.dumps({"report": str(out_path), "artifacts": str(artifacts), "symbols": len(symbols), "warnings": warnings}, ensure_ascii=False))


if __name__ == "__main__":
    main()

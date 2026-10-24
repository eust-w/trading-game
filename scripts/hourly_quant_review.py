#!/usr/bin/env python3
"""Build an hourly review from quant service JSONL logs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
DEFAULT_SCAN_AUDIT = Path("data/processed/paper-signal-service/scan_audit.jsonl")
DEFAULT_EVENTS = Path("data/processed/paper-signal-service/events.jsonl")
DEFAULT_INTENTS = Path("data/processed/paper-signal-service/intents.jsonl")
DEFAULT_EXECUTIONS = Path("data/processed/live-execution/executions.jsonl")
DEFAULT_REPORT_DIR = Path("reports/service/hourly-reviews")
LIVE_EXECUTION_BACKOFF_REASONS = {
    "confirmation_timeout",
    "exchange_rate_limited",
    "live_execution_backoff",
    "margin_insufficient",
    "position_capacity_limit",
}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z",):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"raw": line})
    return rows


def recent(rows: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        ts = parse_time(row.get("time"))
        if ts and ts >= since:
            out.append(row)
    return out


def latest_time(rows: list[dict[str, Any]]) -> str:
    latest: datetime | None = None
    for row in rows:
        ts = parse_time(row.get("time"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest.strftime("%Y-%m-%d %H:%M:%S %z") if latest else "none"


def latest_datetime(rows: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for row in rows:
        ts = parse_time(row.get("time"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest


def normalize_error_text(message: str) -> str:
    text = message.strip()
    if not text:
        return text
    return re.sub(r", request ip: [^\"}]+", "", text)


def extract_request_ip(message: str) -> str | None:
    match = re.search(r"request ip: ([^\"}]+)", message)
    if not match:
        return None
    return match.group(1).strip().rstrip("\\")


def event_request_ips(event: dict[str, Any]) -> list[str]:
    direct = event.get("request_ip")
    if isinstance(direct, str) and direct.strip():
        return [direct.strip()]
    ips: list[str] = []
    for value in (
        event.get("error"),
        event.get("result", {}).get("error"),
        event.get("message"),
    ):
        if not value:
            continue
        request_ip = extract_request_ip(str(value))
        if request_ip:
            ips.append(request_ip)
    return list(dict.fromkeys(ips))


def request_ip_suffix(event: dict[str, Any]) -> str:
    ips = event_request_ips(event)
    if not ips:
        return ""
    return f" request_ip={','.join(ips)}"


def label() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d-%H%M")


def summarize_candidate(row: dict[str, Any]) -> str:
    volume_price = row.get("volume_price_regime")
    volume_price_suffix = (
        f" VP={volume_price}/{row.get('volume_price_score')}"
        if volume_price
        else ""
    )
    order_book_suffix = ""
    if row.get("order_book_imbalance") is not None:
        order_book_suffix = (
            f" OB_imb={row.get('order_book_imbalance')} "
            f"spread={row.get('order_book_spread_bps')}bps"
        )
    elif row.get("order_book_error"):
        order_book_suffix = f" OB_error={row.get('order_book_error')}"
    return (
        f"`{row.get('symbol')}` {row.get('action')} {row.get('direction')} "
        f"score={row.get('score')} strategy={row.get('primary_strategy')} "
        f"price={row.get('last_price')} 24h={row.get('price_change_pct_24h')}% "
        f"ATR={row.get('atr_pct_15m')}% RSI={row.get('rsi14_15m')} "
        f"funding={row.get('funding_rate')}{volume_price_suffix}{order_book_suffix} "
        f"risk={row.get('risk_flags') or '-'}"
    )


def summarize_execution(row: dict[str, Any]) -> str:
    symbol = row.get("symbol")
    status = row.get("status")
    error = row.get("error")
    if symbol or status or error:
        return (
            f"- {row.get('time')} mode={row.get('mode')} symbol={symbol or '-'} "
            f"status={status or '-'} error={error or '-'}"
        )

    stale_orders = row.get("stale_orders") or []
    cancelled = row.get("cancelled_stale_orders") or []
    plans = row.get("plans") or []
    executions = row.get("executions") or []
    plan_symbols = ",".join(
        plan.get("symbol") for plan in plans if plan.get("symbol")
    ) or "-"
    execution_symbols = ",".join(
        execution.get("symbol") for execution in executions if execution.get("symbol")
    ) or "-"
    return (
        f"- {row.get('time')} mode={row.get('mode')} maintenance "
        f"plan_symbols={plan_symbols} execution_symbols={execution_symbols} "
        f"stale_orders={len(stale_orders)} cancelled={len(cancelled)} "
        f"plans={len(plans)} executions={len(executions)}"
    )


def compact_text(value: Any, limit: int = 260) -> str:
    text = str(value or "-").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def operation_strategy_assessment(row: dict[str, Any], reason: str) -> str:
    status = str(row.get("status") or row.get("operation_status") or "")
    error = str(row.get("error") or "")
    risk_flags = str(row.get("risk_flags") or "")
    text = f"{status} {error} {risk_flags} {reason}".lower()
    score_value = row.get("score")
    try:
        score = float(score_value) if score_value not in (None, "") else None
    except (TypeError, ValueError):
        score = None
    if "operation_journal_unavailable" in text:
        return "needs_adjustment: Mongo operation journal must be configured before required live operations."
    if "margin is insufficient" in text or "-2019" in text:
        return "needs_adjustment: entry sizing or available-margin gate should be tightened before retry."
    if "entry_rejected" in text or "execution_error" in text:
        return "needs_adjustment: execution precheck did not match exchange constraints."
    if "protection_failed" in text or "critical_manual_intervention" in text:
        return "needs_adjustment: protection placement path needs immediate review."
    if "too close to mark" in text or "immediate trigger" in text:
        return "needs_adjustment: trigger distance guard should stay active or widen."
    if "weak_recent_volume" in text:
        return "needs_review: signal quality depends on thin recent flow; avoid scaling until liquidity improves."
    if score is not None and score < 70:
        return "needs_review: score is below the normal entry threshold."
    if "protective_order_maintenance" in text or "protective_order" in text:
        return "reasonable: operation is position defense; keep reviewing churn frequency."
    if score is not None and score >= 90:
        return "reasonable: high-score setup if margin, liquidity, and protection placement all confirm."
    return "reasonable: no obvious strategy degradation in the recorded reason."


def journal_mongo_summary(journal: list[dict[str, Any]], symbol: str | None = None) -> str:
    if not journal:
        return ""
    statuses = []
    for item in journal:
        if symbol and item.get("symbol") not in {symbol, None, ""}:
            continue
        status = (item.get("journal_status") or {}).get("mongo_status")
        if status:
            statuses.append(str(status))
    if not statuses:
        return ""
    return f" journal_mongo={','.join(sorted(set(statuses)))}"


def operation_reason_review_lines(executions: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in executions:
        if row.get("symbol") or row.get("operation_reason"):
            reason = row.get("operation_reason") or row.get("error") or row.get("status") or "-"
            assessment = operation_strategy_assessment(row, reason)
            journal_summary = journal_mongo_summary(row.get("operation_journal") or [])
            lines.append(
                f"- {row.get('time')} `{row.get('symbol') or '-'}` status={row.get('status') or '-'} "
                f"reason={compact_text(reason)} assessment={assessment}{journal_summary}"
            )
        for plan in row.get("plans") or []:
            reason = plan.get("operation_reason") or (
                f"protective_order_maintenance: {plan.get('symbol') or '-'} "
                f"stop={plan.get('current_stop')}->{plan.get('new_stop')} "
                f"take={plan.get('current_take_profit')}->{plan.get('new_take_profit')}"
            )
            market_structure_suffix = ""
            if plan.get("volume_price_regime"):
                market_structure_suffix += f" VP={plan.get('volume_price_regime')}/{plan.get('volume_price_score')}"
            if plan.get("order_book_imbalance") is not None:
                market_structure_suffix += (
                    f" OB_imb={plan.get('order_book_imbalance')} "
                    f"spread={plan.get('order_book_spread_bps')}bps"
                )
            elif plan.get("order_book_error"):
                market_structure_suffix += f" OB_error={plan.get('order_book_error')}"
            assessment = operation_strategy_assessment(plan, reason)
            journal_summary = journal_mongo_summary(row.get("operation_journal") or [], plan.get("symbol"))
            lines.append(
                f"- {row.get('time')} `{plan.get('symbol') or '-'}` protection "
                f"reason={compact_text(reason)}{market_structure_suffix} assessment={assessment}{journal_summary}"
            )
    return lines[-12:]


def summarize_protection_event(event: dict[str, Any]) -> str:
    event_type = event.get("type") or "-"
    if event_type == "protection_adjustment_skipped":
        symbols = ",".join(event.get("symbols") or []) or "-"
        unknown_suffix = " active_positions_unknown=true" if event.get("active_positions_unknown") else ""
        return (
            f"- {event.get('time')} {event_type} reason={event.get('reason') or '-'} "
            f"active_positions={event.get('active_positions', '-')} symbols={symbols} "
            f"retry_after={event.get('retry_after') or '-'}{unknown_suffix}"
        )
    error = event.get("error")
    if error:
        symbols = event.get("symbols") or [
            position.get("symbol")
            for position in event.get("positions", [])
            if position.get("symbol")
        ]
        symbol_label = ",".join(symbols) if symbols else "-"
        return (
            f"- {event.get('time')} {event_type} symbols={symbol_label} "
            f"error={error}{request_ip_suffix(event)}"
        )

    result = event.get("result") or {}
    plans = result.get("plans") or []
    symbols = ",".join(
        sorted({plan.get("symbol") for plan in plans if plan.get("symbol")})
    ) or "-"
    return f"- {event.get('time')} {event_type} symbols={symbols}"


def summarize_live_event(event: dict[str, Any]) -> str:
    event_type = event.get("type") or "-"
    if event_type == "live_position_precheck_backoff":
        positions = event.get("positions") or []
        symbols = ",".join(position.get("symbol") or "-" for position in positions) or "-"
        unknown_suffix = " active_positions_unknown=true" if event.get("active_positions_unknown") else ""
        return (
            f"- {event.get('time')} {event_type} active_positions={event.get('active_positions', len(positions))} "
            f"symbols={symbols} retry_after={event.get('retry_after') or '-'}{unknown_suffix}{request_ip_suffix(event)}"
        )
    if event_type == "live_position_precheck":
        positions = event.get("positions") or []
        symbols = ",".join(position.get("symbol") or "-" for position in positions) or "-"
        return (
            f"- {event.get('time')} {event_type} active_positions={event.get('active_positions', len(positions))} "
            f"symbols={symbols}"
        )
    if event_type == "live_position_precheck_error":
        positions = event.get("positions") or []
        symbols = ",".join(position.get("symbol") or "-" for position in positions) or "-"
        unknown_suffix = " active_positions_unknown=true" if event.get("active_positions_unknown") else ""
        return (
            f"- {event.get('time')} {event_type} active_positions={event.get('active_positions', len(positions))} "
            f"symbols={symbols} error={event.get('error') or '-'}{unknown_suffix}{request_ip_suffix(event)}"
        )
    if event_type == "live_position_market_scan_skipped":
        symbols = ",".join(event.get("symbols") or []) or "-"
        unknown_suffix = " active_positions_unknown=true" if event.get("active_positions_unknown") else ""
        return (
            f"- {event.get('time')} {event_type} reason={event.get('reason') or '-'} "
            f"active_positions={event.get('active_positions', '-')} symbols={symbols} "
            f"retry_after={event.get('retry_after') or '-'}{unknown_suffix}"
        )
    if event_type == "live_position_market_scan":
        symbols = ",".join(event.get("symbols") or []) or "-"
        return (
            f"- {event.get('time')} {event_type} symbols={symbols} "
            f"rows={event.get('rows', 0)} errors={len(event.get('errors') or [])}"
        )
    if event_type == "live_execution_backoff":
        return (
            f"- {event.get('time')} {event_type} reason={event.get('reason') or '-'} "
            f"symbol={event.get('symbol') or '-'} status={event.get('status') or '-'} "
            f"retry_after={event.get('retry_after') or '-'} error={event.get('error') or '-'}"
        )
    if event_type == "live_entry_skipped":
        symbols = ",".join(event.get("symbols") or []) or "-"
        unknown_suffix = " active_positions_unknown=true" if event.get("active_positions_unknown") else ""
        return (
            f"- {event.get('time')} {event_type} reason={event.get('reason') or '-'} "
            f"active_positions={event.get('active_positions', '-')} "
            f"max_live_positions={event.get('max_live_positions', '-')} "
            f"symbols={symbols} retry_after={event.get('retry_after') or '-'}{unknown_suffix}"
        )
    intent = event.get("intent") or {}
    result = event.get("result") or {}
    symbol = event.get("symbol") or intent.get("symbol") or result.get("symbol") or event.get("candidate", {}).get("symbol") or "-"
    reason = (
        event.get("reason")
        or event.get("message")
        or event.get("status")
        or result.get("status")
        or result.get("error")
        or "-"
    )
    return f"- {event.get('time')} {event_type} symbol={symbol} reason={reason}"


def latest_live_position_event(live_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(live_events):
        if event.get("type") == "live_position_precheck":
            return event
        if event.get("type") in {
            "live_position_precheck_backoff",
            "live_position_precheck_error",
        } and event.get("positions"):
            return event
    return None


def account_snapshot_line(event: dict[str, Any]) -> str | None:
    account = event.get("account")
    if not isinstance(account, dict) or not account:
        return None
    fields = [
        ("available", account.get("availableBalance")),
        ("wallet", account.get("totalWalletBalance")),
        ("margin", account.get("totalMarginBalance")),
        ("uPnL", account.get("totalUnrealizedProfit")),
    ]
    parts = [f"{label}={value}" for label, value in fields if value not in (None, "")]
    if not parts:
        return None
    return f"- Account balance snapshot: {' '.join(parts)} USDT."


def latest_protection_levels(executions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in executions:
        symbol = row.get("symbol")
        if symbol:
            stop_price = None
            take_price = None
            for order in row.get("risk_orders") or []:
                order_type = str(order.get("orderType") or order.get("type") or "")
                trigger = order.get("triggerPrice") or order.get("stopPrice")
                if order_type == "STOP_MARKET":
                    stop_price = trigger
                elif order_type == "TAKE_PROFIT_MARKET":
                    take_price = trigger
            latest[symbol] = {
                "stop": stop_price,
                "take": take_price,
                "source": row.get("time"),
            }
        for plan in row.get("plans") or []:
            plan_symbol = plan.get("symbol")
            if not plan_symbol:
                continue
            latest[plan_symbol] = {
                "stop": plan.get("new_stop") or plan.get("current_stop"),
                "take": plan.get("new_take_profit") or plan.get("current_take_profit"),
                "source": row.get("time"),
            }
    return latest


def current_live_position_lines(
    live_events: list[dict[str, Any]],
    all_executions: list[dict[str, Any]],
) -> list[str]:
    latest_event = latest_live_position_event(live_events)
    if not latest_event:
        return ["- No live position snapshots were recorded in this window."]

    positions = (latest_event or {}).get("positions") or []
    snapshot_type = str((latest_event or {}).get("type") or "-")
    snapshot_time = (latest_event or {}).get("time") or "-"
    account_line = account_snapshot_line(latest_event)
    snapshot_ts = parse_time(snapshot_time)
    later_unknown_events = [
        event
        for event in live_events
        if event.get("type") in {"live_position_precheck_error", "live_position_precheck_backoff"}
        and event.get("active_positions_unknown")
        and parse_time(event.get("time"))
        and (snapshot_ts is None or parse_time(event.get("time")) > snapshot_ts)
    ]
    latest_unknown = later_unknown_events[-1] if later_unknown_events else None
    if latest_unknown:
        unknown_time = latest_unknown.get("time") or "-"
        lines = [
            f"- Snapshot source: last successful live precheck at {snapshot_time}; a later precheck failed at {unknown_time}, so current exchange position and protection state is unknown."
        ]
        if account_line:
            lines.append(account_line.replace("Account balance snapshot:", "Account balance snapshot (last known):"))
        if not positions:
            lines.append(
                "- Last known snapshot had no active live positions; this is not confirmation that the account is currently flat."
            )
            return lines

        protection_levels = latest_protection_levels(all_executions)
        for position in positions:
            symbol = position.get("symbol") or "-"
            protection = protection_levels.get(symbol, {})
            lines.append(
                f"- Last known `{symbol}` {position.get('positionSide') or '-'} qty={position.get('positionAmt') or '-'} "
                f"entry={position.get('entryPrice') or '-'} mark={position.get('markPrice') or '-'} "
                f"uPnL={position.get('unRealizedProfit') or '-'} liq={position.get('liquidationPrice') or '-'} "
                f"stop={protection.get('stop') or '-'} take={protection.get('take') or '-'} "
                f"protection_source={protection.get('source') or '-'}"
            )
        return lines
    if not positions:
        if snapshot_type == "live_position_precheck":
            lines = [
                f"- Snapshot source: successful live precheck at {snapshot_time}.",
                "- No active live positions are open in the latest exchange snapshot.",
            ]
            if account_line:
                lines.insert(1, account_line)
            return lines
        return [
            f"- Snapshot source: last known {snapshot_type} at {snapshot_time} carried no position details; exchange truth could not be refreshed afterward."
        ]

    protection_levels = latest_protection_levels(all_executions)
    if snapshot_type == "live_position_precheck":
        lines = [f"- Snapshot source: successful live precheck at {snapshot_time}."]
    else:
        lines = [
            f"- Snapshot source: last known positions carried through {snapshot_type} at {snapshot_time}; exchange truth could not be refreshed afterward."
        ]
    if account_line:
        lines.append(account_line)
    for position in positions:
        symbol = position.get("symbol") or "-"
        protection = protection_levels.get(symbol, {})
        lines.append(
            f"- `{symbol}` {position.get('positionSide') or '-'} qty={position.get('positionAmt') or '-'} "
            f"entry={position.get('entryPrice') or '-'} mark={position.get('markPrice') or '-'} "
            f"uPnL={position.get('unRealizedProfit') or '-'} liq={position.get('liquidationPrice') or '-'} "
            f"stop={protection.get('stop') or '-'} take={protection.get('take') or '-'} "
            f"protection_source={protection.get('source') or '-'}"
        )
    return lines


def latest_successful_live_precheck_time(live_events: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for event in live_events:
        if event.get("type") != "live_position_precheck":
            continue
        ts = parse_time(event.get("time"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest


def pending_position_refresh_lines(
    live_events: list[dict[str, Any]],
    all_executions: list[dict[str, Any]],
) -> list[str]:
    latest_event = latest_live_position_event(live_events)
    if not latest_event:
        return []

    successful_precheck_time = latest_successful_live_precheck_time(live_events)
    known_symbols = {
        str(position.get("symbol"))
        for position in latest_event.get("positions") or []
        if position.get("symbol")
    }
    pending = []
    for row in all_executions:
        if row.get("mode") != "live" or row.get("status") != "protected":
            continue
        symbol = str(row.get("symbol") or "-")
        if symbol in known_symbols:
            continue
        row_time = parse_time(row.get("time"))
        if successful_precheck_time and row_time and row_time <= successful_precheck_time:
            continue
        pending.append(row)

    if not pending:
        return []

    protection_levels = latest_protection_levels(all_executions)
    lines = [
        "- Pending refresh: these live entries were protected after the latest position snapshot, so current open/closed status needs the next successful precheck."
    ]
    for row in pending:
        symbol = str(row.get("symbol") or "-")
        protection = protection_levels.get(symbol, {})
        lines.append(
            f"- `{symbol}` {row.get('direction') or '-'} qty={row.get('quantity') or '-'} "
            f"execution={row.get('time') or '-'} stop={protection.get('stop') or '-'} "
            f"take={protection.get('take') or '-'}"
        )
    return lines


def pending_live_intent_lines(
    intents: list[dict[str, Any]],
    all_executions: list[dict[str, Any]],
) -> list[str]:
    executed_intent_ids = {
        str(row.get("intent_id"))
        for row in all_executions
        if row.get("intent_id")
    }
    pending = []
    for intent in intents:
        intent_id = intent.get("id")
        if not intent_id:
            continue
        if str(intent_id) in executed_intent_ids:
            continue
        if intent.get("intent_type") not in {None, "", "entry"}:
            continue
        pending.append(intent)

    if not pending:
        return []

    lines = [
        "- Pending live intent: no execution result has been recorded yet, so it may still be awaiting GUI confirmation or the next service event."
    ]
    for intent in pending[-5:]:
        risk_orders = intent.get("risk_orders") or []
        stop = risk_orders[0].get("stop_price") if len(risk_orders) > 0 else "-"
        take = risk_orders[1].get("stop_price") if len(risk_orders) > 1 else "-"
        lines.append(
            f"- `{intent.get('symbol') or '-'}` {intent.get('direction') or '-'} "
            f"intent={intent.get('time') or '-'} score={intent.get('score') or '-'} "
            f"stop={stop or '-'} take={take or '-'}"
        )
    return lines


def count_events(events: list[dict[str, Any]], event_type: str, reason: str | None = None) -> int:
    total = 0
    for event in events:
        if event.get("type") != event_type:
            continue
        if reason is not None and event.get("reason") != reason:
            continue
        total += 1
    return total


def skipped_full_market_scan_reason_counts(live_events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in live_events:
        if event.get("type") != "live_entry_skipped" or not event.get("skipped_full_market_scan"):
            continue
        reason = str(event.get("reason") or "-")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def render_skip_reason_summary(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    labels = {
        "insufficient_available_balance": "available balance was below the live entry floor",
        "max_live_positions_reached": "live positions stayed at cap",
        "live_position_precheck_error": "live position precheck failed",
        "account_available_balance_unavailable": "account balance snapshot was unavailable",
        "live_execution_backoff": "recent live execution failed",
        "margin_insufficient": "available margin was insufficient",
        "position_capacity_limit": "exchange position capacity was reached",
        "exchange_rate_limited": "exchange rate limits were active",
        "confirmation_timeout": "live confirmation timed out",
    }
    ordered_reasons = (
        "max_live_positions_reached",
        "live_position_precheck_error",
        "account_available_balance_unavailable",
        "live_execution_backoff",
        "margin_insufficient",
        "position_capacity_limit",
        "exchange_rate_limited",
        "confirmation_timeout",
    )
    parts = []
    for reason in ordered_reasons:
        count = counts.get(reason)
        if count:
            parts.append(f"{count} times because {labels.get(reason, reason)}")
    for reason, count in sorted(counts.items()):
        if reason in ordered_reasons:
            continue
        parts.append(f"{count} times because {labels.get(reason, reason)}")
    return "; ".join(parts)


def runtime_guard_stale_feed_note(
    label_name: str,
    ts: datetime,
    since: datetime,
    now: datetime,
    skipped_full_market_scan_counts: dict[str, int],
    skipped_full_market_scan_summary: str,
    has_recent_records: bool,
) -> str | None:
    if (
        has_recent_records
        or not skipped_full_market_scan_counts
        or ts >= since
    ):
        return None

    age_minutes = int((now - ts).total_seconds() // 60)
    seen_at = ts.strftime("%Y-%m-%d %H:%M:%S %z")
    if label_name == "scan":
        return (
            f"- Scan feed is paused by runtime guards: full-market scans were skipped {skipped_full_market_scan_summary}; "
            f"last broad scan was {seen_at} ({age_minutes} minutes old)."
        )
    if label_name == "intent":
        return (
            f"- Intent feed is blocked by the same guards: no new entry intents were allowed while full-market scans were skipped {skipped_full_market_scan_summary}; "
            f"last intent was {seen_at} ({age_minutes} minutes old)."
        )
    if label_name == "execution":
        return (
            f"- Execution feed is idle under the same entry guards: no new entry intents reached live execution while full-market scans were skipped {skipped_full_market_scan_summary}; "
            f"last execution was {seen_at} ({age_minutes} minutes old)."
        )
    return None


def repeated_candidate_notes(scans: list[dict[str, Any]]) -> list[str]:
    candidate_counts: dict[tuple[str, str, str], int] = {}
    weak_volume_hits = 0
    volume_price_risk_hits = 0
    order_book_risk_hits = 0
    for scan in scans:
        for row in scan.get("top_candidates", []):
            key = (row.get("symbol") or "-", row.get("action") or "-", row.get("direction") or "-")
            candidate_counts[key] = candidate_counts.get(key, 0) + 1
            risk_flags = str(row.get("risk_flags") or "")
            if "weak_recent_volume" in risk_flags:
                weak_volume_hits += 1
            if "volume_price_" in risk_flags:
                volume_price_risk_hits += 1
            if "order_book_" in risk_flags:
                order_book_risk_hits += 1
    frequent = [
        item for item in sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0][0]))
        if item[1] > 1
    ][:5]
    notes = []
    if frequent:
        rendered = ", ".join(f"{symbol} {action} x{count}" for (symbol, action, _direction), count in frequent)
        notes.append(f"- Repeated candidates: {rendered}.")
    if weak_volume_hits:
        notes.append(f"- Weak-volume risk flags appeared {weak_volume_hits} times in top candidates; require follow-through volume before treating those names as fresh momentum.")
    if volume_price_risk_hits:
        notes.append(f"- Volume/price structure risk appeared {volume_price_risk_hits} times; high price scores should be downgraded when 30-day volume-price behavior or current volume confirmation is adverse.")
    if order_book_risk_hits:
        notes.append(f"- Order-book pressure risk appeared {order_book_risk_hits} times; avoid entries when spread, imbalance, or nearby resting liquidity fights the signal.")
    return notes


def protection_anomaly_notes(protection_events: list[dict[str, Any]]) -> list[str]:
    notes = []
    for event in protection_events:
        result = event.get("result") or {}
        plans = result.get("plans") or []
        executions = result.get("executions") or []
        missing_current = [
            plan for plan in plans
            if plan.get("current_stop") is None and plan.get("current_take_profit") is None
        ]
        if not missing_current:
            continue
        symbols = ",".join(plan.get("symbol") or "-" for plan in missing_current)
        zero_cancelled = all(not execution.get("cancelled") for execution in executions)
        lookup_sources = sorted(
            {
                plan.get("order_lookup_source")
                for plan in missing_current
                if plan.get("order_lookup_source")
            }
        )
        source_label = ",".join(lookup_sources) if lookup_sources else "-"
        history_parts = []
        for plan in missing_current:
            history = plan.get("recent_order_history") or []
            if not history:
                continue
            latest = history[0]
            history_parts.append(
                f"{plan.get('symbol')} latest={latest.get('algoStatus') or '-'} "
                f"{latest.get('orderType') or '-'} trigger={latest.get('triggerPrice') or '-'} "
                f"updated={latest.get('updateTime') or '-'}"
            )
        history_label = "; ".join(history_parts) if history_parts else "no recent algo history"
        notes.append(
            f"- {event.get('time')} protection lookup gap on {symbols}: current stop/take were missing before replacement; cancelled_existing={not zero_cancelled}; lookup_source={source_label}; history={history_label}."
        )
    return notes


def live_event_notes(live_events: list[dict[str, Any]]) -> list[str]:
    backoff_count = count_events(live_events, "live_position_precheck_backoff")
    precheck_skip_count = count_events(live_events, "live_entry_skipped", "live_position_precheck_error")
    precheck_backoff_skip_count = count_events(live_events, "live_entry_skipped", "live_position_precheck_backoff")
    precheck_guard_skip_count = precheck_skip_count + precheck_backoff_skip_count
    insufficient_balance_skip_count = count_events(live_events, "live_entry_skipped", "insufficient_available_balance")
    execution_backoff_skip_count = sum(
        count_events(live_events, "live_entry_skipped", reason)
        for reason in LIVE_EXECUTION_BACKOFF_REASONS
    )
    skip_count = count_events(live_events, "live_entry_skipped", "max_live_positions_reached")
    notes = []
    if backoff_count:
        notes.append(
            f"- Live position precheck entered backoff {backoff_count} times after repeated auth-like failures; the service was defending known positions instead of continuing noisy retry loops."
        )
    if precheck_guard_skip_count:
        notes.append(
            f"- Live entry was skipped {precheck_guard_skip_count} times because the live position precheck failed or was in backoff; treat this hour as access-path degradation, not a deliberate no-trade decision."
        )
    if insufficient_balance_skip_count:
        notes.append(
            f"- Live entry was skipped {insufficient_balance_skip_count} times because available balance stayed below the configured live-entry floor; this is guarded inactivity, not scan or strategy degradation."
        )
    if execution_backoff_skip_count:
        notes.append(
            f"- Live entry was skipped {execution_backoff_skip_count} times because a recent live execution failed; the service was cooling down entry attempts while keeping protection maintenance active."
        )
    if skip_count:
        notes.append(
            f"- Live entry was skipped {skip_count} times because the live position cap was already full; this hour was dominated by protection maintenance, not new opportunity discovery."
        )
    return notes


def stale_feed_labels(
    last_seen: dict[str, datetime | None],
    since: datetime,
    explained_labels: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    stale = []
    missing = []
    explained = explained_labels or set()
    for label_name, ts in last_seen.items():
        if label_name in explained:
            continue
        if ts is None:
            missing.append(label_name)
        elif ts < since:
            stale.append(label_name)
    return stale, missing


def feed_staleness_anomaly_notes(
    last_seen: dict[str, datetime | None],
    since: datetime,
    explained_labels: set[str] | None = None,
) -> list[str]:
    stale, missing = stale_feed_labels(last_seen, since, explained_labels)
    if not stale and not missing:
        return []

    parts = []
    if stale:
        parts.append(f"stale={','.join(stale)}")
    if missing:
        parts.append(f"missing={','.join(missing)}")
    severity = "Core data feeds are stale or missing" if len(stale) + len(missing) >= 3 else "Feed freshness degraded"
    return [
        f"- {severity} ({'; '.join(parts)}); treat empty scans/intents/executions as producer health evidence, not a clean no-trade signal."
    ]


def feed_staleness_lesson_notes(
    last_seen: dict[str, datetime | None],
    since: datetime,
    explained_labels: set[str] | None = None,
) -> list[str]:
    stale, missing = stale_feed_labels(last_seen, since, explained_labels)
    if not stale and not missing:
        return []
    return [
        "- Empty hourly windows require a freshness check first; stale feeds mean restart or repair the producer before evaluating strategy quality."
    ]


def error_event_notes(events: list[dict[str, Any]]) -> list[str]:
    grouped: dict[tuple[str, str], int] = {}
    grouped_symbols: dict[tuple[str, str], set[str]] = {}
    grouped_ips: dict[tuple[str, str], set[str]] = {}
    for event in events:
        event_type = str(event.get("type") or "-")
        error = normalize_error_text(str(event.get("error") or event.get("result", {}).get("error") or ""))
        if not error and not event_type.endswith("_error"):
            continue
        key = (event_type, error or "unknown error")
        grouped[key] = grouped.get(key, 0) + 1
        symbols = event.get("symbols") or [
            position.get("symbol")
            for position in event.get("positions", [])
            if position.get("symbol")
        ]
        if symbols:
            grouped_symbols.setdefault(key, set()).update(str(symbol) for symbol in symbols)
        request_ips = event_request_ips(event)
        if request_ips:
            grouped_ips.setdefault(key, set()).update(request_ips)
    notes = []
    for (event_type, error), count in sorted(grouped.items(), key=lambda item: (-item[1], item[0][0]))[:5]:
        symbols = sorted(grouped_symbols.get((event_type, error), set()))
        suffix = f" symbols={','.join(symbols)}" if symbols else ""
        ips = sorted(grouped_ips.get((event_type, error), set()))
        ip_suffix = f" request_ip={','.join(ips)}" if ips else ""
        notes.append(f"- {event_type} occurred {count} time(s){suffix}{ip_suffix}: {error}.")
    return notes


def has_repeated_candidates(scans: list[dict[str, Any]]) -> bool:
    candidate_counts: dict[tuple[str, str, str], int] = {}
    for scan in scans:
        for row in scan.get("top_candidates", []):
            key = (row.get("symbol") or "-", row.get("action") or "-", row.get("direction") or "-")
            candidate_counts[key] = candidate_counts.get(key, 0) + 1
    return any(count > 1 for count in candidate_counts.values())


def has_weak_volume_candidates(scans: list[dict[str, Any]]) -> bool:
    for scan in scans:
        for row in scan.get("top_candidates", []):
            if "weak_recent_volume" in str(row.get("risk_flags") or ""):
                return True
    return False


def has_volume_price_or_order_book_risk(scans: list[dict[str, Any]]) -> bool:
    for scan in scans:
        for row in scan.get("top_candidates", []):
            risk_flags = str(row.get("risk_flags") or "")
            if "volume_price_" in risk_flags or "order_book_" in risk_flags:
                return True
    return False


def event_error_text(event: dict[str, Any]) -> str:
    return normalize_error_text(str(event.get("error") or event.get("result", {}).get("error") or ""))


def has_margin_insufficient_error(events: list[dict[str, Any]]) -> bool:
    for event in events:
        text = event_error_text(event).lower()
        if "margin is insufficient" in text or '"code":-2019' in text:
            return True
    return False


def has_access_path_error(events: list[dict[str, Any]]) -> bool:
    access_markers = (
        "http 401",
        '"code":-2015',
        '"code":-1003',
        "http 418",
        "too many requests",
        "banned until",
        "permissionerror",
        "filenotfounderror",
        "env",
    )
    for event in events:
        text = event_error_text(event).lower()
        if any(marker in text for marker in access_markers):
            return True
    return False


def lesson_notes(scans: list[dict[str, Any]], protection_events: list[dict[str, Any]], live_events: list[dict[str, Any]]) -> list[str]:
    notes = []
    error_events = [
        event for event in protection_events + live_events
        if str(event.get("type") or "").endswith("_error") or event.get("error") or event.get("result", {}).get("error")
    ]
    skip_counts = skipped_full_market_scan_reason_counts(live_events)
    execution_backoff_reasons = [
        reason
        for reason in sorted(LIVE_EXECUTION_BACKOFF_REASONS)
        if skip_counts.get(reason)
    ]
    if has_repeated_candidates(scans):
        notes.append("- Repeated watchlist names should be filtered by persistence plus liquidity quality, otherwise the scanner keeps surfacing the same weak-volume pullbacks.")
    if has_weak_volume_candidates(scans):
        notes.append("- Weak-volume candidates should stay in review unless follow-through volume confirms; do not treat a single high score as fresh momentum.")
    if has_volume_price_or_order_book_risk(scans):
        notes.append("- Price-only momentum is not enough: require 30-day volume-price profile confirmation and a non-hostile order book before allowing clean live-confirm candidates.")
    if protection_anomaly_notes(protection_events):
        notes.append("- Protection maintenance must distrust empty aggregate order snapshots and re-check per symbol before assuming protection is missing.")
    if has_access_path_error(error_events):
        notes.append("- Access-path failures should short-circuit downstream maintenance so one env-file read error does not fan out into duplicated protection noise.")
    if has_margin_insufficient_error(error_events):
        notes.append("- Margin-insufficient rejections are sizing/capacity failures; cool down new entries and tighten the available-margin gate before retrying.")
    if skip_counts.get("max_live_positions_reached"):
        notes.append("- When live slots are full, the service should optimize for position defense and review quality instead of treating the hour as an entry-generation cycle.")
    if execution_backoff_reasons:
        rendered = ", ".join(execution_backoff_reasons)
        notes.append(f"- After live entry rejection or timeout ({rendered}), the service should cool down new entries while keeping active-position protection maintenance running.")
    elif any(event.get("type") == "live_entry_skipped" for event in live_events):
        notes.append("- Skipped live-entry cycles need reason-specific review; do not assume every skip means the live position cap was full.")
    if not notes:
        notes.append("- No new structural lesson was obvious in this window.")
    return notes


def build_report(hours: int, output_dir: Path) -> Path:
    now = datetime.now(CST)
    since = now - timedelta(hours=hours)
    all_scans = read_jsonl(DEFAULT_SCAN_AUDIT)
    all_events = read_jsonl(DEFAULT_EVENTS)
    all_intents = read_jsonl(DEFAULT_INTENTS)
    all_executions = read_jsonl(DEFAULT_EXECUTIONS)
    scans = recent(all_scans, since)
    events = recent(all_events, since)
    intents = recent(all_intents, since)
    executions = recent(all_executions, since)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{label()}-quant-review.md"

    latest_scan = scans[-1] if scans else {}
    top_candidates = latest_scan.get("top_candidates", [])[:10]
    protection_events = [event for event in events if "protection" in str(event.get("type", ""))]
    live_events = [event for event in events if str(event.get("type", "")).startswith("live_")]
    max_position_skip_count = count_events(live_events, "live_entry_skipped", "max_live_positions_reached")
    skipped_full_market_scan_counts = skipped_full_market_scan_reason_counts(live_events)
    skipped_full_market_scan_summary = render_skip_reason_summary(skipped_full_market_scan_counts)
    runtime_guard_explained_feeds = (
        {"scan", "intent", "execution"}
        if skipped_full_market_scan_counts
        else set()
    )
    error_events = [
        event for event in events
        if str(event.get("type") or "").endswith("_error") or event.get("error") or event.get("result", {}).get("error")
    ]
    last_seen = {
        "scan": latest_datetime(all_scans),
        "event": latest_datetime(all_events),
        "intent": latest_datetime(all_intents),
        "execution": latest_datetime(all_executions),
    }
    anomaly_notes = (
        feed_staleness_anomaly_notes(last_seen, since, runtime_guard_explained_feeds)
        + repeated_candidate_notes(scans)
        + protection_anomaly_notes(protection_events)
        + error_event_notes(error_events)
        + live_event_notes(live_events)
    )
    freshness_lessons = feed_staleness_lesson_notes(last_seen, since, runtime_guard_explained_feeds)
    strategy_lessons = lesson_notes(scans, protection_events, live_events)
    if freshness_lessons and strategy_lessons == ["- No new structural lesson was obvious in this window."]:
        lessons = freshness_lessons
    else:
        lessons = freshness_lessons + strategy_lessons
    operation_reviews = operation_reason_review_lines(executions)
    stale_notes = []
    recent_records_by_label = {
        "scan": scans,
        "intent": intents,
        "execution": executions,
    }
    for label_name, ts in last_seen.items():
        if ts is None:
            stale_notes.append(f"- No {label_name} records have ever been captured.")
            continue
        runtime_guard_note = runtime_guard_stale_feed_note(
            label_name,
            ts,
            since,
            now,
            skipped_full_market_scan_counts,
            skipped_full_market_scan_summary,
            bool(recent_records_by_label.get(label_name)),
        )
        if runtime_guard_note:
            stale_notes.append(runtime_guard_note)
            continue
        if ts < since:
            age_minutes = int((now - ts).total_seconds() // 60)
            stale_notes.append(
                f"- {label_name.capitalize()} feed is stale: last record at "
                f"{ts.strftime('%Y-%m-%d %H:%M:%S %z')} ({age_minutes} minutes old)."
            )

    candidate_context = None
    if not top_candidates and skipped_full_market_scan_counts and all_scans:
        latest_scan = all_scans[-1]
        top_candidates = latest_scan.get("top_candidates", [])[:10]
        candidate_context = (
            f"- No new broad-market candidates were recorded in this window because full scans were skipped {skipped_full_market_scan_summary}; "
            f"showing the last broad scan from {latest_scan.get('time')}."
        )

    lines = [
        "# Hourly Quant Review",
        "",
        f"- Window: last {hours} hour(s)",
        f"- Generated: {now.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"- Scans: {len(scans)}",
        f"- Intents: {len(intents)}",
        f"- Live execution records: {len(executions)}",
        f"- Protection events: {len(protection_events)}",
        f"- Last scan seen: {latest_time(all_scans)}",
        f"- Last event seen: {latest_time(all_events)}",
        f"- Last intent seen: {latest_time(all_intents)}",
        f"- Last execution seen: {latest_time(all_executions)}",
        "",
    ]
    if stale_notes:
        lines += ["## Feed Freshness", ""]
        lines.extend(stale_notes)
        lines.append("")

    lines += [
        "## Latest Candidates",
        "",
    ]
    if candidate_context:
        lines.append(candidate_context)
    if top_candidates:
        lines.extend(f"- {summarize_candidate(row)}" for row in top_candidates)
    else:
        lines.append("- No candidates recorded.")

    lines += ["", "## Live Intents", ""]
    if intents:
        for intent in intents[-10:]:
            snap = intent.get("market_snapshot", {})
            lines.append(
                f"- `{intent.get('symbol')}` {intent.get('direction')} score={intent.get('score')} "
                f"notional={intent.get('entry_order', {}).get('notional')} "
                f"stop={intent.get('risk_orders', [{}])[0].get('stop_price')} "
                f"take={intent.get('risk_orders', [{}, {}])[1].get('stop_price')} "
                f"strategy={snap.get('primary_strategy')}"
            )
    else:
        lines.append("- No intents in this window.")
    pending_intents = pending_live_intent_lines(intents, all_executions)
    if pending_intents:
        lines.extend([""] + pending_intents)

    lines += ["", "## Current Live Positions", ""]
    lines.extend(current_live_position_lines(live_events, all_executions))
    pending_refresh = pending_position_refresh_lines(live_events, all_executions)
    if pending_refresh:
        lines.extend([""] + pending_refresh)

    lines += ["", "## Executions", ""]
    if executions:
        for item in executions[-10:]:
            lines.append(summarize_execution(item))
    else:
        lines.append("- No execution records in this window.")

    lines += ["", "## Operation Reason And Strategy Review", ""]
    if operation_reviews:
        lines.extend(operation_reviews)
    else:
        lines.append("- No position or protection operation reasons were recorded in this window.")

    lines += ["", "## Protection", ""]
    if protection_events:
        for event in protection_events[-10:]:
            lines.append(summarize_protection_event(event))
    else:
        lines.append("- No protection changes in this window.")

    lines += ["", "## Live Events", ""]
    if live_events:
        for event in live_events[-10:]:
            lines.append(summarize_live_event(event))
    else:
        lines.append("- No live events in this window.")

    lines += ["", "## Key Anomalies", ""]
    if anomaly_notes:
        lines.extend(anomaly_notes)
    else:
        lines.append("- No clear anomaly was detected in this window.")

    lines += ["", "## Lessons Learned", ""]
    lines.extend(lessons)

    lines += [
        "",
        "## Review Notes",
        "",
        "- Review every position/protection operation reason and confirm whether the assessment is `reasonable`, `needs_review`, or `needs_adjustment`.",
        "- Check whether repeated candidates are caused by trend quality or low volume.",
        "- Check whether live execution errors indicate exchange precision, hedge mode, or protection-order issues.",
        "- Do not loosen risk controls without a separate manual review.",
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--output-dir", default=str(DEFAULT_REPORT_DIR))
    args = parser.parse_args()
    print(build_report(args.hours, Path(args.output_dir)))


if __name__ == "__main__":
    main()

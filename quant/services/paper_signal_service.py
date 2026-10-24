#!/usr/bin/env python3
"""Paper signal service for Binance USD-M trend opportunities.

By default this service scans public market data, creates paper order intents,
manages one simulated position, and records stop-loss / take-profit outcomes for
later review. With ``--confirm-live`` it also runs the guarded confirm-then-live
path for newly generated intents and live protection maintenance.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
import warnings
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import ceil
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant.screeners.binance_usdm_15m_trend_screener import BASE_URL, CST, http_get, scan_market, scan_symbols
from quant.services.operation_journal import append_operation_record, operation_journal_required, operation_reason_from_intent
from quant.services.risk_model import adaptive_exit_prices


DEFAULT_CONFIG = Path("config/paper-signal-service.example.json")
DEFAULT_STATE = Path("data/processed/paper-signal-service/state.json")
DEFAULT_EVENTS = Path("data/processed/paper-signal-service/events.jsonl")
DEFAULT_INTENTS = Path("data/processed/paper-signal-service/intents.jsonl")
DEFAULT_REPORT = Path("reports/service/paper-signal-service.md")
SHUTDOWN_REQUESTED = False


def request_shutdown(signum: int, _frame: Any) -> None:
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True


def install_shutdown_handlers() -> None:
    for signal_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, signal_name, None)
        if signum is not None:
            signal.signal(signum, request_shutdown)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %z")


def scanner_order_book_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    scanner = config.get("scanner", {})
    return {
        "order_book_enabled": bool(scanner.get("order_book_enabled", True)),
        "order_book_levels": int(scanner.get("order_book_levels", 20)),
        "order_book_timeout_seconds": float(scanner.get("order_book_timeout_seconds", 2.0)),
    }


def scan_sleep_seconds(config: dict[str, Any], now: datetime | None = None) -> int:
    service = config.get("service", {})
    base_interval = max(0, int(service.get("scan_interval_seconds", 0)))
    boundary_interval = max(0, int(service.get("boundary_scan_interval_seconds", base_interval)))
    fast_window = max(0, int(service.get("boundary_fast_window_seconds", 0)))
    if fast_window <= 0 or boundary_interval >= base_interval:
        return base_interval
    now_dt = now or datetime.now(CST)
    epoch = int(now_dt.timestamp())
    for period in (900, 3600):
        offset = epoch % period
        if min(offset, period - offset) <= fast_window:
            return boundary_interval
    return base_interval


def service_loop_sleep_seconds(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    confirm_live: bool = False,
    now: datetime | None = None,
) -> int:
    sleep_seconds = scan_sleep_seconds(config, now=now)
    if not confirm_live:
        return sleep_seconds

    service = config.get("service", {})
    floor_seconds = max(
        0,
        int(
            service.get(
                "confirm_live_min_sleep_seconds",
                service.get("confirm_live_active_position_min_sleep_seconds", 30),
            )
        ),
    )
    if state.get("last_live_positions"):
        floor_seconds = max(
            floor_seconds,
            max(0, int(service.get("confirm_live_active_position_min_sleep_seconds", 30))),
        )

    now_dt = now or datetime.now(CST)
    retry_after = parse_cst((state.get("last_live_position_precheck") or {}).get("retry_after"))
    if retry_after is None:
        retry_after = parse_cst(state.get("live_execution_backoff_until"))
    market_scan_retry_after = parse_cst(state.get("market_scan_backoff_until"))
    if market_scan_retry_after is not None and (
        retry_after is None or market_scan_retry_after < retry_after
    ):
        retry_after = market_scan_retry_after
    if retry_after is not None and retry_after > now_dt:
        backoff_floor = max(0, int(service.get("confirm_live_backoff_min_sleep_seconds", 30)))
        remaining_seconds = max(1, ceil((retry_after - now_dt).total_seconds()))
        floor_seconds = min(backoff_floor, remaining_seconds)

    return max(sleep_seconds, floor_seconds)


def service_loop_stop_reason(
    *,
    once: bool,
    shutdown_requested: bool,
    iterations: int,
    completed: int,
) -> str | None:
    if once:
        return "once"
    if shutdown_requested:
        return "signal_requested"
    if iterations and completed >= iterations:
        return "iterations_completed"
    return None


def print_service_log(row: dict[str, Any]) -> None:
    print(json.dumps(row, ensure_ascii=False), flush=True)


def print_service_shutdown(reason: str, completed: int) -> None:
    print_service_log(
        {
            "time": now_cst(),
            "event": "service_shutdown",
            "reason": reason,
            "completed_iterations": completed,
        }
    )


def parse_cst(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "mode": "paper",
            "paper_equity": None,
            "open_position": None,
            "closed_positions": [],
            "last_scan": None,
            "last_candidates": [],
            "last_errors": [],
        }
    return json.loads(path.read_text())


def save_state(path: Path, state: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def append_event(path: Path, event: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_intent(path: Path, intent: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(intent, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def stable_position_signature(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stable_fields = (
        "symbol",
        "positionSide",
        "positionAmt",
        "entryPrice",
        "breakEvenPrice",
        "leverage",
        "marginType",
    )
    return [{field: position.get(field) for field in stable_fields if field in position} for position in positions]


def event_signature_value(event: dict[str, Any], field: str) -> Any:
    if field == "stable_positions":
        positions = event.get("positions") or []
        if isinstance(positions, list):
            return stable_position_signature([position for position in positions if isinstance(position, dict)])
        return []
    return event.get(field)


def event_signature(event: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(json.dumps(event_signature_value(event, field), ensure_ascii=False, sort_keys=True) for field in fields)


def append_event_dedup(
    path: Path,
    event: dict[str, Any],
    state: dict[str, Any],
    signature_state_key: str,
    signature_fields: tuple[str, ...],
    heartbeat_seconds: int = 0,
) -> bool:
    signature = event_signature(event, signature_fields)
    written_at_key = f"{signature_state_key}_written_at"
    event_time = parse_cst(event.get("time")) or datetime.now(CST)
    if state.get(signature_state_key) == signature:
        if heartbeat_seconds <= 0:
            return False
        last_written_at = parse_cst(state.get(written_at_key))
        if last_written_at and event_time - last_written_at < timedelta(seconds=heartbeat_seconds):
            return False
    append_event(path, event)
    state[signature_state_key] = signature
    state[written_at_key] = event_time.strftime("%Y-%m-%d %H:%M:%S %z")
    return True


def live_entry_skip_heartbeat_seconds(config: dict[str, Any]) -> int:
    service = config.get("service", {})
    return max(0, int(service.get("live_entry_skip_heartbeat_seconds", 300)))


def clear_event_dedup_signatures(state: dict[str, Any], *keys: str) -> None:
    for key in keys:
        state.pop(key, None)


def latest_successful_live_precheck_at(events_path: Path, state: dict[str, Any]) -> datetime | None:
    cached = parse_cst(state.get("last_successful_live_precheck_at"))
    if cached is not None:
        return cached
    latest: datetime | None = None
    for event in read_jsonl(events_path):
        if event.get("type") != "live_position_precheck":
            continue
        ts = parse_cst(event.get("time"))
        if ts and (latest is None or ts > latest):
            latest = ts
    if latest is not None:
        state["last_successful_live_precheck_at"] = latest.strftime("%Y-%m-%d %H:%M:%S %z")
    return latest


def live_intent_event(intent: dict[str, Any]) -> dict[str, Any]:
    snapshot = intent.get("market_snapshot", {})
    return {
        "time": now_cst(),
        "type": "live_intent_created",
        "symbol": intent.get("symbol"),
        "direction": intent.get("direction"),
        "score": intent.get("score"),
        "primary_strategy": snapshot.get("primary_strategy"),
        "risk_flags": intent.get("risk_flags"),
        "intent": intent,
    }


def live_execution_event(result: dict[str, Any]) -> dict[str, Any]:
    event = {
        "time": now_cst(),
        "type": "live_execution_result",
        "symbol": result.get("symbol"),
        "status": result.get("status"),
        "error": result.get("error"),
        "result": result,
    }
    request_ip = result.get("request_ip") or extract_request_ip(str(result.get("error") or ""))
    if request_ip:
        event["request_ip"] = request_ip
    error_code = result.get("error_code")
    if error_code is None:
        error_code = extract_binance_error_code(str(result.get("error") or ""))
    if error_code is not None:
        event["error_code"] = error_code
    return event


def market_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "symbol",
        "action",
        "direction",
        "score",
        "long_score",
        "short_score",
        "last_price",
        "price_change_pct_24h",
        "quote_volume_24h",
        "primary_strategy",
        "strategy_tags",
        "rsi14_15m",
        "atr_pct_15m",
        "volume_ratio",
        "micro_volume_ratio",
        "micro_trend_1m_pct",
        "micro_trend_5m_pct",
        "taker_buy_ratio_1m",
        "taker_buy_ratio_15m",
        "recent_trade_buy_ratio",
        "recent_trade_imbalance",
        "recent_trade_quote",
        "volume_price_score",
        "volume_price_regime",
        "volume_price_notes",
        "order_book_imbalance",
        "order_book_spread_bps",
        "order_book_bid_wall_ratio",
        "order_book_ask_wall_ratio",
        "order_book_error",
        "funding_rate",
        "bb_width_pct",
        "trend_15m_pct",
        "trend_1h_pct",
        "price_vs_ema21_pct",
        "price_vs_ema55_pct",
        "reasons",
        "risk_flags",
    ]
    return {field: row.get(field) for field in fields if field in row}


def append_scan_audit(path: Path, rows: list[dict[str, Any]], errors: list[dict[str, str]], symbols_scanned: int) -> None:
    watch_rows = [row for row in rows if "WATCH" in row["action"]]
    append_jsonl(
        path,
        {
            "time": now_cst(),
            "symbols_scanned": symbols_scanned,
            "rows": len(rows),
            "errors": errors[:20],
            "top_candidates": [market_snapshot(row) for row in watch_rows[:20]],
            "top_by_score": [market_snapshot(row) for row in sorted(rows, key=lambda item: -float(item["score"]))[:20]],
        },
    )


def auth_like_live_precheck_error(message: str) -> bool:
    text = message.lower()
    return (
        "http 401" in text
        or '"code":-2015' in text
        or "invalid api-key" in text
        or '"code":-1003' in text
        or "http 418" in text
        or "418 client error" in text
        or "too many requests" in text
        or "banned until" in text
        or "i'm a teapot" in text
    )


def extract_request_ip(message: str) -> str | None:
    match = re.search(r"request ip: ([^\"}]+)", message, re.IGNORECASE)
    if not match:
        match = re.search(r"\bIP\(([^)]+)\)", message)
    if not match:
        return None
    return match.group(1).strip().rstrip("\\")


def extract_binance_error_code(message: str) -> int | None:
    match = re.search(r'"code":\s*(-?\d+)', message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def extract_binance_ban_until(message: str) -> datetime | None:
    match = re.search(r"banned until\s+(\d{10,})", message, re.IGNORECASE)
    if not match:
        return None
    try:
        raw_timestamp = int(match.group(1))
    except ValueError:
        return None
    timestamp_seconds = raw_timestamp / 1000 if raw_timestamp > 10_000_000_000 else raw_timestamp
    try:
        return datetime.fromtimestamp(timestamp_seconds, timezone.utc).astimezone(CST)
    except (OverflowError, OSError, ValueError):
        return None


def rate_limit_retry_after(
    config: dict[str, Any],
    message: str,
    default_retry_after: datetime,
) -> datetime:
    ban_until = extract_binance_ban_until(message)
    if ban_until is None:
        return default_retry_after
    service = config.get("service", {})
    grace_seconds = max(0, int(service.get("binance_rate_limit_ban_grace_seconds", 30)))
    return max(default_retry_after, ban_until + timedelta(seconds=grace_seconds))


def live_precheck_backoff_retry_after(
    config: dict[str, Any],
    message: str,
    *,
    base_time: datetime | None = None,
) -> datetime:
    service = config.get("service", {})
    cooldown = max(30, int(service.get("live_precheck_auth_backoff_seconds", 180)))
    default_retry_after = (base_time or datetime.now(CST)) + timedelta(seconds=cooldown)
    return rate_limit_retry_after(config, message, default_retry_after)


def market_scan_backoff_reason(message: str) -> str:
    text = message.lower()
    error_code = extract_binance_error_code(message)
    if (
        error_code == -1003
        or "http 418" in text
        or "418 client error" in text
        or "too many requests" in text
        or "banned until" in text
        or "i'm a teapot" in text
    ):
        return "exchange_rate_limited"
    return "market_scan_error"


def market_scan_backoff_seconds(config: dict[str, Any], reason: str) -> int:
    service = config.get("service", {})
    default_cooldown = max(30, int(service.get("live_execution_backoff_seconds", 900)))
    if reason == "exchange_rate_limited":
        return max(30, int(service.get("market_scan_rate_limit_backoff_seconds", default_cooldown)))
    return max(30, int(service.get("market_scan_backoff_seconds", default_cooldown)))


def set_market_scan_backoff(
    state: dict[str, Any],
    error_message: str,
    config: dict[str, Any],
    *,
    base_time: datetime | None = None,
) -> None:
    reason = market_scan_backoff_reason(error_message)
    retry_after = (base_time or datetime.now(CST)) + timedelta(
        seconds=market_scan_backoff_seconds(config, reason)
    )
    if reason == "exchange_rate_limited":
        retry_after = rate_limit_retry_after(config, error_message, retry_after)
    error_code = extract_binance_error_code(error_message)
    request_ip = extract_request_ip(error_message)
    state["market_scan_backoff_until"] = retry_after.strftime("%Y-%m-%d %H:%M:%S %z")
    state["market_scan_backoff_reason"] = reason
    state["market_scan_backoff_error"] = error_message[:220]
    if request_ip:
        state["market_scan_backoff_request_ip"] = request_ip
    else:
        state.pop("market_scan_backoff_request_ip", None)
    if error_code is not None:
        state["market_scan_backoff_error_code"] = error_code
    else:
        state.pop("market_scan_backoff_error_code", None)


def clear_market_scan_backoff(state: dict[str, Any]) -> None:
    state.pop("market_scan_backoff_until", None)
    state.pop("market_scan_backoff_reason", None)
    state.pop("market_scan_backoff_error", None)
    state.pop("market_scan_backoff_request_ip", None)
    state.pop("market_scan_backoff_error_code", None)


def clear_expired_market_scan_backoff(state: dict[str, Any]) -> None:
    retry_after = parse_cst(state.get("market_scan_backoff_until"))
    if retry_after is not None and retry_after <= datetime.now(CST):
        clear_market_scan_backoff(state)


def market_scan_backoff_event(state: dict[str, Any]) -> dict[str, Any] | None:
    retry_after = parse_cst(state.get("market_scan_backoff_until"))
    if retry_after is None:
        return None
    if retry_after <= datetime.now(CST):
        clear_market_scan_backoff(state)
        return None
    event = {
        "time": now_cst(),
        "type": "market_scan_backoff",
        "reason": state.get("market_scan_backoff_reason") or "market_scan_error",
        "error": state.get("market_scan_backoff_error"),
        "retry_after": retry_after.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    request_ip = state.get("market_scan_backoff_request_ip")
    if request_ip:
        event["request_ip"] = request_ip
    error_code = state.get("market_scan_backoff_error_code")
    if error_code is not None:
        event["error_code"] = error_code
    return event


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def live_entry_leverage(config: dict[str, Any], live_precheck_result: dict[str, Any] | None) -> Decimal:
    configured = decimal_or_none(config.get("risk", {}).get("live_entry_leverage"))
    if configured and configured > 0:
        return configured
    leverages = []
    for position in (live_precheck_result or {}).get("positions", []) or []:
        leverage = decimal_or_none(position.get("leverage"))
        if leverage and leverage > 0:
            leverages.append(leverage)
    if leverages:
        return min(leverages)
    return Decimal("20")


def live_entry_capacity(
    config: dict[str, Any],
    live_precheck_result: dict[str, Any] | None,
    base_equity: float,
) -> dict[str, Any]:
    risk = config.get("risk", {})
    account = (live_precheck_result or {}).get("account") or {}
    available = decimal_or_none(account.get("availableBalance"))
    if available is None:
        return {
            "effective_equity": float(base_equity),
            "available_balance": None,
            "max_entry_notional": None,
            "reason": "account_available_balance_unavailable",
            "should_skip": True,
        }

    min_available = decimal_or_none(risk.get("live_entry_min_available_balance_usdt")) or Decimal("5")
    leverage = live_entry_leverage(config, live_precheck_result)
    buffer_pct = decimal_or_none(risk.get("live_entry_available_balance_buffer_pct")) or Decimal("70")
    buffer = max(Decimal("0"), min(buffer_pct, Decimal("100"))) / Decimal("100")
    max_notional_pct = decimal_or_none(risk.get("max_notional_pct")) or Decimal("100")
    max_notional_pct = max(max_notional_pct, Decimal("0.0001")) / Decimal("100")
    max_entry_notional = max(Decimal("0"), available) * leverage * buffer
    min_notional = decimal_or_none(risk.get("live_entry_min_notional_usdt")) or Decimal("20")

    should_skip = available < min_available or max_entry_notional < min_notional
    reason = None
    if available < min_available:
        reason = "insufficient_available_balance"
    elif max_entry_notional < min_notional:
        reason = "entry_notional_capacity_too_low"
    effective_equity = Decimal(str(base_equity))
    if not should_skip:
        effective_equity = min(effective_equity, max_entry_notional / max_notional_pct)

    return {
        "effective_equity": float(effective_equity),
        "available_balance": float(available),
        "max_entry_notional": float(max_entry_notional),
        "estimated_leverage": float(leverage),
        "buffer_pct": float(buffer_pct),
        "reason": reason,
        "should_skip": should_skip,
    }


def live_precheck_backoff_event(
    state: dict[str, Any],
    config: dict[str, Any],
    max_live_positions: int,
) -> dict[str, Any] | None:
    retry_after = parse_cst(state.get("live_precheck_backoff_until"))
    if retry_after is None:
        return None
    now = datetime.now(CST)
    if retry_after <= now:
        return None
    positions = list(state.get("last_live_positions") or [])
    active_positions = len(positions) if positions else int(state.get("live_precheck_backoff_active_positions", 0))
    event = {
        "time": now_cst(),
        "type": "live_position_precheck_backoff",
        "error": state.get("live_precheck_backoff_error") or "previous live position precheck error",
        "active_positions": active_positions,
        "positions": positions,
        "retry_after": retry_after.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    if not positions and state.get("live_precheck_backoff_active_positions_unknown", True):
        event["active_positions_unknown"] = True
    request_ip = state.get("live_precheck_backoff_request_ip")
    if request_ip:
        event["request_ip"] = request_ip
    error_code = state.get("live_precheck_backoff_error_code")
    if error_code is not None:
        event["error_code"] = error_code
    return event


def clear_live_precheck_backoff(state: dict[str, Any]) -> None:
    state.pop("live_precheck_backoff_until", None)
    state.pop("live_precheck_backoff_error", None)
    state.pop("live_precheck_backoff_active_positions", None)
    state.pop("live_precheck_backoff_active_positions_unknown", None)
    state.pop("live_precheck_backoff_request_ip", None)
    state.pop("live_precheck_backoff_error_code", None)


def clear_live_execution_backoff(state: dict[str, Any]) -> None:
    state.pop("live_execution_backoff_until", None)
    state.pop("live_execution_backoff_reason", None)
    state.pop("live_execution_backoff_symbol", None)
    state.pop("live_execution_backoff_status", None)
    state.pop("live_execution_backoff_error", None)
    state.pop("live_execution_backoff_error_code", None)


def live_execution_backoff_event(state: dict[str, Any]) -> dict[str, Any] | None:
    retry_after = parse_cst(state.get("live_execution_backoff_until"))
    if retry_after is None:
        return None
    now = datetime.now(CST)
    if retry_after <= now:
        clear_live_execution_backoff(state)
        return None
    event = {
        "time": now_cst(),
        "type": "live_execution_backoff",
        "reason": state.get("live_execution_backoff_reason") or "recent_live_execution_error",
        "symbol": state.get("live_execution_backoff_symbol"),
        "status": state.get("live_execution_backoff_status"),
        "error": state.get("live_execution_backoff_error"),
        "retry_after": retry_after.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    error_code = state.get("live_execution_backoff_error_code")
    if error_code is None:
        error_code = extract_binance_error_code(str(state.get("live_execution_backoff_error") or ""))
    if error_code is not None:
        event["error_code"] = error_code
    return event


def live_execution_backoff_blocks_market_scan(event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    return event.get("reason") in {
        "confirmation_timeout",
        "exchange_rate_limited",
        "margin_insufficient",
        "position_capacity_limit",
    }


def live_execution_failure_backoff_reason(result: dict[str, Any] | None) -> str | None:
    if not result:
        return None
    status = str(result.get("status") or "")
    error = str(result.get("error") or "")
    error_code = result.get("error_code")
    if error_code is None:
        error_code = extract_binance_error_code(error)
    if status == "confirmation_timeout":
        return "confirmation_timeout"
    if status == "cancelled_by_user":
        return "cancelled_by_user"
    if status not in {"entry_rejected_no_position_opened", "execution_error"}:
        return None
    error_text = error.lower()
    if (
        error_code == -1003
        or "http 418" in error_text
        or "418 client error" in error_text
        or "too many requests" in error_text
        or "banned until" in error_text
        or "i'm a teapot" in error_text
    ):
        return "exchange_rate_limited"
    if error_code == -2019 or "margin is insufficient" in error.lower():
        return "margin_insufficient"
    if error_code == -2027 or "maximum allowable position" in error.lower():
        return "position_capacity_limit"
    return None


def live_execution_failure_should_backoff(result: dict[str, Any] | None) -> bool:
    return live_execution_failure_backoff_reason(result) is not None


def live_execution_backoff_seconds(config: dict[str, Any], reason: str | None) -> int:
    service = config.get("service", {})
    default_cooldown = max(30, int(service.get("live_execution_backoff_seconds", 900)))
    if reason == "margin_insufficient":
        return max(
            default_cooldown,
            int(service.get("live_margin_insufficient_backoff_seconds", default_cooldown)),
        )
    return default_cooldown


def set_live_execution_backoff(
    state: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
    base_time: datetime | None = None,
) -> None:
    reason = live_execution_failure_backoff_reason(result) or "recent_live_execution_error"
    cooldown = live_execution_backoff_seconds(config, reason)
    retry_after = (base_time or datetime.now(CST)) + timedelta(seconds=cooldown)
    error = str(result.get("error") or "")
    if reason == "exchange_rate_limited":
        retry_after = rate_limit_retry_after(config, error, retry_after)
    error_code = result.get("error_code")
    if error_code is None:
        error_code = extract_binance_error_code(error)
    state["live_execution_backoff_until"] = retry_after.strftime("%Y-%m-%d %H:%M:%S %z")
    state["live_execution_backoff_reason"] = reason
    state["live_execution_backoff_symbol"] = result.get("symbol")
    state["live_execution_backoff_status"] = result.get("status")
    state["live_execution_backoff_error"] = error[:220]
    if error_code is not None:
        state["live_execution_backoff_error_code"] = error_code


def restore_live_execution_backoff_from_log(
    state: dict[str, Any],
    executions_path: Path,
    config: dict[str, Any],
) -> None:
    current_event = live_execution_backoff_event(state)
    current_retry_after = parse_cst((current_event or {}).get("retry_after"))
    now = datetime.now(CST)
    latest_row: dict[str, Any] | None = None
    latest_ts: datetime | None = None
    for row in read_jsonl(executions_path):
        reason = live_execution_failure_backoff_reason(row)
        if reason is None:
            continue
        cooldown = live_execution_backoff_seconds(config, reason)
        row_time = parse_cst(row.get("time"))
        if row_time is None or row_time + timedelta(seconds=cooldown) <= now:
            continue
        if current_retry_after is not None and row_time + timedelta(seconds=cooldown) <= current_retry_after:
            continue
        if latest_ts is None or row_time > latest_ts:
            latest_row = row
            latest_ts = row_time
    if latest_row is not None and latest_ts is not None:
        set_live_execution_backoff(state, latest_row, config, base_time=latest_ts)


def latest_price(symbol: str) -> float:
    data = http_get("/fapi/v1/ticker/price", {"symbol": symbol})
    return float(data["price"])


def is_clean_watch(row: dict[str, Any], allow_review: bool) -> bool:
    action = row["action"]
    if action == "BLOCK" or row["direction"] not in {"LONG", "SHORT"}:
        return False
    if action.startswith("REVIEW_") and not allow_review:
        return False
    return "WATCH" in action


MARKET_STRUCTURE_ENTRY_RISKS = {
    "volume_price_divergence",
    "volume_price_profile_adverse",
    "volume_price_churn_or_exhaustion",
    "order_book_ask_pressure",
    "order_book_bid_pressure",
    "order_book_wide_spread",
    "order_book_unavailable",
}
DEFAULT_BLOCKED_HISTORY_REASONS = {
    "past_loser",
    "past_major_loser",
    "high_freq_loss",
    "insurance_clear_block",
}


def has_market_structure_entry_risk(row: dict[str, Any]) -> bool:
    risk_flags = {item.strip() for item in str(row.get("risk_flags") or "").split(",") if item.strip()}
    return bool(risk_flags & MARKET_STRUCTURE_ENTRY_RISKS)


def has_blocked_history_reason(row: dict[str, Any], config: dict[str, Any]) -> bool:
    strategy = config.get("strategy", {})
    if not bool(strategy.get("block_loss_history_entries", True)):
        return False
    blocked = {
        str(item).strip()
        for item in strategy.get("blocked_history_reasons", DEFAULT_BLOCKED_HISTORY_REASONS)
        if str(item).strip()
    }
    if not blocked:
        return False
    reasons = {item.strip() for item in str(row.get("reasons") or "").split(",") if item.strip()}
    return bool(reasons & blocked)


def choose_candidate(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    excluded_symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    allow_review = bool(config["strategy"].get("allow_review_candidates", False))
    block_market_structure_risk = bool(config["strategy"].get("block_market_structure_risk_entries", True))
    min_score = float(config["strategy"]["min_score"])
    blocked = set(config["strategy"].get("manual_blocklist", []))
    excluded = excluded_symbols or set()
    allowed_directions = set(config["strategy"].get("directions", ["LONG", "SHORT"]))
    for row in rows:
        if row["symbol"] in blocked:
            continue
        if row["symbol"] in excluded:
            continue
        if row["direction"] not in allowed_directions:
            continue
        if float(row["score"]) < min_score:
            continue
        if block_market_structure_risk and has_market_structure_entry_risk(row):
            continue
        if has_blocked_history_reason(row, config):
            continue
        if is_clean_watch(row, allow_review):
            return row
    return None


def build_position(candidate: dict[str, Any], config: dict[str, Any], equity: float) -> dict[str, Any]:
    entry = float(candidate["last_price"])
    direction = candidate["direction"]
    exits = adaptive_exit_prices(candidate, config, entry, direction)
    stop_loss = float(exits["stop_loss"])
    take_profit = float(exits["take_profit"])
    risk_per_unit = abs(entry - stop_loss)
    risk_usdt = equity * float(config["risk"]["risk_per_trade_pct"]) / 100
    max_notional = equity * float(config["risk"]["max_notional_pct"]) / 100
    raw_qty = risk_usdt / risk_per_unit if risk_per_unit else 0
    max_qty = max_notional / entry if entry else 0
    qty = max(0.0, min(raw_qty, max_qty))
    notional = qty * entry
    exit_plan = exits["plan"]

    return {
        "id": str(uuid.uuid4()),
        "mode": "paper",
        "opened_at": now_cst(),
        "symbol": candidate["symbol"],
        "direction": direction,
        "entry_price": round(entry, 10),
        "qty": round(qty, 8),
        "notional": round(notional, 4),
        "risk_usdt": round(risk_usdt, 4),
        "stop_loss": round(stop_loss, 10),
        "take_profit": round(take_profit, 10),
        "exit_model": exit_plan["model"],
        "exit_profile": exit_plan["profile"],
        "stop_pct": exit_plan["stop_pct"],
        "take_profit_r": exit_plan["take_profit_r"],
        "take_profit_pct": exit_plan["take_profit_pct"],
        "exit_notes": ",".join(exit_plan["notes"]),
        "score": candidate["score"],
        "reasons": candidate["reasons"],
        "risk_flags": candidate["risk_flags"],
        "market_snapshot": market_snapshot(candidate),
        "source": "binance_usdm_15m_trend_screener",
        "intent_logged_at": None,
    }


def build_order_intent(position: dict[str, Any], intent_type: str) -> dict[str, Any]:
    side = "BUY" if position["direction"] == "LONG" else "SELL"
    close_side = "SELL" if position["direction"] == "LONG" else "BUY"
    return {
        "id": str(uuid.uuid4()),
        "time": now_cst(),
        "mode": "intent_only",
        "intent_type": intent_type,
        "symbol": position["symbol"],
        "direction": position["direction"],
        "entry_order": {
            "side": side,
            "type": "MARKET",
            "quantity": position["qty"],
            "notional": position["notional"],
        },
        "risk_orders": [
            {
                "side": close_side,
                "type": "STOP_MARKET",
                "stop_price": position["stop_loss"],
                "reduce_only": True,
            },
            {
                "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stop_price": position["take_profit"],
                "reduce_only": True,
            },
        ],
        "risk_usdt": position["risk_usdt"],
        "score": position["score"],
        "reasons": position["reasons"],
        "risk_flags": position["risk_flags"],
        "market_snapshot": position.get("market_snapshot", {}),
        "approval_required": True,
    }


def record_paper_position_operation(
    config: dict[str, Any],
    operation_type: str,
    position: dict[str, Any],
    *,
    intent: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    operation_reason = reason or (
        operation_reason_from_intent(intent, operation_type)
        if intent is not None
        else f"{operation_type}: {position.get('symbol') or '-'} {position.get('direction') or '-'}"
    )
    record = {
        "operation_type": operation_type,
        "operation_status": "recorded",
        "mode": "paper",
        "position_id": position.get("id"),
        "intent_id": (intent or {}).get("id"),
        "symbol": position.get("symbol"),
        "direction": position.get("direction"),
        "quantity": position.get("qty"),
        "notional": position.get("notional"),
        "entry_price": position.get("entry_price"),
        "exit_price": position.get("exit_price"),
        "paper_pnl": position.get("paper_pnl"),
        "operation_reason": operation_reason,
        "strategy": (position.get("market_snapshot") or {}).get("primary_strategy"),
        "score": position.get("score"),
        "reasons": position.get("reasons"),
        "risk_flags": position.get("risk_flags"),
    }
    try:
        return append_operation_record(config, record)
    except Exception as exc:
        if operation_journal_required(config):
            raise
        return {
            "enabled": True,
            "mongo_status": "error",
            "journal_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def check_exit(position: dict[str, Any], price: float) -> str | None:
    direction = position["direction"]
    if direction == "LONG":
        if price <= float(position["stop_loss"]):
            return "STOP_LOSS"
        if price >= float(position["take_profit"]):
            return "TAKE_PROFIT"
    if direction == "SHORT":
        if price >= float(position["stop_loss"]):
            return "STOP_LOSS"
        if price <= float(position["take_profit"]):
            return "TAKE_PROFIT"
    return None


def close_position(position: dict[str, Any], reason: str, exit_price: float) -> dict[str, Any]:
    entry = float(position["entry_price"])
    qty = float(position["qty"])
    if position["direction"] == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    closed = dict(position)
    closed.update(
        {
            "closed_at": now_cst(),
            "exit_price": round(exit_price, 10),
            "exit_reason": reason,
            "paper_pnl": round(pnl, 6),
        }
    )
    return closed


def render_report(path: Path, state: dict[str, Any], config: dict[str, Any]) -> None:
    ensure_parent(path)
    position = state.get("open_position")
    lines = [
        "# Paper Signal Service",
        "",
        f"- Updated: {now_cst()}",
        "- Mode: paper only, no real Binance orders.",
        f"- Paper equity: {state.get('paper_equity')}",
        f"- Scan interval seconds: {config['service']['scan_interval_seconds']}",
        "",
        "## Open Position",
        "",
    ]
    if position:
        lines += [
            f"- Symbol: `{position['symbol']}`",
            f"- Direction: {position['direction']}",
            f"- Entry: {position['entry_price']}",
            f"- Qty: {position['qty']}",
            f"- Notional: {position['notional']}",
            f"- Stop loss: {position['stop_loss']}",
            f"- Take profit: {position['take_profit']}",
            f"- Score: {position['score']}",
            f"- Reasons: {position['reasons']}",
            f"- Risk flags: {position['risk_flags'] or '-'}",
        ]
    else:
        lines.append("- None")

    lines += [
        "",
        "## Last Candidates",
        "",
        "| Symbol | Action | Dir | Score | RSI | ATR% | Reasons | Risk |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in state.get("last_candidates", [])[:15]:
        lines.append(
            f"| `{row['symbol']}` | {row['action']} | {row['direction']} | {row['score']} | {row['rsi14_15m']} | {row['atr_pct_15m']} | {row['reasons']} | {row['risk_flags']} |"
        )
    if not state.get("last_candidates"):
        lines.append("| - | - | - | - | - | - | - | - |")

    lines += [
        "",
        "## Recent Closed Positions",
        "",
        "| Symbol | Dir | Entry | Exit | Reason | PnL |",
        "|---|---|---:|---:|---|---:|",
    ]
    for row in state.get("closed_positions", [])[-10:]:
        lines.append(
            f"| `{row['symbol']}` | {row['direction']} | {row['entry_price']} | {row['exit_price']} | {row['exit_reason']} | {row['paper_pnl']} |"
        )
    path.write_text("\n".join(lines))


def maybe_execute_live_intent(config: dict[str, Any], intent: dict[str, Any], events_path: Path) -> dict[str, Any] | None:
    execution = config.get("execution", {})
    if execution.get("mode") != "confirm_then_live":
        return
    from quant.services.live_execution import execute_intent

    executions_path = Path(execution.get("executions_path", "data/processed/live-execution/executions.jsonl"))
    try:
        result = execute_intent(
            intent=intent,
            env_path=Path(execution.get("env_file", ".env")),
            executions_path=executions_path,
            dry_run=False,
            terminal_confirm=bool(execution.get("terminal_confirm", False)),
            confirmation_timeout_seconds=int(execution.get("confirmation_timeout_seconds", 120)),
            journal_config=config,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        result = {
            "time": now_cst(),
            "mode": "live",
            "intent_id": intent.get("id"),
            "symbol": intent.get("symbol"),
            "direction": intent.get("direction"),
            "quantity": str(intent.get("entry_order", {}).get("quantity") or ""),
            "entry_order": None,
            "risk_orders": [],
            "flatten_order": None,
            "status": "execution_error",
            "error": error,
            "operation_reason": operation_reason_from_intent(intent, "position_entry"),
        }
        request_ip = extract_request_ip(error)
        if request_ip:
            result["request_ip"] = request_ip
        error_code = extract_binance_error_code(error)
        if error_code is not None:
            result["error_code"] = error_code
        append_jsonl(executions_path, result)
    append_event(events_path, live_execution_event(result))
    return result


def nonzero_price_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if Decimal(text) == 0:
            return None
    except InvalidOperation:
        pass
    return text


def execution_fill_price(intent: dict[str, Any], result: dict[str, Any]) -> str | None:
    entry_order = result.get("entry_order") or {}
    for value in (
        entry_order.get("avgPrice"),
        entry_order.get("avgFillPrice"),
        entry_order.get("price"),
        (intent.get("market_snapshot") or {}).get("last_price"),
    ):
        price = nonzero_price_text(value)
        if price is not None:
            return price
    return None


def inferred_live_position_from_execution(intent: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("status") != "protected":
        return None
    symbol = str(result.get("symbol") or intent.get("symbol") or "").strip()
    direction = str(result.get("direction") or intent.get("direction") or "").strip().upper()
    if not symbol or direction not in {"LONG", "SHORT"}:
        return None
    quantity = str(result.get("quantity") or (result.get("entry_order") or {}).get("executedQty") or "")
    if not quantity:
        return None
    signed_quantity = quantity if direction == "LONG" or quantity.startswith("-") else f"-{quantity}"
    entry_price = execution_fill_price(intent, result)
    market_snapshot = intent.get("market_snapshot") or {}
    return {
        "symbol": symbol,
        "positionSide": direction,
        "positionAmt": signed_quantity,
        "entryPrice": entry_price,
        "breakEvenPrice": entry_price,
        "markPrice": str(market_snapshot.get("last_price")) if market_snapshot.get("last_price") is not None else entry_price,
        "unRealizedProfit": "0",
        "liquidationPrice": None,
        "leverage": None,
        "marginType": None,
        "snapshot_source": "inferred_from_live_execution",
        "snapshot_time": result.get("time") or now_cst(),
    }


def merge_inferred_live_position(state: dict[str, Any], intent: dict[str, Any], result: dict[str, Any]) -> None:
    inferred = inferred_live_position_from_execution(intent, result)
    if inferred is None:
        return
    existing_positions = list(state.get("last_live_positions") or [])
    merged_positions = []
    replaced = False
    for position in existing_positions:
        if (
            position.get("symbol") == inferred["symbol"]
            and str(position.get("positionSide") or "").upper() == inferred["positionSide"]
        ):
            merged_positions.append(inferred)
            replaced = True
        else:
            merged_positions.append(position)
    if not replaced:
        merged_positions.append(inferred)
    state["last_live_positions"] = merged_positions
    state["last_live_position_symbols"] = [
        position.get("symbol")
        for position in merged_positions
        if position.get("symbol")
    ]


def supplement_recent_protected_positions(
    state: dict[str, Any],
    events_path: Path,
    intents_path: Path,
    executions_path: Path,
) -> None:
    since = latest_successful_live_precheck_at(events_path, state)
    if since is None:
        return
    intents_by_id = {
        str(intent.get("id")): intent
        for intent in read_jsonl(intents_path)
        if intent.get("id")
    }
    existing_positions = list(state.get("last_live_positions") or [])
    existing_keys = {
        (
            str(position.get("symbol") or ""),
            str(position.get("positionSide") or "").upper(),
        )
        for position in existing_positions
    }
    merged_positions = list(existing_positions)
    for row in read_jsonl(executions_path):
        if row.get("mode") != "live" or row.get("status") != "protected":
            continue
        row_time = parse_cst(row.get("time"))
        if row_time is None or row_time <= since:
            continue
        symbol = str(row.get("symbol") or "")
        direction = str(row.get("direction") or "").upper()
        key = (symbol, direction)
        if not symbol or direction not in {"LONG", "SHORT"} or key in existing_keys:
            continue
        inferred = inferred_live_position_from_execution(
            intents_by_id.get(str(row.get("intent_id")) or "", {}),
            row,
        )
        if inferred is None:
            continue
        merged_positions.append(inferred)
        existing_keys.add(key)
    if merged_positions != existing_positions:
        state["last_live_positions"] = merged_positions
        state["last_live_position_symbols"] = [
            position.get("symbol")
            for position in merged_positions
            if position.get("symbol")
        ]


def print_scan_progress(index: int, total: int, symbol: str) -> None:
    print(f"[scan] {index:02d}/{total:02d} {symbol}", file=sys.stderr, flush=True)


def service_scan_market(config: dict[str, Any], show_progress: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    return scan_market(
        max_symbols=int(config["scanner"]["max_symbols"]),
        min_quote_volume=float(config["scanner"]["min_quote_volume"]),
        klines_limit=int(config["scanner"]["klines_limit"]),
        history_path=Path(config["scanner"]["history_path"]),
        volume_price_profile_path=Path(
            config["scanner"].get(
                "volume_price_profile_path",
                "data/processed/binance-usdm-30d/volume_price_profile.json",
            )
        ),
        **scanner_order_book_kwargs(config),
        concurrency=int(config["scanner"].get("concurrency", 1)),
        progress_callback=print_scan_progress if show_progress else None,
    )


def record_market_scan_error(
    config: dict[str, Any],
    state: dict[str, Any],
    events_path: Path,
    error_message: str,
) -> None:
    set_market_scan_backoff(state, error_message, config)
    scan_error = {"symbol": "MARKET", "error": error_message}
    state["last_scan"] = {"time": now_cst(), "symbols_scanned": 0, "rows": 0}
    state["last_errors"] = [scan_error]
    state["last_candidates"] = []
    append_scan_audit(
        Path(config["service"].get("scan_audit_path", "data/processed/paper-signal-service/scan_audit.jsonl")),
        [],
        [scan_error],
        0,
    )
    event = {
        "time": now_cst(),
        "type": "market_scan_error",
        "reason": state.get("market_scan_backoff_reason") or "market_scan_error",
        "error": error_message,
        "retry_after": state.get("market_scan_backoff_until"),
    }
    request_ip = state.get("market_scan_backoff_request_ip")
    if request_ip:
        event["request_ip"] = request_ip
    error_code = state.get("market_scan_backoff_error_code")
    if error_code is not None:
        event["error_code"] = error_code
    append_event(events_path, event)


def append_market_scan_backoff_skip(
    config: dict[str, Any],
    state: dict[str, Any],
    events_path: Path,
    backoff_event: dict[str, Any],
    *,
    active_positions: int = 0,
    max_live_positions: int = 0,
    symbols: list[str] | None = None,
) -> None:
    append_event_dedup(
        events_path,
        backoff_event,
        state,
        "last_market_scan_backoff_signature",
        ("type", "reason", "error", "retry_after"),
        heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
    )
    live_entry_skipped_event = {
        "time": now_cst(),
        "type": "live_entry_skipped",
        "reason": backoff_event.get("reason") or "market_scan_error",
        "symbols": symbols or [],
        "active_positions": active_positions,
        "max_live_positions": max_live_positions,
        "skipped_full_market_scan": True,
        "retry_after": backoff_event.get("retry_after"),
    }
    append_event_dedup(
        events_path,
        live_entry_skipped_event,
        state,
        "last_live_entry_skipped_signature",
        ("type", "reason", "symbols", "active_positions", "max_live_positions", "retry_after"),
        heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
    )


def service_tick(
    config: dict[str, Any],
    state: dict[str, Any],
    events_path: Path,
    intents_path: Path,
    show_progress: bool = True,
    live_confirm_only: bool = False,
) -> dict[str, Any]:
    state["mode"] = "live_confirm" if live_confirm_only else "paper"
    state["paper_equity"] = state.get("paper_equity") or float(config["risk"]["paper_equity_usdt"])
    clear_expired_market_scan_backoff(state)
    if live_confirm_only:
        # Confirm-live status is exchange-sourced; stale paper positions should not leak into saved state.
        state["open_position"] = None

    position = None if live_confirm_only else state.get("open_position")
    if position:
        if not position.get("intent_logged_at"):
            intent = build_order_intent(position, "entry")
            append_intent(intents_path, intent)
            position["intent_logged_at"] = now_cst()
            state["open_position"] = position
        price = latest_price(position["symbol"])
        reason = check_exit(position, price)
        if reason:
            closed = close_position(position, reason, price)
            state["closed_positions"].append(closed)
            state["open_position"] = None
            exit_intent = {
                "id": str(uuid.uuid4()),
                "time": now_cst(),
                "mode": "intent_only",
                "intent_type": "exit",
                "symbol": closed["symbol"],
                "direction": closed["direction"],
                "exit_reason": closed["exit_reason"],
                "exit_price": closed["exit_price"],
                "paper_pnl": closed["paper_pnl"],
                "approval_required": True,
            }
            journal_status = record_paper_position_operation(
                config,
                "paper_position_exit",
                closed,
                intent=exit_intent,
                reason=(
                    f"paper_position_exit: {closed['symbol']} {closed['direction']} "
                    f"exit_reason={closed['exit_reason']} exit_price={closed['exit_price']} pnl={closed['paper_pnl']}"
                ),
            )
            append_event(events_path, {"time": now_cst(), "type": "paper_exit", "position": closed, "operation_journal": journal_status})
            append_intent(intents_path, exit_intent)

    live_precheck_result = None
    live_precheck_failed = False
    if live_confirm_only:
        from quant.services.protection_manager import adjust_live_protection_after_scan, inspect_live_protection_before_scan

        max_live_positions = int(config.get("risk", {}).get("max_live_positions", 1))
        executions_path = Path(config.get("execution", {}).get("executions_path", "data/processed/live-execution/executions.jsonl"))
        restore_live_execution_backoff_from_log(state, executions_path, config)
        supplement_recent_protected_positions(state, events_path, intents_path, executions_path)
        backoff_result = live_precheck_backoff_event(state, config, max_live_positions)
        if backoff_result is not None:
            live_precheck_failed = True
            live_precheck_result = backoff_result
            append_event_dedup(
                events_path,
                live_precheck_result,
                state,
                "last_live_position_precheck_backoff_signature",
                ("type", "error", "active_positions", "positions", "retry_after"),
            )
            state["last_live_position_precheck"] = live_precheck_result
        else:
            try:
                live_precheck_result = inspect_live_protection_before_scan(config, events_path, write_event=False)
                append_event_dedup(
                    events_path,
                    live_precheck_result,
                    state,
                    "last_live_position_precheck_signature",
                    ("type", "active_positions", "stable_positions", "stale_orders"),
                    heartbeat_seconds=max(
                        0,
                        int(config.get("service", {}).get("live_precheck_heartbeat_seconds", 300)),
                    ),
                )
                state["last_live_position_precheck"] = live_precheck_result
                clear_live_precheck_backoff(state)
                clear_event_dedup_signatures(
                    state,
                    "last_live_position_precheck_backoff_signature",
                    "last_live_position_market_scan_skipped_signature",
                    "last_protection_adjustment_skipped_signature",
                )
                state["last_successful_live_precheck_at"] = live_precheck_result.get("time")
            except Exception as exc:
                live_precheck_failed = True
                error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
                known_positions = list(state.get("last_live_positions") or [])
                active_positions = len(known_positions)
                active_positions_unknown = not known_positions
                request_ip = extract_request_ip(error_message)
                error_code = extract_binance_error_code(error_message)
                live_precheck_result = {
                    "time": now_cst(),
                    "type": "live_position_precheck_error",
                    "error": error_message,
                    "active_positions": active_positions,
                    "positions": known_positions,
                }
                if active_positions_unknown:
                    live_precheck_result["active_positions_unknown"] = True
                if request_ip:
                    live_precheck_result["request_ip"] = request_ip
                if error_code is not None:
                    live_precheck_result["error_code"] = error_code
                append_event(events_path, live_precheck_result)
                state["last_live_position_precheck"] = live_precheck_result
                if auth_like_live_precheck_error(error_message):
                    retry_after = live_precheck_backoff_retry_after(config, error_message)
                    state["live_precheck_backoff_until"] = retry_after.strftime("%Y-%m-%d %H:%M:%S %z")
                    state["live_precheck_backoff_error"] = error_message
                    state["live_precheck_backoff_active_positions"] = active_positions
                    state["live_precheck_backoff_active_positions_unknown"] = active_positions_unknown
                    state["live_precheck_backoff_request_ip"] = request_ip
                    state["live_precheck_backoff_error_code"] = error_code
                else:
                    clear_live_precheck_backoff(state)

        position_symbols = [
            position.get("symbol")
            for position in (live_precheck_result or {}).get("positions", [])
            if position.get("symbol")
        ]
        state["last_live_positions"] = list((live_precheck_result or {}).get("positions", []))
        state["last_live_position_symbols"] = position_symbols
        position_rows: list[dict[str, Any]] = []
        position_errors: list[dict[str, str]] = []
        position_symbols_scanned = 0
        if position_symbols:
            position_rows, position_errors, position_symbols_scanned = scan_symbols(
                symbols=position_symbols,
                klines_limit=int(config["scanner"]["klines_limit"]),
                history_path=Path(config["scanner"]["history_path"]),
                volume_price_profile_path=Path(config["scanner"].get("volume_price_profile_path", "data/processed/binance-usdm-30d/volume_price_profile.json")),
                **scanner_order_book_kwargs(config),
                concurrency=int(config.get("protection", {}).get("concurrency", config["scanner"].get("concurrency", 1))),
                progress_callback=print_scan_progress if show_progress else None,
            )
        state["last_position_scan"] = {
            "time": now_cst(),
            "symbols_scanned": position_symbols_scanned,
            "rows": len(position_rows),
            "errors": position_errors,
        }
        precheck_reason = str((live_precheck_result or {}).get("type") or "live_position_precheck_error")
        if live_precheck_failed:
            state["last_position_scan"]["reason"] = str((live_precheck_result or {}).get("type") or "live_position_precheck_error")
            skipped_event = {
                "time": now_cst(),
                "type": "live_position_market_scan_skipped",
                "reason": state["last_position_scan"]["reason"],
                "symbols": position_symbols,
                "active_positions": int((live_precheck_result or {}).get("active_positions", max_live_positions)),
            }
            if (live_precheck_result or {}).get("retry_after"):
                skipped_event["retry_after"] = live_precheck_result["retry_after"]
            if (live_precheck_result or {}).get("active_positions_unknown"):
                skipped_event["active_positions_unknown"] = True
            append_event_dedup(
                events_path,
                skipped_event,
                state,
                "last_live_position_market_scan_skipped_signature",
                ("type", "reason", "symbols", "active_positions", "retry_after"),
            )
        else:
            append_event_dedup(
                events_path,
                {
                    "time": now_cst(),
                    "type": "live_position_market_scan",
                    "symbols": position_symbols,
                    "symbols_scanned": position_symbols_scanned,
                    "rows": len(position_rows),
                    "errors": position_errors[:20],
                },
                state,
                "last_live_position_market_scan_signature",
                ("type", "symbols", "symbols_scanned", "rows", "errors"),
            )

        protection_result = None
        if live_precheck_failed:
            protection_result = {
                "time": now_cst(),
                "type": "protection_adjustment_skipped",
                "reason": precheck_reason,
                "active_positions": int((live_precheck_result or {}).get("active_positions", max_live_positions)),
                "symbols": position_symbols,
            }
            if (live_precheck_result or {}).get("retry_after"):
                protection_result["retry_after"] = live_precheck_result["retry_after"]
            if (live_precheck_result or {}).get("active_positions_unknown"):
                protection_result["active_positions_unknown"] = True
            append_event_dedup(
                events_path,
                protection_result,
                state,
                "last_protection_adjustment_skipped_signature",
                ("type", "reason", "symbols", "active_positions", "retry_after"),
            )
        else:
            live_precheck_positions = list((live_precheck_result or {}).get("positions", []))
            live_precheck_stale_orders = list((live_precheck_result or {}).get("stale_orders", []))
            if (
                config.get("protection", {}).get("enabled", False)
                and not live_precheck_positions
                and not live_precheck_stale_orders
            ):
                protection_result = {
                    "time": now_cst(),
                    "type": "protection_adjustment_skipped",
                    "reason": "no_active_positions",
                    "active_positions": 0,
                    "symbols": [],
                    "positions": [],
                    "stale_orders": [],
                }
                append_event_dedup(
                    events_path,
                    protection_result,
                    state,
                    "last_protection_adjustment_skipped_signature",
                    ("type", "reason", "symbols", "active_positions"),
                    heartbeat_seconds=max(
                        0,
                        int(config.get("service", {}).get("live_precheck_heartbeat_seconds", 300)),
                    ),
                )
            else:
                try:
                    protection_result = adjust_live_protection_after_scan(
                        rows=position_rows,
                        config=config,
                        events_path=events_path,
                        dry_run=False,
                        terminal_confirm=bool(config.get("execution", {}).get("terminal_confirm", False)),
                    )
                except Exception as exc:
                    error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
                    request_ip = extract_request_ip(error_message)
                    error_code = extract_binance_error_code(error_message)
                    protection_result = {
                        "time": now_cst(),
                        "type": "protection_adjustment_error",
                        "error": error_message,
                        "active_positions": int((live_precheck_result or {}).get("active_positions", max_live_positions)),
                        "symbols": position_symbols,
                        "positions": list((live_precheck_result or {}).get("positions", [])),
                        "position_scan": dict(state.get("last_position_scan", {})),
                    }
                    if request_ip:
                        protection_result["request_ip"] = request_ip
                    if error_code is not None:
                        protection_result["error_code"] = error_code
                    append_event(events_path, protection_result)
        if protection_result:
            state["last_protection_adjustment"] = protection_result

        active_live_positions = 0
        if protection_result:
            active_live_positions = int(
                protection_result.get("active_positions", len(protection_result.get("positions", [])))
            )
        elif live_precheck_result:
            active_live_positions = int(
                live_precheck_result.get("active_positions", len(live_precheck_result.get("positions", [])))
            )
        if live_precheck_failed or active_live_positions >= max_live_positions:
            state["last_live_intent"] = None
            live_entry_skipped_event = {
                "time": now_cst(),
                "type": "live_entry_skipped",
                "reason": precheck_reason if live_precheck_failed else "max_live_positions_reached",
                "symbols": position_symbols,
                "active_positions": active_live_positions,
                "max_live_positions": max_live_positions,
                "skipped_full_market_scan": True,
            }
            if (live_precheck_result or {}).get("retry_after"):
                live_entry_skipped_event["retry_after"] = live_precheck_result["retry_after"]
            if (live_precheck_result or {}).get("active_positions_unknown"):
                live_entry_skipped_event["active_positions_unknown"] = True
            append_event_dedup(
                events_path,
                live_entry_skipped_event,
                state,
                "last_live_entry_skipped_signature",
                ("type", "reason", "symbols", "active_positions", "max_live_positions", "retry_after"),
                heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
            )
            return state

        live_backoff_result = live_execution_backoff_event(state)
        backoff_symbols: set[str] = set()
        if live_backoff_result is not None:
            append_event_dedup(
                events_path,
                live_backoff_result,
                state,
                "last_live_execution_backoff_signature",
                ("type", "reason", "symbol", "status", "error", "retry_after"),
            )
            if live_backoff_result.get("symbol"):
                backoff_symbols.add(str(live_backoff_result["symbol"]))
            if live_execution_backoff_blocks_market_scan(live_backoff_result):
                state["last_live_intent"] = None
                live_entry_skipped_event = {
                    "time": now_cst(),
                    "type": "live_entry_skipped",
                    "reason": str(live_backoff_result.get("reason") or "live_execution_backoff"),
                    "symbols": position_symbols,
                    "active_positions": active_live_positions,
                    "max_live_positions": max_live_positions,
                    "skipped_full_market_scan": True,
                    "backoff_symbols": sorted(backoff_symbols),
                    "retry_after": live_backoff_result.get("retry_after"),
                }
                append_event_dedup(
                    events_path,
                    live_entry_skipped_event,
                    state,
                    "last_live_entry_skipped_signature",
                    (
                        "type",
                        "reason",
                        "symbols",
                        "active_positions",
                        "max_live_positions",
                        "retry_after",
                        "backoff_symbols",
                    ),
                    heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
                )
                return state

        entry_capacity = live_entry_capacity(config, live_precheck_result, float(state["paper_equity"]))
        state["last_live_entry_capacity"] = entry_capacity
        if entry_capacity.get("should_skip"):
            state["last_live_intent"] = None
            live_entry_skipped_event = {
                "time": now_cst(),
                "type": "live_entry_skipped",
                "reason": entry_capacity.get("reason") or "live_entry_capacity_limit",
                "symbols": position_symbols,
                "active_positions": active_live_positions,
                "max_live_positions": max_live_positions,
                "skipped_full_market_scan": True,
                "available_balance": entry_capacity.get("available_balance"),
                "max_entry_notional": entry_capacity.get("max_entry_notional"),
                "estimated_leverage": entry_capacity.get("estimated_leverage"),
            }
            append_event_dedup(
                events_path,
                live_entry_skipped_event,
                state,
                "last_live_entry_skipped_signature",
                ("type", "reason", "symbols", "active_positions", "max_live_positions"),
                heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
            )
            return state

        backoff_event = market_scan_backoff_event(state)
        if backoff_event is not None:
            state["last_live_intent"] = None
            append_market_scan_backoff_skip(
                config,
                state,
                events_path,
                backoff_event,
                active_positions=active_live_positions,
                max_live_positions=max_live_positions,
                symbols=position_symbols,
            )
            return state

        try:
            rows, errors, symbols_scanned = service_scan_market(config, show_progress)
            clear_market_scan_backoff(state)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
            record_market_scan_error(config, state, events_path, error_message)
            backoff_event = market_scan_backoff_event(state)
            if backoff_event is not None:
                append_market_scan_backoff_skip(
                    config,
                    state,
                    events_path,
                    backoff_event,
                    active_positions=active_live_positions,
                    max_live_positions=max_live_positions,
                    symbols=position_symbols,
                )
            state["last_live_intent"] = None
            return state
        state["last_scan"] = {"time": now_cst(), "symbols_scanned": symbols_scanned, "rows": len(rows)}
        state["last_errors"] = errors
        state["last_candidates"] = [row for row in rows if "WATCH" in row["action"]][:20]
        append_scan_audit(Path(config["service"].get("scan_audit_path", "data/processed/paper-signal-service/scan_audit.jsonl")), rows, errors, symbols_scanned)

        active_symbols = {
            position.get("symbol")
            for position in (live_precheck_result or {}).get("positions", [])
            if position.get("symbol")
        }
        excluded_symbols = active_symbols | backoff_symbols
        candidate = choose_candidate(rows, config, excluded_symbols=excluded_symbols)
        if candidate:
            position = build_position(candidate, config, float(entry_capacity["effective_equity"]))
            position["mode"] = "live_intent"
            position["live_sizing"] = entry_capacity
            if position["qty"] > 0:
                intent = build_order_intent(position, "entry")
                intent["live_sizing"] = entry_capacity
                append_intent(intents_path, intent)
                state["last_live_intent"] = intent
                append_event(events_path, live_intent_event(intent))
                execution_result = maybe_execute_live_intent(config, intent, events_path)
                if live_execution_failure_should_backoff(execution_result):
                    set_live_execution_backoff(state, execution_result, config)
                elif execution_result and execution_result.get("status") == "protected":
                    clear_live_execution_backoff(state)
                merge_inferred_live_position(state, intent, execution_result)
            else:
                state["last_live_intent"] = None
                live_entry_skipped_event = {
                    "time": now_cst(),
                    "type": "live_entry_skipped",
                    "reason": "entry_quantity_zero_after_capacity_gate",
                    "symbols": position_symbols,
                    "active_positions": active_live_positions,
                    "max_live_positions": max_live_positions,
                    "skipped_full_market_scan": False,
                    "candidate_symbol": candidate.get("symbol"),
                    "available_balance": entry_capacity.get("available_balance"),
                    "max_entry_notional": entry_capacity.get("max_entry_notional"),
                }
                append_event_dedup(
                    events_path,
                    live_entry_skipped_event,
                    state,
                    "last_live_entry_skipped_signature",
                    (
                        "type",
                        "reason",
                        "symbols",
                        "active_positions",
                        "max_live_positions",
                        "candidate_symbol",
                    ),
                    heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
                )
        else:
            state["last_live_intent"] = None
            if backoff_symbols:
                live_entry_skipped_event = {
                    "time": now_cst(),
                    "type": "live_entry_skipped",
                    "reason": "no_candidate_after_backoff_symbol_filter",
                    "symbols": position_symbols,
                    "active_positions": active_live_positions,
                    "max_live_positions": max_live_positions,
                    "skipped_full_market_scan": False,
                    "backoff_symbols": sorted(backoff_symbols),
                    "retry_after": live_backoff_result.get("retry_after") if live_backoff_result else None,
                }
                append_event_dedup(
                    events_path,
                    live_entry_skipped_event,
                    state,
                    "last_live_entry_skipped_signature",
                    (
                        "type",
                        "reason",
                        "symbols",
                        "active_positions",
                        "max_live_positions",
                        "retry_after",
                        "backoff_symbols",
                    ),
                    heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
                )
        return state

    backoff_event = market_scan_backoff_event(state)
    if backoff_event is not None:
        append_event_dedup(
            events_path,
            backoff_event,
            state,
            "last_market_scan_backoff_signature",
            ("type", "reason", "error", "retry_after"),
            heartbeat_seconds=live_entry_skip_heartbeat_seconds(config),
        )
        return state

    try:
        rows, errors, symbols_scanned = service_scan_market(config, show_progress)
        clear_market_scan_backoff(state)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
        record_market_scan_error(config, state, events_path, error_message)
        return state
    state["last_scan"] = {"time": now_cst(), "symbols_scanned": symbols_scanned, "rows": len(rows)}
    state["last_errors"] = errors
    state["last_candidates"] = [row for row in rows if "WATCH" in row["action"]][:20]
    append_scan_audit(Path(config["service"].get("scan_audit_path", "data/processed/paper-signal-service/scan_audit.jsonl")), rows, errors, symbols_scanned)

    if not state.get("open_position"):
        candidate = choose_candidate(rows, config)
        if candidate:
            position = build_position(candidate, config, float(state["paper_equity"]))
            if position["qty"] > 0:
                position["intent_logged_at"] = now_cst()
                state["open_position"] = position
                intent = build_order_intent(position, "entry")
                journal_status = record_paper_position_operation(config, "paper_position_entry", position, intent=intent)
                append_event(events_path, {"time": now_cst(), "type": "paper_entry", "position": position, "operation_journal": journal_status})
                append_intent(intents_path, intent)
                maybe_execute_live_intent(config, intent, events_path)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--intents", default=str(DEFAULT_INTENTS))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--write-report", action="store_true", help="write reports/service markdown output")
    parser.add_argument("--confirm-live", action="store_true", help="show confirmation dialog and execute newly generated live intents")
    parser.add_argument("--no-progress", action="store_true", help="hide per-symbol scan progress")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--iterations", type=int, default=0, help="0 means run forever unless --once is used")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if args.confirm_live:
        config.setdefault("execution", {})["mode"] = "confirm_then_live"
    state_path = Path(args.state)
    events_path = Path(args.events)
    intents_path = Path(args.intents)
    report_path = Path(args.report)
    state = load_state(state_path)
    install_shutdown_handlers()

    iterations = 1 if args.once else args.iterations
    completed = 0
    while True:
        if SHUTDOWN_REQUESTED:
            print_service_shutdown("signal_requested", completed)
            break
        tick_started_at = time.time()
        state = service_tick(
            config,
            state,
            events_path,
            intents_path,
            show_progress=not args.no_progress,
            live_confirm_only=args.confirm_live,
        )
        save_state(state_path, state)
        if args.write_report or bool(config["service"].get("write_report", False)):
            render_report(report_path, state, config)
        completed += 1
        sleep_seconds = service_loop_sleep_seconds(config, state, confirm_live=args.confirm_live)
        print_service_log(
            {
                "time": now_cst(),
                "iteration": completed,
                "mode": state.get("mode"),
                "open_position": None if args.confirm_live else state.get("open_position"),
                "last_live_intent": state.get("last_live_intent") if args.confirm_live else None,
                "active_live_positions": len(state.get("last_live_positions") or []) if args.confirm_live else None,
                "live_precheck_status": (state.get("last_live_position_precheck") or {}).get("type") if args.confirm_live else None,
                "live_precheck_retry_after": (state.get("last_live_position_precheck") or {}).get("retry_after") if args.confirm_live else None,
                "tick_elapsed_seconds": round(time.time() - tick_started_at, 3),
                "next_sleep_seconds": sleep_seconds,
            }
        )
        stop_reason = service_loop_stop_reason(
            once=args.once,
            shutdown_requested=SHUTDOWN_REQUESTED,
            iterations=iterations,
            completed=completed,
        )
        if stop_reason:
            print_service_shutdown(stop_reason, completed)
            break
        if sleep_seconds > 0:
            sleep_until = time.time() + sleep_seconds
            while not SHUTDOWN_REQUESTED and time.time() < sleep_until:
                time.sleep(min(1.0, sleep_until - time.time()))
            if SHUTDOWN_REQUESTED:
                print_service_shutdown("signal_requested", completed)
                break


if __name__ == "__main__":
    main()

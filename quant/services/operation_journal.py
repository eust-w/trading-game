#!/usr/bin/env python3
"""Operation audit journal for position and protective-order actions."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # Optional runtime dependency; the service can still mirror locally.
    from pymongo import MongoClient
except ImportError:  # pragma: no cover - depends on local environment
    MongoClient = None  # type: ignore[assignment]


CST = timezone(timedelta(hours=8))
DEFAULT_MONGO_URI = "mongodb://localhost:27017/"
DEFAULT_MONGO_URI_ENV = "ALPHA_VAULT_MONGODB_URI"
DEFAULT_DATABASE = "alpha_vault"
DEFAULT_COLLECTION = "operation_audit"
DEFAULT_LOCAL_DAILY_PATH = "data/processed/operation-journal/{date}-operations.jsonl"


class OperationJournalError(RuntimeError):
    """Raised when operation journaling is required but cannot complete."""


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %z")


def normalize_journal_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not config:
        return {}
    if "operation_journal" in config:
        nested = config.get("operation_journal") or {}
        return nested if isinstance(nested, dict) else {}
    return config


def default_operation_journal_config() -> dict[str, Any]:
    return {
        "enabled": os.environ.get("ALPHA_VAULT_OPERATION_JOURNAL_ENABLED", "1").lower()
        not in {"0", "false", "no"},
        "required": os.environ.get("ALPHA_VAULT_OPERATION_JOURNAL_REQUIRED", "1").lower()
        in {"1", "true", "yes"},
        "mongo_uri": os.environ.get(DEFAULT_MONGO_URI_ENV, DEFAULT_MONGO_URI),
        "mongo_uri_env": os.environ.get("ALPHA_VAULT_MONGODB_URI_ENV", DEFAULT_MONGO_URI_ENV),
        "mongo_database": os.environ.get("ALPHA_VAULT_MONGODB_DATABASE", DEFAULT_DATABASE),
        "mongo_collection": os.environ.get("ALPHA_VAULT_MONGODB_COLLECTION", DEFAULT_COLLECTION),
        "local_daily_path": os.environ.get("ALPHA_VAULT_OPERATION_JOURNAL_PATH", DEFAULT_LOCAL_DAILY_PATH),
    }


def mongo_uri_from_config(cfg: dict[str, Any]) -> tuple[str | None, str]:
    direct_uri = str(cfg.get("mongo_uri") or "").strip()
    if direct_uri:
        return direct_uri, "operation_journal.mongo_uri"
    uri_env = str(cfg.get("mongo_uri_env") or DEFAULT_MONGO_URI_ENV)
    return os.environ.get(uri_env), uri_env


def operation_journal_required(config: dict[str, Any] | None) -> bool:
    return bool(normalize_journal_config(config).get("required", False))


def daily_path(pattern: str | Path, now: datetime | None = None) -> Path:
    base_time = now or datetime.now(CST)
    text = str(pattern)
    return Path(
        text.format(
            date=base_time.strftime("%Y-%m-%d"),
            date_compact=base_time.strftime("%Y%m%d"),
        )
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_text(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value if str(item).strip()]
        return ",".join(items) if items else "-"
    return str(value)


def operation_reason_from_intent(intent: dict[str, Any], action: str = "position_entry") -> str:
    snapshot = intent.get("market_snapshot") or {}
    risk_orders = intent.get("risk_orders") or []
    stop = risk_orders[0].get("stop_price") if len(risk_orders) > 0 else None
    take = risk_orders[1].get("stop_price") if len(risk_orders) > 1 else None
    entry = intent.get("entry_order") or {}
    return "; ".join(
        [
            f"{action}: {intent.get('symbol') or '-'} {intent.get('direction') or '-'}",
            f"strategy={_safe_text(snapshot.get('primary_strategy'))}",
            f"score={_safe_text(intent.get('score'))}",
            f"reasons={_safe_text(intent.get('reasons') or snapshot.get('reasons'))}",
            f"risk_flags={_safe_text(intent.get('risk_flags') or snapshot.get('risk_flags'))}",
            f"notional={_safe_text(entry.get('notional'))}",
            f"quantity={_safe_text(entry.get('quantity'))}",
            f"stop={_safe_text(stop)}",
            f"take_profit={_safe_text(take)}",
        ]
    )


def operation_reason_from_plan(plan: dict[str, Any]) -> str:
    if plan.get("operation_reason"):
        return str(plan["operation_reason"])
    parts = [
        f"protective_order_maintenance: {plan.get('symbol') or '-'} {plan.get('direction') or '-'}",
        f"strategy={_safe_text(plan.get('primary_strategy'))}",
        f"score={_safe_text(plan.get('score'))}",
        f"stop={_safe_text(plan.get('current_stop'))}->{_safe_text(plan.get('new_stop'))}",
        f"take_profit={_safe_text(plan.get('current_take_profit'))}->{_safe_text(plan.get('new_take_profit'))}",
        f"exit_model={_safe_text(plan.get('exit_model'))}/{_safe_text(plan.get('exit_profile'))}",
    ]
    if plan.get("exit_signal"):
        parts.append(f"exit_signal={plan['exit_signal']}")
    if plan.get("break_even_floor"):
        parts.append(f"break_even_floor={plan['break_even_floor']}")
    if plan.get("profit_lock_floor"):
        parts.append(f"profit_lock_floor={plan['profit_lock_floor']}")
    if plan.get("take_tighten_allowed"):
        parts.append("take_tighten_allowed=true")
    if plan.get("exit_notes"):
        parts.append(f"exit_notes={plan['exit_notes']}")
    if plan.get("volume_price_regime") or plan.get("volume_price_score") not in (None, ""):
        parts.append(
            "volume_price="
            f"{_safe_text(plan.get('volume_price_regime'))}/{_safe_text(plan.get('volume_price_score'))}"
        )
    order_book_parts = []
    if plan.get("order_book_imbalance") not in (None, ""):
        order_book_parts.append(f"imbalance:{plan.get('order_book_imbalance')}")
    if plan.get("order_book_spread_bps") not in (None, ""):
        order_book_parts.append(f"spread_bps:{plan.get('order_book_spread_bps')}")
    if plan.get("order_book_error"):
        order_book_parts.append(f"error:{plan.get('order_book_error')}")
    if order_book_parts:
        parts.append(f"order_book={','.join(order_book_parts)}")
    if plan.get("protection_notes"):
        parts.append(f"protection_notes={plan['protection_notes']}")
    if plan.get("order_lookup_source"):
        parts.append(f"order_lookup_source={plan['order_lookup_source']}")
    return "; ".join(parts)


def stale_order_reason(order: dict[str, Any]) -> str:
    return (
        "protective_order_cancel_stale: active position no longer matches "
        f"{order.get('symbol') or '-'} {order.get('positionSide') or '-'} "
        f"{order.get('orderType') or order.get('type') or '-'} "
        f"trigger={order.get('triggerPrice') or order.get('stopPrice') or '-'}"
    )


def append_operation_record(
    config: dict[str, Any] | None,
    record: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = normalize_journal_config(config)
    if not cfg.get("enabled", False):
        return {"enabled": False, "mongo_status": "disabled", "local_status": "disabled"}

    required = bool(cfg.get("required", False))
    timestamp = now or datetime.now(CST)
    row = dict(record)
    row.setdefault("operation_id", str(uuid.uuid4()))
    row.setdefault("time", timestamp.strftime("%Y-%m-%d %H:%M:%S %z"))
    row.setdefault("recorded_at", timestamp.strftime("%Y-%m-%d %H:%M:%S %z"))
    row.setdefault("source", "alpha-vault")

    status: dict[str, Any] = {"enabled": True}
    local_pattern = cfg.get("local_daily_path", DEFAULT_LOCAL_DAILY_PATH)
    if local_pattern:
        local_path = daily_path(local_pattern, timestamp)
        append_jsonl(local_path, row)
        status["local_status"] = "written"
        status["local_path"] = str(local_path)
    else:
        status["local_status"] = "disabled"

    uri, uri_source = mongo_uri_from_config(cfg)
    if not uri:
        status["mongo_status"] = "disabled_no_uri"
        if required:
            raise OperationJournalError(f"Mongo operation journal is required but {uri_source} is not set")
        return status
    if MongoClient is None:
        status["mongo_status"] = "disabled_missing_pymongo"
        if required:
            raise OperationJournalError("Mongo operation journal is required but pymongo is not installed")
        return status

    database = str(cfg.get("mongo_database") or DEFAULT_DATABASE)
    collection = str(cfg.get("mongo_collection") or DEFAULT_COLLECTION)
    timeout_ms = int(cfg.get("server_selection_timeout_ms", 5000))
    client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    try:
        result = client[database][collection].insert_one(row)
        status["mongo_status"] = "inserted"
        status["mongo_database"] = database
        status["mongo_collection"] = collection
        status["mongo_inserted_id"] = str(result.inserted_id)
    except Exception as exc:
        status["mongo_status"] = "error"
        status["mongo_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        if required:
            raise OperationJournalError(status["mongo_error"]) from exc
    finally:
        client.close()
    return status


def assert_operation_journal_ready(config: dict[str, Any] | None) -> None:
    cfg = normalize_journal_config(config)
    if not cfg.get("enabled", False) or not cfg.get("required", False):
        return
    uri, uri_source = mongo_uri_from_config(cfg)
    if not uri:
        raise OperationJournalError(f"Mongo operation journal is required but {uri_source} is not set")
    if MongoClient is None:
        raise OperationJournalError("Mongo operation journal is required but pymongo is not installed")
    timeout_ms = int(cfg.get("server_selection_timeout_ms", 5000))
    client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise OperationJournalError(
            f"Mongo operation journal is required but ping failed: {type(exc).__name__}: {str(exc)[:200]}"
        ) from exc
    finally:
        client.close()

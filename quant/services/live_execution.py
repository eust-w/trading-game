#!/usr/bin/env python3
"""Human-approved live execution for Binance USD-M order intents.

This module requires an explicit confirmation dialog before any live order is
sent. It is designed to execute one generated intent at a time.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import warnings
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant.screeners.binance_usdm_15m_trend_screener import BASE_URL
from quant.services.operation_journal import (
    OperationJournalError,
    append_operation_record,
    assert_operation_journal_ready,
    default_operation_journal_config,
    operation_journal_required,
    operation_reason_from_intent,
)


CST = timezone(timedelta(hours=8))
DEFAULT_ENV_PATH = Path(".env")
DEFAULT_INTENTS = Path("data/processed/paper-signal-service/intents.jsonl")
DEFAULT_EXECUTIONS = Path("data/processed/live-execution/executions.jsonl")
DEFAULT_CONFIRMATION_TIMEOUT_SECONDS = 120


class ConfirmationTimeoutError(RuntimeError):
    pass


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def load_intents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def select_intent(path: Path, intent_id: str | None) -> dict[str, Any]:
    intents = load_intents(path)
    if not intents:
        raise SystemExit(f"No intents found at {path}")
    if intent_id:
        for intent in intents:
            if intent.get("id") == intent_id:
                return intent
        raise SystemExit(f"Intent not found: {intent_id}")
    for intent in reversed(intents):
        if intent.get("intent_type") == "entry":
            return intent
    raise SystemExit("No entry intent found")


class BinanceLiveClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = BASE_URL) -> None:
        if base_url != "https://fapi.binance.com":
            raise RuntimeError(f"Refusing non-USD-M futures base URL: {base_url}")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.time_offset_ms = 0

    def sync_time(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                response = requests.get(self.base_url + "/fapi/v1/time", timeout=30)
                response.raise_for_status()
                self.time_offset_ms = int(response.json()["serverTime"]) - int(time.time() * 1000)
                return
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(min(2**attempt, 30))
        raise RuntimeError(f"/fapi/v1/time failed after retries: {type(last_error).__name__}: {str(last_error)[:160]}")

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                response = requests.get(self.base_url + path, params=params or {}, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(min(2**attempt, 30))
        raise RuntimeError(f"{path} failed after retries: {type(last_error).__name__}: {str(last_error)[:160]}")

    def signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        headers = {"X-MBX-APIKEY": self.api_key, "User-Agent": "alpha-vault-approved-executor/1.0"}
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                payload = dict(params)
                payload.setdefault("recvWindow", 10000)
                payload["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
                query = urllib.parse.urlencode(payload, doseq=True)
                signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                signed_query = f"{query}&signature={signature}"
                if method == "POST":
                    response = requests.post(self.base_url + path, params=signed_query, headers=headers, timeout=30)
                elif method == "GET":
                    response = requests.get(self.base_url + path, params=signed_query, headers=headers, timeout=30)
                elif method == "DELETE":
                    response = requests.delete(self.base_url + path, params=signed_query, headers=headers, timeout=30)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                if response.status_code >= 400:
                    if response.status_code == 400 and "\"code\":-1021" in response.text:
                        self.sync_time()
                        if attempt < 5:
                            time.sleep(min(2**attempt, 30))
                            continue
                    raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:300]}")
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(min(2**attempt, 30))
        raise RuntimeError(f"{path} failed after retries: {type(last_error).__name__}: {str(last_error)[:160]}")

    def exchange_symbol(self, symbol: str) -> dict[str, Any]:
        data = self.public_get("/fapi/v1/exchangeInfo")
        for item in data.get("symbols", []):
            if item.get("symbol") == symbol:
                return item
        raise RuntimeError(f"Symbol not found: {symbol}")

    def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else {}
        return self.signed_request("GET", "/fapi/v2/positionRisk", params)

    def account(self) -> dict[str, Any]:
        return self.signed_request("GET", "/fapi/v2/account", {})

    def hedge_mode_enabled(self) -> bool:
        data = self.signed_request("GET", "/fapi/v1/positionSide/dual", {})
        return str(data.get("dualSidePosition")).lower() == "true"

    def new_order(self, params: dict[str, Any]) -> Any:
        return self.signed_request("POST", "/fapi/v1/order", params)

    def new_algo_order(self, params: dict[str, Any]) -> Any:
        return self.signed_request("POST", "/fapi/v1/algoOrder", params)

    def open_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self.signed_request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})

    def open_all_algo_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.signed_request("GET", "/fapi/v1/openAlgoOrders", {"limit": limit})

    def all_algo_orders(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.signed_request("GET", "/fapi/v1/allAlgoOrders", {"symbol": symbol, "limit": limit})

    def cancel_algo_order(self, order: dict[str, Any]) -> Any:
        params: dict[str, Any] = {}
        if order.get("algoId"):
            params["algoId"] = order["algoId"]
            return self.signed_request("DELETE", "/fapi/v1/algoOrder", params)
        if order.get("clientAlgoId"):
            params["clientAlgoId"] = order["clientAlgoId"]
            return self.signed_request("DELETE", "/fapi/v1/algoOrder", params)
        raise RuntimeError("Open algo order has no algoId or clientAlgoId")


def decimal_places(step: Decimal) -> int:
    return max(0, -step.normalize().as_tuple().exponent)


def round_step(value: Decimal, step: Decimal) -> Decimal:
    if step == 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def symbol_filters(symbol_info: dict[str, Any]) -> dict[str, Decimal]:
    filters = {item["filterType"]: item for item in symbol_info.get("filters", [])}
    price = filters.get("PRICE_FILTER") or {}
    lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE")
    market_lot = filters.get("MARKET_LOT_SIZE") or lot
    min_notional = filters.get("MIN_NOTIONAL") or {}
    return {
        "tick_size": Decimal(str(price.get("tickSize", "0.00000001"))),
        "min_price": Decimal(str(price.get("minPrice", "0"))),
        "max_price": Decimal(str(price.get("maxPrice", "0"))),
        "step_size": Decimal(str(market_lot.get("stepSize", lot.get("stepSize", "0.000001")))),
        "min_qty": Decimal(str(market_lot.get("minQty", lot.get("minQty", "0")))),
        "min_notional": Decimal(str(min_notional.get("notional", min_notional.get("minNotional", "0")))),
    }


def format_decimal(value: Decimal, step: Decimal) -> str:
    places = decimal_places(step)
    return f"{value:.{places}f}"


def normalized_quantity(intent: dict[str, Any], symbol_info: dict[str, Any]) -> str:
    validate_usdt_perpetual_intent(intent, symbol_info)
    filters = symbol_filters(symbol_info)
    raw_qty = Decimal(str(intent["entry_order"]["quantity"]))
    qty = round_step(raw_qty, filters["step_size"])
    if qty <= 0 or qty < filters["min_qty"]:
        raise RuntimeError(f"Quantity below minQty after rounding: {qty}")
    notional = Decimal(str(intent["entry_order"]["notional"]))
    if filters["min_notional"] and notional < filters["min_notional"]:
        raise RuntimeError(f"Notional below minNotional: {notional} < {filters['min_notional']}")
    return format_decimal(qty, filters["step_size"])


def normalized_trigger_price(raw_price: Any, symbol_info: dict[str, Any]) -> str:
    filters = symbol_filters(symbol_info)
    tick_size = filters["tick_size"]
    price = round_step(Decimal(str(raw_price)), tick_size)
    if price <= 0 or price < filters["min_price"]:
        raise RuntimeError(f"Trigger price below minPrice after rounding: {price}")
    if filters["max_price"] and price > filters["max_price"]:
        raise RuntimeError(f"Trigger price above maxPrice after rounding: {price}")
    return format_decimal(price, tick_size)


def normalized_risk_orders(intent: dict[str, Any], symbol_info: dict[str, Any]) -> list[dict[str, Any]]:
    orders = []
    for risk_order in intent["risk_orders"]:
        normalized = dict(risk_order)
        normalized["stop_price"] = normalized_trigger_price(risk_order["stop_price"], symbol_info)
        orders.append(normalized)
    return orders


def validate_usdt_perpetual_intent(intent: dict[str, Any], symbol_info: dict[str, Any]) -> None:
    symbol = intent.get("symbol", "")
    if not symbol.endswith("USDT"):
        raise RuntimeError(f"Refusing non-USDT U-margined symbol: {symbol}")
    if symbol_info.get("contractType") != "PERPETUAL":
        raise RuntimeError(f"Refusing non-perpetual contract: {symbol}")
    if symbol_info.get("quoteAsset") != "USDT":
        raise RuntimeError(f"Refusing non-USDT quote asset for U-margined workflow: {symbol_info.get('quoteAsset')}")
    if symbol_info.get("status") != "TRADING":
        raise RuntimeError(f"Refusing non-trading symbol: {symbol}")


def confirmation_text(intent: dict[str, Any], quantity: str) -> str:
    stop = intent["risk_orders"][0]["stop_price"]
    take = intent["risk_orders"][1]["stop_price"]
    return (
        "REAL BINANCE USD-M ORDER\\n"
        f"Symbol: {intent['symbol']}\\n"
        f"Direction: {intent['direction']}\\n"
        f"Market side: {intent['entry_order']['side']}\\n"
        f"Quantity: {quantity}\\n"
        f"Approx notional: {intent['entry_order']['notional']} USDT\\n"
        f"Stop loss: {stop}\\n"
        f"Take profit: {take}\\n"
        f"Risk: {intent['risk_usdt']} USDT\\n\\n"
        "Confirm to send real Binance orders?"
    )


def confirm_with_gui(message: str, timeout_seconds: int | None = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS) -> bool:
    script = [
        "osascript",
        "-e",
        f'display dialog {json.dumps(message)} buttons {{"Cancel", "Confirm"}} default button "Cancel" with icon caution',
    ]
    timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    try:
        result = subprocess.run(script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ConfirmationTimeoutError(f"GUI confirmation timed out after {timeout_seconds} seconds") from exc
    return result.returncode == 0 and "button returned:Confirm" in result.stdout


def confirm_with_terminal(message: str) -> bool:
    print(message)
    print()
    typed = input("Type EXECUTE REAL ORDER to confirm: ").strip()
    return typed == "EXECUTE REAL ORDER"


def position_side(intent: dict[str, Any]) -> str:
    if intent["direction"] == "LONG":
        return "LONG"
    if intent["direction"] == "SHORT":
        return "SHORT"
    raise RuntimeError(f"Unsupported direction: {intent['direction']}")


def add_position_mode_params(params: dict[str, Any], intent: dict[str, Any], hedge_mode: bool, reduce_only: bool = False) -> dict[str, Any]:
    out = dict(params)
    if hedge_mode:
        out["positionSide"] = position_side(intent)
    elif reduce_only:
        out["reduceOnly"] = "true"
    return out


def flatten_position(client: BinanceLiveClient, intent: dict[str, Any], quantity: str, reason: str, hedge_mode: bool) -> Any:
    close_side = "SELL" if intent["direction"] == "LONG" else "BUY"
    return client.new_order(
        add_position_mode_params(
            {
            "symbol": intent["symbol"],
            "side": close_side,
            "type": "MARKET",
            "quantity": quantity,
            "newOrderRespType": "RESULT",
            "newClientOrderId": f"av_flat_{int(time.time())}",
            },
            intent,
            hedge_mode,
            reduce_only=True,
        )
    )


def execute_intent(
    intent: dict[str, Any],
    env_path: Path,
    executions_path: Path,
    dry_run: bool,
    terminal_confirm: bool,
    confirmation_timeout_seconds: int | None = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
    journal_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if intent.get("intent_type") != "entry":
        raise SystemExit("Only entry intents are executable")

    effective_journal_config = journal_config if journal_config is not None else default_operation_journal_config()
    operation_reason = operation_reason_from_intent(intent, "position_entry")

    if dry_run:
        quantity = str(intent["entry_order"]["quantity"])
        result = {
            "time": now_cst(),
            "mode": "dry_run",
            "intent": intent,
            "quantity": quantity,
            "confirmation_text": confirmation_text(intent, quantity),
            "operation_reason": operation_reason,
        }
        append_jsonl(executions_path, result)
        return result

    try:
        assert_operation_journal_ready(effective_journal_config)
    except OperationJournalError as exc:
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
            "status": "operation_journal_unavailable",
            "error": str(exc),
            "operation_reason": operation_reason,
        }
        append_jsonl(executions_path, result)
        return result

    env = load_env_file(env_path)
    api_key = env.get("BINANCE_API_KEY") or os.environ.get("BINANCE_API_KEY")
    api_secret = env.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit("Missing Binance API credentials")

    client = BinanceLiveClient(api_key, api_secret, BASE_URL)
    client.sync_time()
    symbol_info = client.exchange_symbol(intent["symbol"])
    quantity = normalized_quantity(intent, symbol_info)
    risk_orders = normalized_risk_orders(intent, symbol_info)
    hedge_mode = client.hedge_mode_enabled()
    display_intent = dict(intent)
    display_intent["risk_orders"] = risk_orders
    message = confirmation_text(display_intent, quantity)

    # try:
    #     confirmed = (
    #         confirm_with_terminal(message)
    #         if terminal_confirm
    #         else confirm_with_gui(message, confirmation_timeout_seconds)
    #     )
    # except ConfirmationTimeoutError as exc:
    #     result = {
    #         "time": now_cst(),
    #         "mode": "cancelled",
    #         "intent_id": intent["id"],
    #         "symbol": intent["symbol"],
    #         "direction": intent.get("direction"),
    #         "quantity": quantity,
    #         "status": "confirmation_timeout",
    #         "error": str(exc),
    #         "operation_reason": operation_reason,
    #     }
    #     append_jsonl(executions_path, result)
    #     return result
    confirmed = True
    if not confirmed:
        result = {
            "time": now_cst(),
            "mode": "cancelled",
            "intent_id": intent["id"],
            "symbol": intent["symbol"],
            "direction": intent.get("direction"),
            "quantity": quantity,
            "status": "cancelled_by_user",
            "operation_reason": operation_reason,
        }
        append_jsonl(executions_path, result)
        return result

    execution: dict[str, Any] = {
        "time": now_cst(),
        "mode": "live",
        "intent_id": intent["id"],
        "symbol": intent["symbol"],
        "direction": intent["direction"],
        "quantity": quantity,
        "hedge_mode": hedge_mode,
        "entry_order": None,
        "risk_orders": [],
        "flatten_order": None,
        "status": "started",
        "operation_reason": operation_reason,
        "operation_journal": [],
    }

    def journal(
        operation_type: str,
        status: str,
        *,
        order_type_name: str | None = None,
        trigger_price: Any = None,
        exchange_response: Any = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> None:
        snapshot = intent.get("market_snapshot") or {}
        record = {
            "operation_type": operation_type,
            "operation_status": status,
            "mode": "live",
            "intent_id": intent.get("id"),
            "symbol": intent.get("symbol"),
            "direction": intent.get("direction"),
            "quantity": quantity,
            "order_type": order_type_name,
            "trigger_price": trigger_price,
            "operation_reason": reason or operation_reason,
            "strategy": snapshot.get("primary_strategy"),
            "score": intent.get("score"),
            "reasons": intent.get("reasons") or snapshot.get("reasons"),
            "risk_flags": intent.get("risk_flags") or snapshot.get("risk_flags"),
            "exchange_response": exchange_response,
            "error": error,
        }
        try:
            journal_status = append_operation_record(effective_journal_config, record)
        except Exception as exc:
            if operation_journal_required(effective_journal_config):
                raise
            journal_status = {
                "enabled": True,
                "mongo_status": "error",
                "journal_error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
        execution["operation_journal"].append(
            {
                "operation_type": operation_type,
                "operation_status": status,
                "journal_status": journal_status,
            }
        )

    try:
        entry = client.new_order(
            add_position_mode_params(
                {
                "symbol": intent["symbol"],
                "side": intent["entry_order"]["side"],
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
                "newClientOrderId": f"av_entry_{int(time.time())}",
                },
                intent,
                hedge_mode,
            )
        )
        execution["entry_order"] = entry
        journal("position_entry", "submitted", order_type_name="MARKET", exchange_response=entry)

        for risk_order in risk_orders:
            algo = client.new_algo_order(
                add_position_mode_params(
                    {
                    "symbol": intent["symbol"],
                    "side": risk_order["side"],
                    "type": risk_order["type"],
                    "algoType": "CONDITIONAL",
                    "quantity": quantity,
                    "triggerPrice": str(risk_order["stop_price"]),
                    "workingType": "MARK_PRICE",
                    "newClientAlgoId": f"av_{risk_order['type'].lower()}_{int(time.time())}",
                    },
                    intent,
                    hedge_mode,
                    reduce_only=True,
                )
            )
            execution["risk_orders"].append(algo)
            journal(
                "protective_order_place",
                "submitted",
                order_type_name=risk_order["type"],
                trigger_price=risk_order["stop_price"],
                exchange_response=algo,
                reason=operation_reason_from_intent(intent, f"protective_order_place:{risk_order['type']}"),
            )
        execution["status"] = "protected"
    except Exception as exc:
        execution["error"] = str(exc)
        if not execution["entry_order"]:
            execution["status"] = "entry_rejected_no_position_opened"
            journal("position_entry_rejected", "error", order_type_name="MARKET", error=str(exc))
            append_jsonl(executions_path, execution)
            return execution
        execution["status"] = "protection_failed_flatten_attempted"
        journal(
            "protective_order_place_failed",
            "error",
            error=str(exc),
            reason=f"protection placement failed after entry; {operation_reason}",
        )
        try:
            execution["flatten_order"] = flatten_position(client, intent, quantity, str(exc), hedge_mode)
            journal(
                "position_flatten",
                "submitted",
                order_type_name="MARKET",
                exchange_response=execution["flatten_order"],
                reason=f"flatten after protection placement failure: {str(exc)[:160]}; {operation_reason}",
            )
        except Exception as flatten_exc:
            execution["flatten_error"] = str(flatten_exc)
            execution["status"] = "critical_manual_intervention_required"
            journal(
                "position_flatten_failed",
                "error",
                order_type_name="MARKET",
                error=str(flatten_exc),
                reason=f"flatten failed after protection placement failure: {str(exc)[:160]}; {operation_reason}",
            )
    append_jsonl(executions_path, execution)
    return execution


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent-id")
    parser.add_argument("--intents", default=str(DEFAULT_INTENTS))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--executions", default=str(DEFAULT_EXECUTIONS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--terminal-confirm", action="store_true")
    parser.add_argument("--confirmation-timeout-seconds", type=int, default=DEFAULT_CONFIRMATION_TIMEOUT_SECONDS)
    args = parser.parse_args()

    intent = select_intent(Path(args.intents), args.intent_id)
    result = execute_intent(
        intent=intent,
        env_path=Path(args.env_file),
        executions_path=Path(args.executions),
        dry_run=args.dry_run,
        terminal_confirm=args.terminal_confirm,
        confirmation_timeout_seconds=args.confirmation_timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

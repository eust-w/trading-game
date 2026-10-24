#!/usr/bin/env python3
"""Adjust live Binance USD-M protective orders after market scans."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant.screeners.binance_usdm_15m_trend_screener import BASE_URL
from quant.services.live_execution import (
    BinanceLiveClient,
    add_position_mode_params,
    append_jsonl,
    confirm_with_gui,
    confirm_with_terminal,
    load_env_file,
    normalized_trigger_price,
    now_cst,
)
from quant.services.operation_journal import (
    append_operation_record,
    assert_operation_journal_ready,
    operation_journal_required,
    operation_reason_from_plan,
    stale_order_reason,
)
from quant.services.risk_model import adaptive_exit_prices, should_tighten_take_profit


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def active_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions = []
    for row in rows:
        amount = dec(row.get("positionAmt", "0"))
        if amount != 0:
            positions.append(row)
    return positions


def position_snapshot(position: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "symbol",
        "positionSide",
        "positionAmt",
        "entryPrice",
        "breakEvenPrice",
        "markPrice",
        "unRealizedProfit",
        "liquidationPrice",
        "leverage",
        "marginType",
    ]
    return {field: position.get(field) for field in fields if field in position}


def account_snapshot(account: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "availableBalance",
        "totalWalletBalance",
        "totalMarginBalance",
        "totalInitialMargin",
        "totalMaintMargin",
        "totalUnrealizedProfit",
        "maxWithdrawAmount",
    ]
    return {field: account.get(field) for field in fields if field in account}


def position_direction(position: dict[str, Any]) -> str:
    side = position.get("positionSide")
    if side in {"LONG", "SHORT"}:
        return side
    return "LONG" if dec(position.get("positionAmt", "0")) > 0 else "SHORT"


def trigger_price(order: dict[str, Any]) -> Decimal | None:
    for key in ("triggerPrice", "stopPrice", "activatePrice"):
        value = order.get(key)
        if value not in (None, "", "0", "0.0", "0.000000"):
            return dec(value)
    return None


def order_type(order: dict[str, Any]) -> str:
    return str(order.get("orderType") or order.get("type") or order.get("origType") or "")


def format_exchange_ms(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone(timedelta(hours=8)))
    except (TypeError, ValueError, OSError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


def protective_orders_for_position(orders: list[dict[str, Any]], position: dict[str, Any], hedge_mode: bool) -> list[dict[str, Any]]:
    direction = position_direction(position)
    symbol = position["symbol"]
    matched = []
    for order in orders:
        if order.get("symbol") != symbol:
            continue
        if hedge_mode and order.get("positionSide") not in {direction, None, ""}:
            continue
        if order_type(order) in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            matched.append(order)
    return matched


def is_protective_order(order: dict[str, Any]) -> bool:
    reduce_only = str(order.get("reduceOnly", False)).lower() == "true"
    return order_type(order) in {"STOP_MARKET", "TAKE_PROFIT_MARKET"} and reduce_only


def position_key(position: dict[str, Any], hedge_mode: bool) -> tuple[str, str]:
    side = position_direction(position) if hedge_mode else "BOTH"
    return position["symbol"], side


def order_position_key(order: dict[str, Any], hedge_mode: bool) -> tuple[str, str]:
    side = str(order.get("positionSide") or "BOTH") if hedge_mode else "BOTH"
    return str(order.get("symbol")), side


def stale_protective_orders(orders: list[dict[str, Any]], positions: list[dict[str, Any]], hedge_mode: bool) -> list[dict[str, Any]]:
    active = {position_key(position, hedge_mode) for position in positions}
    stale = []
    for order in orders:
        if is_protective_order(order) and order_position_key(order, hedge_mode) not in active:
            stale.append(order)
    return stale


def current_protection(orders: list[dict[str, Any]]) -> tuple[Decimal | None, Decimal | None]:
    stop = None
    take = None
    for order in orders:
        price = trigger_price(order)
        if price is None:
            continue
        kind = order_type(order)
        if kind == "STOP_MARKET":
            stop = price
        elif kind == "TAKE_PROFIT_MARKET":
            take = price
    return stop, take


def latest_order_update_ms(orders: list[dict[str, Any]]) -> int | None:
    latest = None
    for order in orders:
        for key in ("updateTime", "createTime"):
            value = order.get(key)
            if value in (None, "", 0, "0"):
                continue
            try:
                timestamp = int(value)
            except (TypeError, ValueError):
                continue
            if timestamp <= 0:
                continue
            latest = timestamp if latest is None else max(latest, timestamp)
    return latest


def recent_protection_history(
    client: BinanceLiveClient,
    position: dict[str, Any],
    hedge_mode: bool,
    limit: int = 8,
) -> list[dict[str, Any]]:
    history = client.all_algo_orders(position["symbol"], limit=max(1, int(limit)))
    matched = protective_orders_for_position(history, position, hedge_mode)
    out = []
    for order in matched[:limit]:
        out.append(
            {
                "algoId": order.get("algoId"),
                "orderType": order_type(order),
                "algoStatus": order.get("algoStatus"),
                "triggerPrice": order.get("triggerPrice"),
                "updateTime": format_exchange_ms(order.get("updateTime")),
                "createTime": format_exchange_ms(order.get("createTime")),
            }
        )
    return out


def desired_prices(position: dict[str, Any], scan_row: dict[str, Any], config: dict[str, Any]) -> tuple[Decimal, Decimal, dict[str, Any]]:
    mark = dec(position.get("markPrice") or scan_row["last_price"])
    exits = adaptive_exit_prices(scan_row, config, float(mark), position_direction(position))
    return dec(exits["stop_loss"]), dec(exits["take_profit"]), exits["plan"]


def recent_trigger_price(recent_history: list[dict[str, Any]], target_type: str) -> Decimal | None:
    for order in recent_history:
        if order.get("orderType") != target_type:
            continue
        price = order.get("triggerPrice")
        if price not in (None, "", "0", "0.0", "0.000000"):
            return dec(price)
    return None


def clamp_protective_price(direction: str, price_kind: str, desired: Decimal, floor: Decimal | None) -> Decimal:
    if floor is None:
        return desired
    if direction == "LONG":
        return max(desired, floor)
    return min(desired, floor)


def breakeven_floor(position: dict[str, Any], mark: Decimal, config: dict[str, Any]) -> Decimal | None:
    risk = config.get("risk", {})
    activation_pct = dec(risk.get("break_even_activation_pct", risk.get("min_stop_pct", 0.35))) / dec("100")
    if activation_pct <= 0:
        return None
    break_even_raw = position.get("breakEvenPrice") or position.get("entryPrice")
    if break_even_raw in (None, "", "0", "0.0"):
        return None
    break_even = dec(break_even_raw)
    direction = position_direction(position)
    if direction == "LONG":
        if mark < break_even * (dec("1") + activation_pct):
            return None
    else:
        if mark > break_even * (dec("1") - activation_pct):
            return None
    return break_even


def profit_lock_floor(position: dict[str, Any], mark: Decimal, config: dict[str, Any]) -> Decimal | None:
    risk = config.get("risk", {})
    if not risk.get("profit_lock_enabled", False):
        return None
    activation_pct = dec(risk.get("profit_lock_activation_pct", 0.6)) / dec("100")
    retain_pct = dec(risk.get("profit_lock_retain_pct", 45)) / dec("100")
    if activation_pct <= 0 or retain_pct <= 0:
        return None
    retain_pct = min(retain_pct, dec("0.95"))
    break_even_raw = position.get("breakEvenPrice") or position.get("entryPrice")
    if break_even_raw in (None, "", "0", "0.0"):
        return None
    break_even = dec(break_even_raw)
    if break_even <= 0:
        return None
    direction = position_direction(position)
    if direction == "LONG":
        favorable_move = mark / break_even - dec("1")
        if favorable_move < activation_pct:
            return None
        return break_even + (mark - break_even) * retain_pct
    favorable_move = dec("1") - mark / break_even
    if favorable_move < activation_pct:
        return None
    return break_even - (break_even - mark) * retain_pct


def unsuitable_exit_prices(position: dict[str, Any], scan_row: dict[str, Any], config: dict[str, Any]) -> tuple[Decimal, Decimal, str | None]:
    if not config.get("risk", {}).get("unsuitable_exit_enabled", True):
        return Decimal("0"), Decimal("0"), None
    direction = position_direction(position)
    mark = dec(position.get("markPrice") or scan_row["last_price"])
    opposite_score = dec(config.get("risk", {}).get("unsuitable_exit_opposite_score", 65))
    hold_score = dec(config.get("risk", {}).get("unsuitable_exit_hold_score", 45))
    band_pct = dec(config.get("risk", {}).get("unsuitable_exit_band_pct", 0.18)) / dec("100")
    reasons = set(str(scan_row.get("reasons", "")).split(","))
    row_direction = scan_row.get("direction")
    long_score = dec(scan_row.get("long_score", 0))
    short_score = dec(scan_row.get("short_score", 0))
    long_opposite = (
        direction == "LONG"
        and row_direction == "SHORT"
        and short_score >= opposite_score
        and (
            {"15m_ema_bear", "1h_trend_down"}.issubset(reasons)
            or {"micro_capital_outflow", "1m_micro_bear"}.issubset(reasons)
        )
    )
    short_opposite = (
        direction == "SHORT"
        and row_direction == "LONG"
        and long_score >= opposite_score
        and (
            {"15m_ema_bull", "1h_trend_up"}.issubset(reasons)
            or {"micro_capital_inflow", "1m_micro_bull"}.issubset(reasons)
        )
    )
    long_not_supported = (
        direction == "LONG"
        and long_score < hold_score
        and ("15m_ema_bear" in reasons or "1h_trend_down" in reasons or "micro_capital_outflow" in reasons)
    )
    short_not_supported = (
        direction == "SHORT"
        and short_score < hold_score
        and ("15m_ema_bull" in reasons or "1h_trend_up" in reasons or "micro_capital_inflow" in reasons)
    )
    reason = None
    if long_opposite or short_opposite:
        reason = "opposite_trend"
    elif long_not_supported or short_not_supported:
        reason = "hold_signal_failed"
    if reason is None:
        return Decimal("0"), Decimal("0"), None
    if direction == "LONG":
        return mark * (dec("1") - band_pct), mark * (dec("1") + band_pct), reason
    return mark * (dec("1") + band_pct), mark * (dec("1") - band_pct), reason


def should_update_price(current: Decimal | None, desired: Decimal, threshold: Decimal) -> bool:
    if current is None:
        return True
    if current == 0:
        return True
    return abs(desired / current - Decimal("1")) > threshold


def has_min_tick_move(
    current: Decimal | None,
    desired: Decimal,
    tick_size: Decimal,
    min_ticks: int,
) -> bool:
    if current is None:
        return True
    if tick_size <= 0:
        return True
    if min_ticks <= 1:
        return current != desired
    return abs(desired - current) >= tick_size * Decimal(min_ticks)


def immediate_trigger_reason(
    direction: str,
    order_type_name: str,
    trigger: Decimal,
    mark: Decimal,
    tick_size: Decimal,
    min_distance_ticks: int = 0,
) -> str | None:
    buffer = max(tick_size, Decimal("0")) * Decimal(max(0, min_distance_ticks))
    if direction == "LONG":
        if order_type_name == "STOP_MARKET" and trigger >= mark - buffer:
            return f"long stop trigger {trigger} is too close to mark {mark}"
        if order_type_name == "TAKE_PROFIT_MARKET" and trigger <= mark + buffer:
            return f"long take-profit trigger {trigger} is too close to mark {mark}"
    else:
        if order_type_name == "STOP_MARKET" and trigger <= mark + buffer:
            return f"short stop trigger {trigger} is too close to mark {mark}"
        if order_type_name == "TAKE_PROFIT_MARKET" and trigger >= mark - buffer:
            return f"short take-profit trigger {trigger} is too close to mark {mark}"
    return None


def should_improve_stop(
    direction: str,
    current: Decimal | None,
    desired: Decimal,
    threshold: Decimal,
    tick_size: Decimal,
    min_ticks: int,
) -> bool:
    if current is None:
        return True
    if not has_min_tick_move(current, desired, tick_size, min_ticks):
        return False
    if direction == "LONG":
        return desired > current * (Decimal("1") + threshold)
    return desired < current * (Decimal("1") - threshold)


def should_improve_take(
    direction: str,
    current: Decimal | None,
    desired: Decimal,
    threshold: Decimal,
    tick_size: Decimal,
    min_ticks: int,
    allow_tighten: bool = False,
) -> bool:
    if current is None:
        return True
    if not has_min_tick_move(current, desired, tick_size, min_ticks):
        return False
    if direction == "LONG":
        if desired > current * (Decimal("1") + threshold):
            return True
        return allow_tighten and desired < current * (Decimal("1") - threshold)
    if desired < current * (Decimal("1") - threshold):
        return True
    return allow_tighten and desired > current * (Decimal("1") + threshold)


def build_adjustments(
    client: BinanceLiveClient,
    positions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    hedge_mode: bool,
    all_open_orders: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_symbol = {row["symbol"]: row for row in rows}
    threshold = dec(config.get("protection", {}).get("min_adjust_pct", 0.08)) / dec("100")
    min_adjust_ticks = max(1, int(config.get("protection", {}).get("min_adjust_ticks", 2)))
    min_trigger_distance_ticks = max(0, int(config.get("protection", {}).get("min_trigger_distance_ticks", 1)))
    min_existing_order_age_seconds = max(
        0,
        int(config.get("protection", {}).get("min_existing_order_age_seconds", 300)),
    )
    orders = all_open_orders

    def build_one(position: dict[str, Any]) -> dict[str, Any] | None:
        symbol = position["symbol"]
        scan_row = by_symbol.get(symbol)
        if not scan_row:
            return None
        symbol_info = client.exchange_symbol(symbol)
        direction = position_direction(position)
        lookup_source = "all_open_orders" if orders is not None else "symbol_open_orders"
        source_orders = orders if orders is not None else client.open_algo_orders(symbol)
        open_orders = protective_orders_for_position(source_orders, position, hedge_mode)
        if orders is not None and not open_orders:
            refreshed_orders = client.open_algo_orders(symbol)
            refreshed_open_orders = protective_orders_for_position(refreshed_orders, position, hedge_mode)
            if refreshed_open_orders:
                open_orders = refreshed_open_orders
                lookup_source = "symbol_refresh"
        current_stop, current_take = current_protection(open_orders)
        if current_stop is not None and current_take is not None and min_existing_order_age_seconds > 0:
            latest_update = latest_order_update_ms(open_orders)
            if latest_update is not None:
                age_seconds = int(time.time() - (latest_update / 1000))
                if age_seconds < min_existing_order_age_seconds:
                    return None
        recent_history = []
        if current_stop is None and current_take is None:
            recent_history = recent_protection_history(client, position, hedge_mode)
        recent_stop = recent_trigger_price(recent_history, "STOP_MARKET")
        recent_take = recent_trigger_price(recent_history, "TAKE_PROFIT_MARKET")
        price_filters = {
            item["filterType"]: item
            for item in symbol_info.get("filters", [])
        }
        tick_size = dec((price_filters.get("PRICE_FILTER") or {}).get("tickSize", "0.00000001"))
        desired_stop, desired_take, exit_plan = desired_prices(position, scan_row, config)
        exit_stop, exit_take, exit_signal = unsuitable_exit_prices(position, scan_row, config)
        mark = dec(position.get("markPrice") or scan_row["last_price"])
        direction = position_direction(position)
        take_tighten_allowed = should_tighten_take_profit(scan_row, config, direction)
        break_even = breakeven_floor(position, mark, config)
        profit_lock = profit_lock_floor(position, mark, config)
        protection_notes = []
        normalized_break_even = None
        normalized_profit_lock = None
        normalized_current_stop = (
            dec(normalized_trigger_price(current_stop, symbol_info))
            if current_stop is not None
            else None
        )
        normalized_current_take = (
            dec(normalized_trigger_price(current_take, symbol_info))
            if current_take is not None
            else None
        )
        normalized_desired_stop = dec(normalized_trigger_price(desired_stop, symbol_info))
        normalized_desired_take = dec(normalized_trigger_price(desired_take, symbol_info))
        if exit_signal:
            desired_stop = exit_stop
            desired_take = exit_take
            normalized_desired_stop = dec(normalized_trigger_price(desired_stop, symbol_info))
            normalized_desired_take = dec(normalized_trigger_price(desired_take, symbol_info))
            stop_changed = has_min_tick_move(
                normalized_current_stop,
                normalized_desired_stop,
                tick_size,
                min_adjust_ticks,
            ) and should_update_price(
                normalized_current_stop,
                normalized_desired_stop,
                threshold,
            )
            take_changed = has_min_tick_move(
                normalized_current_take,
                normalized_desired_take,
                tick_size,
                min_adjust_ticks,
            ) and should_update_price(
                normalized_current_take,
                normalized_desired_take,
                threshold,
            )
        else:
            if break_even is not None:
                normalized_break_even = dec(normalized_trigger_price(break_even, symbol_info))
                normalized_desired_stop = clamp_protective_price(
                    direction,
                    "stop",
                    normalized_desired_stop,
                    normalized_break_even,
                )
            if profit_lock is not None:
                normalized_profit_lock = dec(normalized_trigger_price(profit_lock, symbol_info))
                normalized_desired_stop = clamp_protective_price(
                    direction,
                    "stop",
                    normalized_desired_stop,
                    normalized_profit_lock,
                )
            if normalized_current_stop is None and recent_stop is not None:
                normalized_recent_stop = dec(normalized_trigger_price(recent_stop, symbol_info))
                reason = immediate_trigger_reason(
                    direction,
                    "STOP_MARKET",
                    normalized_recent_stop,
                    mark,
                    tick_size,
                    min_trigger_distance_ticks,
                )
                if reason:
                    protection_notes.append(f"ignored_recent_stop:{reason}")
                else:
                    normalized_desired_stop = clamp_protective_price(
                        direction,
                        "stop",
                        normalized_desired_stop,
                        normalized_recent_stop,
                    )
            if normalized_current_take is None and recent_take is not None:
                normalized_recent_take = dec(normalized_trigger_price(recent_take, symbol_info))
                reason = immediate_trigger_reason(
                    direction,
                    "TAKE_PROFIT_MARKET",
                    normalized_recent_take,
                    mark,
                    tick_size,
                    min_trigger_distance_ticks,
                )
                if reason:
                    protection_notes.append(f"ignored_recent_take:{reason}")
                else:
                    normalized_desired_take = clamp_protective_price(
                        direction,
                        "take",
                        normalized_desired_take,
                        normalized_recent_take,
                    )
            break_even_raw = position.get("breakEvenPrice") or position.get("entryPrice")
            if break_even_raw not in (None, "", "0", "0.0"):
                min_net_pct = dec(config.get("risk", {}).get("adaptive_take_profit_min_net_pct", 0.12)) / dec("100")
                break_even_for_take = dec(break_even_raw)
                if break_even_for_take > 0:
                    if direction == "LONG":
                        min_take = break_even_for_take * (dec("1") + min_net_pct)
                        normalized_desired_take = max(
                            normalized_desired_take,
                            dec(normalized_trigger_price(min_take, symbol_info)),
                        )
                    else:
                        max_take = break_even_for_take * (dec("1") - min_net_pct)
                        normalized_desired_take = min(
                            normalized_desired_take,
                            dec(normalized_trigger_price(max_take, symbol_info)),
                        )

            stop_changed = should_improve_stop(
                direction,
                normalized_current_stop,
                normalized_desired_stop,
                threshold,
                tick_size,
                min_adjust_ticks,
            )
            take_changed = should_improve_take(
                direction,
                normalized_current_take,
                normalized_desired_take,
                threshold,
                tick_size,
                min_adjust_ticks,
                allow_tighten=take_tighten_allowed,
            )
        if not stop_changed and not take_changed:
            return None
        final_stop = normalized_desired_stop if stop_changed else normalized_current_stop
        final_take = normalized_desired_take if take_changed else normalized_current_take
        if final_stop is None or final_take is None:
            return None
        invalid_reasons = [
            reason
            for reason in (
                immediate_trigger_reason(
                    direction,
                    "STOP_MARKET",
                    final_stop,
                    mark,
                    tick_size,
                    min_trigger_distance_ticks,
                ),
                immediate_trigger_reason(
                    direction,
                    "TAKE_PROFIT_MARKET",
                    final_take,
                    mark,
                    tick_size,
                    min_trigger_distance_ticks,
                ),
            )
            if reason
        ]
        if invalid_reasons:
            return None
        plan = {
            "symbol": symbol,
            "direction": direction,
            "quantity": str(abs(dec(position["positionAmt"]))),
            "mark_price": str(position.get("markPrice") or scan_row["last_price"]),
            "current_stop": str(current_stop) if current_stop is not None else None,
            "current_take_profit": str(current_take) if current_take is not None else None,
            "new_stop": normalized_trigger_price(final_stop, symbol_info),
            "new_take_profit": normalized_trigger_price(final_take, symbol_info),
            "cancel_orders": open_orders,
            "score": scan_row.get("score"),
            "primary_strategy": scan_row.get("primary_strategy"),
            "exit_signal": exit_signal,
            "order_lookup_source": lookup_source,
            "recent_order_history": recent_history,
            "break_even_floor": str(normalized_break_even) if normalized_break_even is not None else None,
            "profit_lock_floor": str(normalized_profit_lock) if normalized_profit_lock is not None else None,
            "exit_model": exit_plan.get("model"),
            "exit_profile": exit_plan.get("profile"),
            "stop_pct": exit_plan.get("stop_pct"),
            "take_profit_r": exit_plan.get("take_profit_r"),
            "take_profit_pct": exit_plan.get("take_profit_pct"),
            "take_tighten_allowed": take_tighten_allowed,
            "exit_notes": ",".join(exit_plan.get("notes", [])),
            "volume_price_regime": scan_row.get("volume_price_regime"),
            "volume_price_score": scan_row.get("volume_price_score"),
            "volume_price_notes": scan_row.get("volume_price_notes"),
            "order_book_imbalance": scan_row.get("order_book_imbalance"),
            "order_book_spread_bps": scan_row.get("order_book_spread_bps"),
            "order_book_error": scan_row.get("order_book_error"),
            "protection_notes": ",".join(protection_notes),
            "tick_size": str(tick_size),
            "min_trigger_distance_ticks": min_trigger_distance_ticks,
        }
        plan["operation_reason"] = operation_reason_from_plan(plan)
        return plan

    worker_count = max(1, min(len(positions), int(config.get("protection", {}).get("concurrency", 3))))
    if worker_count == 1:
        return [plan for plan in (build_one(position) for position in positions) if plan]
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return [plan for plan in executor.map(build_one, positions) if plan]


def stale_order_summary(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": order.get("symbol"),
        "positionSide": order.get("positionSide"),
        "orderType": order_type(order),
        "side": order.get("side"),
        "quantity": order.get("quantity"),
        "triggerPrice": order.get("triggerPrice"),
        "algoId": order.get("algoId"),
        "clientAlgoId": order.get("clientAlgoId"),
    }


def live_client_from_config(config: dict[str, Any]) -> BinanceLiveClient:
    execution = config.get("execution", {})
    env_path = Path(execution.get("env_file", ""))
    env = load_env_file(env_path)
    api_key = env.get("BINANCE_API_KEY") or os.environ.get("BINANCE_API_KEY")
    api_secret = env.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit("Missing Binance API credentials")
    client = BinanceLiveClient(api_key, api_secret, BASE_URL)
    client.sync_time()
    return client


def inspect_live_protection_before_scan(
    config: dict[str, Any],
    events_path: Path,
    *,
    write_event: bool = True,
) -> dict[str, Any]:
    client = live_client_from_config(config)
    hedge_mode = client.hedge_mode_enabled()
    positions = active_positions(client.position_risk())
    all_open_orders = client.open_all_algo_orders()
    stale_orders = stale_protective_orders(all_open_orders, positions, hedge_mode)
    account: dict[str, Any] | None = None
    account_error: str | None = None
    try:
        account = account_snapshot(client.account())
    except Exception as exc:
        account_error = f"{type(exc).__name__}: {str(exc)[:220]}"
    result = {
        "time": now_cst(),
        "type": "live_position_precheck",
        "active_positions": len(positions),
        "positions": [position_snapshot(position) for position in positions],
        "stale_orders": [stale_order_summary(order) for order in stale_orders],
    }
    if account is not None:
        result["account"] = account
    if account_error:
        result["account_error"] = account_error
    if write_event:
        append_jsonl(events_path, result)
    return result


def confirmation_text(plans: list[dict[str, Any]], stale_orders: list[dict[str, Any]] | None = None) -> str:
    lines = ["ADJUST REAL BINANCE USD-M PROTECTION"]
    if stale_orders:
        lines += ["", "Cancel stale protective orders without active matching position:"]
        for order in stale_orders:
            lines.append(
                f"{order.get('symbol')} {order_type(order)} {order.get('positionSide')} "
                f"qty={order.get('quantity')} trigger={order.get('triggerPrice')}"
            )
    for plan in plans:
        lines += [
            "",
            f"{plan['symbol']} {plan['direction']} qty={plan['quantity']}",
            f"mark={plan['mark_price']}",
            f"stop: {plan['current_stop']} -> {plan['new_stop']}",
            f"take: {plan['current_take_profit']} -> {plan['new_take_profit']}",
        ]
    lines.append("")
    lines.append("Confirm to cancel old protective orders and place new stop/take-profit orders?")
    return "\n".join(lines)


def place_protective_order(
    client: BinanceLiveClient,
    plan: dict[str, Any],
    order_type_name: str,
    trigger: str,
    hedge_mode: bool,
) -> Any:
    close_side = "SELL" if plan["direction"] == "LONG" else "BUY"
    intent = {"direction": plan["direction"]}
    return client.new_algo_order(
        add_position_mode_params(
            {
                "symbol": plan["symbol"],
                "side": close_side,
                "type": order_type_name,
                "algoType": "CONDITIONAL",
                "quantity": plan["quantity"],
                "triggerPrice": trigger,
                "workingType": "MARK_PRICE",
                "newClientAlgoId": f"av_{plan['symbol'].lower()}_{order_type_name.lower()}_{uuid.uuid4().hex[:8]}",
            },
            intent,
            hedge_mode,
            reduce_only=True,
        )
    )


def execute_adjustments(
    client: BinanceLiveClient,
    plans: list[dict[str, Any]],
    stale_orders: list[dict[str, Any]],
    hedge_mode: bool,
    log_path: Path,
    dry_run: bool = False,
    concurrency: int = 1,
    journal_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "time": now_cst(),
        "mode": "dry_run" if dry_run else "live",
        "plans": plans,
        "stale_orders": [stale_order_summary(order) for order in stale_orders],
        "executions": [],
        "cancelled_stale_orders": [],
        "operation_journal": [],
    }
    if dry_run:
        append_jsonl(log_path, result)
        return result
    assert_operation_journal_ready(journal_config)

    def journal_operation(
        operation_type: str,
        operation_status: str,
        *,
        symbol: str | None,
        direction: str | None = None,
        quantity: Any = None,
        order_type_name: str | None = None,
        trigger_price: Any = None,
        operation_reason: str,
        plan: dict[str, Any] | None = None,
        exchange_response: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "operation_type": operation_type,
            "operation_status": operation_status,
            "mode": "live",
            "symbol": symbol,
            "direction": direction,
            "quantity": quantity,
            "order_type": order_type_name,
            "trigger_price": trigger_price,
            "operation_reason": operation_reason,
            "strategy": (plan or {}).get("primary_strategy"),
            "score": (plan or {}).get("score"),
            "exit_signal": (plan or {}).get("exit_signal"),
            "exit_model": (plan or {}).get("exit_model"),
            "exit_profile": (plan or {}).get("exit_profile"),
            "exchange_response": exchange_response,
            "error": error,
        }
        try:
            journal_status = append_operation_record(journal_config, record)
        except Exception as exc:
            if operation_journal_required(journal_config):
                raise
            journal_status = {
                "enabled": True,
                "mongo_status": "error",
                "journal_error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
        result["operation_journal"].append(
            {
                "operation_type": operation_type,
                "operation_status": operation_status,
                "symbol": symbol,
                "journal_status": journal_status,
            }
        )
        return journal_status

    def cancel_stale(order: dict[str, Any]) -> Any:
        reason = stale_order_reason(stale_order_summary(order))
        cancel_result = client.cancel_algo_order(order)
        journal_status = journal_operation(
            "protective_order_cancel_stale",
            "submitted",
            symbol=order.get("symbol"),
            direction=order.get("positionSide"),
            quantity=order.get("quantity"),
            order_type_name=order_type(order),
            trigger_price=order.get("triggerPrice") or order.get("stopPrice"),
            operation_reason=reason,
            exchange_response=cancel_result,
        )
        return {
            "order": stale_order_summary(order),
            "result": cancel_result,
            "operation_reason": reason,
            "operation_journal": journal_status,
        }

    stale_worker_count = max(1, min(int(concurrency), len(stale_orders) or 1))
    if stale_worker_count == 1:
        for order in stale_orders:
            result["cancelled_stale_orders"].append(cancel_stale(order))
    else:
        with ThreadPoolExecutor(max_workers=stale_worker_count) as executor:
            futures = [executor.submit(cancel_stale, order) for order in stale_orders]
            for future in as_completed(futures):
                result["cancelled_stale_orders"].append(future.result())

    def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
        operation_reason = operation_reason_from_plan(plan)
        row = {"symbol": plan["symbol"], "cancelled": [], "placed": [], "operation_reason": operation_reason}
        mark = dec(plan["mark_price"])
        tick_size = dec(plan.get("tick_size", "0"))
        min_distance_ticks = int(plan.get("min_trigger_distance_ticks", 0))
        invalid_reasons = [
            reason
            for reason in (
                immediate_trigger_reason(
                    plan["direction"],
                    "STOP_MARKET",
                    dec(plan["new_stop"]),
                    mark,
                    tick_size,
                    min_distance_ticks,
                ),
                immediate_trigger_reason(
                    plan["direction"],
                    "TAKE_PROFIT_MARKET",
                    dec(plan["new_take_profit"]),
                    mark,
                    tick_size,
                    min_distance_ticks,
                ),
            )
            if reason
        ]
        if invalid_reasons:
            row["skipped"] = True
            row["skip_reason"] = "; ".join(invalid_reasons)
            row["operation_journal"] = journal_operation(
                "protective_order_adjustment_skipped",
                "skipped",
                symbol=plan["symbol"],
                direction=plan["direction"],
                quantity=plan["quantity"],
                operation_reason=f"{row['skip_reason']}; {operation_reason}",
                plan=plan,
            )
            return row
        for order in plan["cancel_orders"]:
            cancel_result = client.cancel_algo_order(order)
            journal_status = journal_operation(
                "protective_order_cancel",
                "submitted",
                symbol=plan["symbol"],
                direction=plan["direction"],
                quantity=plan["quantity"],
                order_type_name=order_type(order),
                trigger_price=order.get("triggerPrice") or order.get("stopPrice"),
                operation_reason=operation_reason,
                plan=plan,
                exchange_response=cancel_result,
            )
            row["cancelled"].append(
                {
                    "order": stale_order_summary(order),
                    "result": cancel_result,
                    "operation_reason": operation_reason,
                    "operation_journal": journal_status,
                }
            )
        stop_result = place_protective_order(client, plan, "STOP_MARKET", plan["new_stop"], hedge_mode)
        stop_journal = journal_operation(
            "protective_order_place",
            "submitted",
            symbol=plan["symbol"],
            direction=plan["direction"],
            quantity=plan["quantity"],
            order_type_name="STOP_MARKET",
            trigger_price=plan["new_stop"],
            operation_reason=operation_reason,
            plan=plan,
            exchange_response=stop_result,
        )
        row["placed"].append({"result": stop_result, "operation_reason": operation_reason, "operation_journal": stop_journal})
        take_result = place_protective_order(client, plan, "TAKE_PROFIT_MARKET", plan["new_take_profit"], hedge_mode)
        take_journal = journal_operation(
            "protective_order_place",
            "submitted",
            symbol=plan["symbol"],
            direction=plan["direction"],
            quantity=plan["quantity"],
            order_type_name="TAKE_PROFIT_MARKET",
            trigger_price=plan["new_take_profit"],
            operation_reason=operation_reason,
            plan=plan,
            exchange_response=take_result,
        )
        row["placed"].append({"result": take_result, "operation_reason": operation_reason, "operation_journal": take_journal})
        return row

    worker_count = max(1, min(int(concurrency), len(plans) or 1))
    if worker_count == 1:
        for plan in plans:
            result["executions"].append(execute_plan(plan))
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(execute_plan, plan) for plan in plans]
            for future in as_completed(futures):
                result["executions"].append(future.result())
    append_jsonl(log_path, result)
    return result


def adjust_live_protection_after_scan(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    events_path: Path,
    dry_run: bool = False,
    terminal_confirm: bool = False,
) -> dict[str, Any] | None:
    protection = config.get("protection", {})
    if not protection.get("enabled", False):
        return None
    execution = config.get("execution", {})
    client = live_client_from_config(config)
    hedge_mode = client.hedge_mode_enabled()
    positions = active_positions(client.position_risk())
    all_open_orders = client.open_all_algo_orders()
    stale_orders = stale_protective_orders(all_open_orders, positions, hedge_mode)
    if not positions and not stale_orders:
        return {"time": now_cst(), "type": "protection_adjustment_skipped", "reason": "no_active_positions", "active_positions": 0}
    position_snapshots = [position_snapshot(position) for position in positions]
    plans = build_adjustments(client, positions, rows, config, hedge_mode, all_open_orders)
    if not plans and not stale_orders:
        return {
            "time": now_cst(),
            "type": "protection_adjustment_skipped",
            "reason": "no_adjustment_needed",
            "active_positions": len(positions),
            "positions": position_snapshots,
        }

    # message = confirmation_text(plans, stale_orders)
    # confirmed = True if dry_run else (confirm_with_terminal(message) if terminal_confirm else confirm_with_gui(message))
    # if not confirmed:
    #     result = {
    #         "time": now_cst(),
    #         "type": "protection_adjustment_cancelled",
    #         "plans": plans,
    #         "stale_orders": [stale_order_summary(order) for order in stale_orders],
    #         "positions": position_snapshots,
    #     }
    #     append_jsonl(events_path, result)
    #     return result

    result = execute_adjustments(
        client,
        plans,
        stale_orders,
        hedge_mode,
        Path(execution.get("executions_path", "data/processed/live-execution/executions.jsonl")),
        dry_run=dry_run,
        concurrency=int(protection.get("execution_concurrency", protection.get("concurrency", 3))),
        journal_config=config,
    )
    result["active_positions"] = len(positions)
    result["positions"] = position_snapshots
    append_jsonl(events_path, {"time": now_cst(), "type": "protection_adjustment_result", "result": result})
    return result

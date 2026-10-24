import json
from datetime import datetime, timedelta

from quant.services import paper_signal_service as service_module
from quant.services.paper_signal_service import (
    CST,
    append_event_dedup,
    auth_like_live_precheck_error,
    choose_candidate,
    extract_binance_ban_until,
    extract_request_ip,
    live_entry_capacity,
    live_entry_skip_heartbeat_seconds,
    live_execution_backoff_blocks_market_scan,
    live_execution_failure_backoff_reason,
    live_precheck_backoff_event,
    live_precheck_backoff_retry_after,
    restore_live_execution_backoff_from_log,
    scan_sleep_seconds,
    set_live_execution_backoff,
    service_tick,
    service_loop_sleep_seconds,
    service_loop_stop_reason,
)


def test_scan_sleep_seconds_allows_zero_interval():
    assert scan_sleep_seconds({"service": {"scan_interval_seconds": 0, "boundary_scan_interval_seconds": 0}}) == 0


def test_service_loop_sleep_preserves_zero_for_non_confirm_live():
    config = {"service": {"scan_interval_seconds": 0, "boundary_scan_interval_seconds": 0}}

    assert service_loop_sleep_seconds(config, {}, confirm_live=False) == 0


def test_confirm_live_flat_account_gets_sleep_floor():
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_min_sleep_seconds": 30,
        }
    }

    assert service_loop_sleep_seconds(config, {}, confirm_live=True) == 30


def test_confirm_live_min_sleep_can_be_disabled():
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_min_sleep_seconds": 0,
        }
    }

    assert service_loop_sleep_seconds(config, {}, confirm_live=True) == 0


def test_confirm_live_active_position_gets_sleep_floor():
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_active_position_min_sleep_seconds": 30,
        }
    }
    state = {"last_live_positions": [{"symbol": "XAUTUSDT"}]}

    assert service_loop_sleep_seconds(config, state, confirm_live=True) == 30


def test_confirm_live_backoff_sleep_caps_at_retry_after():
    now = datetime(2026, 7, 2, 18, 0, tzinfo=CST)
    retry_after = (now + timedelta(seconds=12)).strftime("%Y-%m-%d %H:%M:%S %z")
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_backoff_min_sleep_seconds": 30,
        }
    }
    state = {"live_execution_backoff_until": retry_after}

    assert service_loop_sleep_seconds(config, state, confirm_live=True, now=now) == 12


def test_confirm_live_backoff_sleep_rounds_subsecond_retry_after_up():
    now = datetime(2026, 7, 2, 18, 0, 0, 500000, tzinfo=CST)
    retry_after = "2026-07-02 18:00:01 +0800"
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_backoff_min_sleep_seconds": 30,
        }
    }
    state = {"live_execution_backoff_until": retry_after}

    assert service_loop_sleep_seconds(config, state, confirm_live=True, now=now) == 1


def test_confirm_live_sleep_uses_market_scan_backoff_retry_after():
    now = datetime(2026, 7, 22, 4, 10, tzinfo=CST)
    retry_after = (now + timedelta(seconds=12)).strftime("%Y-%m-%d %H:%M:%S %z")
    config = {
        "service": {
            "scan_interval_seconds": 0,
            "boundary_scan_interval_seconds": 0,
            "confirm_live_backoff_min_sleep_seconds": 30,
        }
    }
    state = {"market_scan_backoff_until": retry_after}

    assert service_loop_sleep_seconds(config, state, confirm_live=True, now=now) == 12


def test_service_loop_stop_reason_prefers_once_after_tick():
    assert (
        service_loop_stop_reason(
            once=True,
            shutdown_requested=True,
            iterations=10,
            completed=1,
        )
        == "once"
    )


def test_service_loop_stop_reason_reports_signal_and_iteration_limits():
    assert (
        service_loop_stop_reason(
            once=False,
            shutdown_requested=True,
            iterations=0,
            completed=5,
        )
        == "signal_requested"
    )
    assert (
        service_loop_stop_reason(
            once=False,
            shutdown_requested=False,
            iterations=3,
            completed=3,
        )
        == "iterations_completed"
    )
    assert (
        service_loop_stop_reason(
            once=False,
            shutdown_requested=False,
            iterations=0,
            completed=99,
        )
        is None
    )


def test_live_entry_skip_heartbeat_seconds_defaults_and_clamps():
    assert live_entry_skip_heartbeat_seconds({}) == 300
    assert live_entry_skip_heartbeat_seconds({"service": {"live_entry_skip_heartbeat_seconds": 60}}) == 60
    assert live_entry_skip_heartbeat_seconds({"service": {"live_entry_skip_heartbeat_seconds": -1}}) == 0


def test_choose_candidate_skips_active_and_backoff_symbols():
    config = {
        "strategy": {
            "allow_review_candidates": False,
            "min_score": 70,
            "manual_blocklist": [],
            "directions": ["LONG", "SHORT"],
        }
    }
    rows = [
        {"symbol": "WLFIUSDT", "action": "WATCH_LONG", "direction": "LONG", "score": 99},
        {"symbol": "SKYAIUSDT", "action": "WATCH_SHORT", "direction": "SHORT", "score": 98},
        {"symbol": "WLDUSDT", "action": "WATCH_SHORT", "direction": "SHORT", "score": 97},
    ]

    candidate = choose_candidate(rows, config, excluded_symbols={"WLFIUSDT", "SKYAIUSDT"})

    assert candidate is not None
    assert candidate["symbol"] == "WLDUSDT"


def test_choose_candidate_rejects_market_structure_review_even_when_reviews_allowed():
    config = {
        "strategy": {
            "allow_review_candidates": True,
            "min_score": 70,
            "manual_blocklist": [],
            "directions": ["LONG", "SHORT"],
        }
    }
    rows = [
        {
            "symbol": "PENDLEUSDT",
            "action": "REVIEW_WATCH_LONG",
            "direction": "LONG",
            "score": 92,
            "risk_flags": "volume_price_divergence,order_book_ask_pressure",
        },
        {
            "symbol": "INJUSDT",
            "action": "WATCH_LONG",
            "direction": "LONG",
            "score": 74,
            "risk_flags": "",
        },
    ]

    candidate = choose_candidate(rows, config)

    assert candidate is not None
    assert candidate["symbol"] == "INJUSDT"


def test_choose_candidate_blocks_symbols_with_loss_history_reasons():
    config = {
        "strategy": {
            "allow_review_candidates": False,
            "min_score": 70,
            "manual_blocklist": [],
            "directions": ["LONG", "SHORT"],
        }
    }
    rows = [
        {
            "symbol": "XPLUSDT",
            "action": "WATCH_LONG",
            "direction": "LONG",
            "score": 99,
            "reasons": "15m_ema_bull,past_loser",
            "risk_flags": "",
        },
        {
            "symbol": "FETUSDT",
            "action": "WATCH_SHORT",
            "direction": "SHORT",
            "score": 98,
            "reasons": "15m_ema_bear,high_freq_loss",
            "risk_flags": "",
        },
        {
            "symbol": "TAIKOUSDT",
            "action": "WATCH_LONG",
            "direction": "LONG",
            "score": 88,
            "reasons": "15m_ema_bull,past_winner",
            "risk_flags": "",
        },
    ]

    candidate = choose_candidate(rows, config)

    assert candidate is not None
    assert candidate["symbol"] == "TAIKOUSDT"


def test_confirmation_timeout_triggers_execution_backoff():
    assert live_execution_failure_backoff_reason({"status": "confirmation_timeout"}) == "confirmation_timeout"


def test_user_cancelled_execution_backs_off_symbol_only():
    assert live_execution_failure_backoff_reason({"status": "cancelled_by_user"}) == "cancelled_by_user"
    assert not live_execution_backoff_blocks_market_scan({"reason": "cancelled_by_user"})


def test_capacity_backoffs_block_market_scan():
    assert live_execution_backoff_blocks_market_scan({"reason": "confirmation_timeout"})
    assert live_execution_backoff_blocks_market_scan({"reason": "exchange_rate_limited"})
    assert live_execution_backoff_blocks_market_scan({"reason": "margin_insufficient"})
    assert live_execution_backoff_blocks_market_scan({"reason": "position_capacity_limit"})


def test_requests_418_text_triggers_rate_limit_backoff_classification():
    error = "RuntimeError: /fapi/v1/exchangeInfo failed after retries: HTTPError: 418 Client Error"

    assert auth_like_live_precheck_error(error)
    assert (
        live_execution_failure_backoff_reason({"status": "execution_error", "error": error})
        == "exchange_rate_limited"
    )


def test_binance_ban_until_extends_live_precheck_backoff():
    base_time = datetime(2026, 7, 5, 4, 8, 48, tzinfo=CST)
    error = (
        'RuntimeError: /fapi/v1/positionSide/dual HTTP 418: {"code":-1003,'
        '"msg":"Way too many requests; IP(203.0.113.10) banned until 1783198773795. '
        'Please use the websocket for live updates to avoid bans."}'
    )
    config = {
        "service": {
            "live_precheck_auth_backoff_seconds": 30,
            "binance_rate_limit_ban_grace_seconds": 15,
        }
    }

    retry_after = live_precheck_backoff_retry_after(config, error, base_time=base_time)

    assert extract_request_ip(error) == "203.0.113.10"
    assert extract_binance_ban_until(error).strftime("%Y-%m-%d %H:%M:%S %z") == "2026-07-05 04:59:33 +0800"
    assert retry_after.strftime("%Y-%m-%d %H:%M:%S %z") == "2026-07-05 04:59:48 +0800"


def test_exchange_rate_limited_execution_backoff_uses_binance_ban_until():
    base_time = datetime(2026, 7, 5, 4, 8, 48, tzinfo=CST)
    state = {}
    result = {
        "status": "execution_error",
        "symbol": "BTCUSDT",
        "error": (
            '/fapi/v1/order HTTP 418: {"code":-1003,'
            '"msg":"Way too many requests; IP(203.0.113.10) banned until 1783198773795."}'
        ),
    }
    config = {
        "service": {
            "live_execution_backoff_seconds": 30,
            "binance_rate_limit_ban_grace_seconds": 15,
        }
    }

    set_live_execution_backoff(state, result, config, base_time=base_time)

    assert state["live_execution_backoff_reason"] == "exchange_rate_limited"
    assert state["live_execution_backoff_until"] == "2026-07-05 04:59:48 +0800"


def test_margin_insufficient_uses_longer_live_backoff():
    base_time = datetime(2026, 7, 2, 18, 0, tzinfo=CST)
    state = {}
    result = {
        "status": "entry_rejected_no_position_opened",
        "symbol": "XMRUSDT",
        "error": '/fapi/v1/order HTTP 400: {"code":-2019,"msg":"Margin is insufficient."}',
    }
    config = {
        "service": {
            "live_execution_backoff_seconds": 900,
            "live_margin_insufficient_backoff_seconds": 3600,
        }
    }

    set_live_execution_backoff(state, result, config, base_time=base_time)

    assert state["live_execution_backoff_reason"] == "margin_insufficient"
    assert state["live_execution_backoff_until"] == "2026-07-02 19:00:00 +0800"


def test_live_entry_capacity_caps_equity_from_available_balance():
    config = {
        "risk": {
            "max_notional_pct": 100,
            "live_entry_available_balance_buffer_pct": 70,
            "live_entry_leverage": 20,
            "live_entry_min_available_balance_usdt": 5,
            "live_entry_min_notional_usdt": 20,
        }
    }
    precheck = {"account": {"availableBalance": "30"}}

    capacity = live_entry_capacity(config, precheck, base_equity=1000)

    assert not capacity["should_skip"]
    assert capacity["effective_equity"] == 420.0
    assert capacity["max_entry_notional"] == 420.0


def test_live_entry_capacity_skips_when_available_balance_is_too_low():
    config = {
        "risk": {
            "max_notional_pct": 100,
            "live_entry_available_balance_buffer_pct": 70,
            "live_entry_leverage": 20,
            "live_entry_min_available_balance_usdt": 5,
            "live_entry_min_notional_usdt": 20,
        }
    }
    precheck = {"account": {"availableBalance": "2"}}

    capacity = live_entry_capacity(config, precheck, base_equity=1000)

    assert capacity["should_skip"]
    assert capacity["reason"] == "insufficient_available_balance"


def test_live_entry_capacity_skips_when_available_balance_is_unavailable():
    config = {
        "risk": {
            "max_notional_pct": 100,
            "live_entry_available_balance_buffer_pct": 70,
            "live_entry_leverage": 20,
            "live_entry_min_available_balance_usdt": 5,
            "live_entry_min_notional_usdt": 20,
        }
    }
    precheck = {"account_error": "RuntimeError: account unavailable"}

    capacity = live_entry_capacity(config, precheck, base_equity=1000)

    assert capacity["should_skip"]
    assert capacity["reason"] == "account_available_balance_unavailable"
    assert capacity["max_entry_notional"] is None


def test_restore_extends_existing_margin_backoff_with_new_config(tmp_path):
    now = datetime.now(CST).replace(microsecond=0)
    execution_time = now - timedelta(minutes=5)
    executions_path = tmp_path / "executions.jsonl"
    executions_path.write_text(
        (
            '{"time": "%s", "status": "entry_rejected_no_position_opened", '
            '"symbol": "LITUSDT", "error": "/fapi/v1/order HTTP 400: '
            '{\\"code\\":-2019,\\"msg\\":\\"Margin is insufficient.\\"}"}\n'
        )
        % execution_time.strftime("%Y-%m-%d %H:%M:%S %z")
    )
    state = {
        "live_execution_backoff_until": (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S %z"),
        "live_execution_backoff_reason": "margin_insufficient",
    }
    config = {
        "service": {
            "live_execution_backoff_seconds": 900,
            "live_margin_insufficient_backoff_seconds": 3600,
        }
    }

    restore_live_execution_backoff_from_log(state, executions_path, config)

    assert state["live_execution_backoff_until"] == (execution_time + timedelta(seconds=3600)).strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )
    assert state["live_execution_backoff_symbol"] == "LITUSDT"


def test_live_precheck_backoff_event_marks_unknown_position_count():
    retry_after = (datetime.now(CST) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S %z")
    state = {
        "live_precheck_backoff_until": retry_after,
        "live_precheck_backoff_error": "RuntimeError: precheck failed",
        "live_precheck_backoff_active_positions": 0,
        "live_precheck_backoff_active_positions_unknown": True,
    }

    event = live_precheck_backoff_event(state, {"service": {}}, max_live_positions=3)

    assert event is not None
    assert event["active_positions"] == 0
    assert event["positions"] == []
    assert event["active_positions_unknown"] is True


def test_service_tick_precheck_error_does_not_report_full_slots(tmp_path, monkeypatch):
    from quant.services import protection_manager

    def fail_precheck(*_args, **_kwargs):
        raise RuntimeError(
            '/fapi/v1/positionSide/dual HTTP 401: {"code":-2015,"msg":"Invalid API-key"}'
        )

    monkeypatch.setattr(protection_manager, "inspect_live_protection_before_scan", fail_precheck)
    events_path = tmp_path / "events.jsonl"
    intents_path = tmp_path / "intents.jsonl"
    config = {
        "risk": {
            "paper_equity_usdt": 1000,
            "max_live_positions": 3,
        },
        "execution": {
            "executions_path": str(tmp_path / "executions.jsonl"),
        },
        "service": {
            "live_precheck_auth_backoff_seconds": 30,
        },
        "scanner": {
            "klines_limit": 120,
            "history_path": str(tmp_path / "missing-history.csv"),
        },
    }

    state = service_tick(config, {}, events_path, intents_path, show_progress=False, live_confirm_only=True)
    events = [json.loads(line) for line in events_path.read_text().splitlines()]

    precheck_error = next(event for event in events if event["type"] == "live_position_precheck_error")
    skipped_entry = next(event for event in events if event["type"] == "live_entry_skipped")
    assert precheck_error["active_positions"] == 0
    assert precheck_error["active_positions_unknown"] is True
    assert skipped_entry["active_positions"] == 0
    assert skipped_entry["active_positions_unknown"] is True
    assert state["live_precheck_backoff_active_positions"] == 0
    assert state["live_precheck_backoff_active_positions_unknown"] is True


def test_service_tick_skips_protection_when_empty_precheck_has_no_stale_orders(tmp_path, monkeypatch):
    from quant.services import protection_manager

    def empty_precheck(*_args, **_kwargs):
        return {
            "time": "2026-08-02 04:12:09 +0800",
            "type": "live_position_precheck",
            "active_positions": 0,
            "positions": [],
            "stale_orders": [],
            "account": {"availableBalance": "4.50"},
        }

    def fail_adjustment(*_args, **_kwargs):
        raise AssertionError("empty precheck should not trigger protection adjustment")

    monkeypatch.setattr(protection_manager, "inspect_live_protection_before_scan", empty_precheck)
    monkeypatch.setattr(protection_manager, "adjust_live_protection_after_scan", fail_adjustment)
    events_path = tmp_path / "events.jsonl"
    intents_path = tmp_path / "intents.jsonl"
    config = {
        "risk": {
            "paper_equity_usdt": 1000,
            "max_live_positions": 3,
            "live_entry_min_available_balance_usdt": 5,
        },
        "protection": {
            "enabled": True,
        },
        "execution": {
            "executions_path": str(tmp_path / "executions.jsonl"),
        },
        "service": {
            "live_precheck_heartbeat_seconds": 0,
        },
        "scanner": {
            "klines_limit": 120,
            "history_path": str(tmp_path / "missing-history.csv"),
        },
    }

    state = service_tick(config, {}, events_path, intents_path, show_progress=False, live_confirm_only=True)
    events = [json.loads(line) for line in events_path.read_text().splitlines()]

    protection_skip = next(event for event in events if event["type"] == "protection_adjustment_skipped")
    entry_skip = next(event for event in events if event["type"] == "live_entry_skipped")
    assert protection_skip["reason"] == "no_active_positions"
    assert protection_skip["active_positions"] == 0
    assert entry_skip["reason"] == "insufficient_available_balance"
    assert state["last_protection_adjustment"]["reason"] == "no_active_positions"


def test_service_tick_records_market_scan_backoff_without_crashing(tmp_path, monkeypatch):
    def fail_scan_market(**_kwargs):
        raise RuntimeError("/fapi/v1/exchangeInfo failed after retries: HTTPError: 418 Client Error")

    monkeypatch.setattr(service_module, "scan_market", fail_scan_market)
    events_path = tmp_path / "events.jsonl"
    intents_path = tmp_path / "intents.jsonl"
    scan_audit_path = tmp_path / "scan_audit.jsonl"
    config = {
        "risk": {"paper_equity_usdt": 1000},
        "service": {
            "scan_audit_path": str(scan_audit_path),
            "live_execution_backoff_seconds": 30,
            "market_scan_rate_limit_backoff_seconds": 30,
        },
        "scanner": {
            "max_symbols": 80,
            "min_quote_volume": 0,
            "klines_limit": 120,
            "history_path": str(tmp_path / "missing-history.csv"),
        },
    }

    state = service_tick(config, {}, events_path, intents_path, show_progress=False)

    assert state["last_scan"]["symbols_scanned"] == 0
    assert state["last_candidates"] == []
    assert state["market_scan_backoff_reason"] == "exchange_rate_limited"
    event = json.loads(events_path.read_text().splitlines()[-1])
    assert event["type"] == "market_scan_error"
    assert event["reason"] == "exchange_rate_limited"
    audit = json.loads(scan_audit_path.read_text().splitlines()[-1])
    assert audit["symbols_scanned"] == 0
    assert audit["errors"][0]["symbol"] == "MARKET"


def test_append_event_dedup_skips_repeated_empty_precheck(tmp_path):
    events_path = tmp_path / "events.jsonl"
    state = {}
    event = {
        "time": "2026-07-02 08:00:00 +0800",
        "type": "live_position_precheck",
        "active_positions": 0,
        "positions": [],
        "stale_orders": [],
    }

    assert append_event_dedup(
        events_path,
        event,
        state,
        "last_live_position_precheck_signature",
        ("type", "active_positions", "positions", "stale_orders"),
    )
    assert not append_event_dedup(
        events_path,
        {**event, "time": "2026-07-02 08:00:07 +0800"},
        state,
        "last_live_position_precheck_signature",
        ("type", "active_positions", "positions", "stale_orders"),
    )
    assert events_path.read_text().count("live_position_precheck") == 1


def test_append_event_dedup_writes_heartbeat_after_interval(tmp_path):
    events_path = tmp_path / "events.jsonl"
    state = {}
    event = {
        "time": "2026-07-02 08:00:00 +0800",
        "type": "live_position_precheck",
        "active_positions": 0,
        "positions": [],
        "stale_orders": [],
    }
    fields = ("type", "active_positions", "positions", "stale_orders")

    assert append_event_dedup(
        events_path,
        event,
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )
    assert not append_event_dedup(
        events_path,
        {**event, "time": "2026-07-02 08:04:59 +0800"},
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )
    assert append_event_dedup(
        events_path,
        {**event, "time": "2026-07-02 08:05:00 +0800"},
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )
    assert events_path.read_text().count("live_position_precheck") == 2


def test_append_event_dedup_ignores_mark_only_position_changes(tmp_path):
    events_path = tmp_path / "events.jsonl"
    state = {}
    fields = ("type", "active_positions", "stable_positions", "stale_orders")
    event = {
        "time": "2026-07-03 00:00:00 +0800",
        "type": "live_position_precheck",
        "active_positions": 1,
        "positions": [
            {
                "symbol": "BCHUSDT",
                "positionSide": "LONG",
                "positionAmt": "4.601",
                "entryPrice": "217.02883938274286",
                "breakEvenPrice": "217.13735380243423",
                "markPrice": "218.67050468",
                "unRealizedProfit": "7.55330204",
                "liquidationPrice": "205.13528891",
                "leverage": "20",
                "marginType": "cross",
            }
        ],
        "stale_orders": [],
    }

    assert append_event_dedup(
        events_path,
        event,
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )
    assert not append_event_dedup(
        events_path,
        {
            **event,
            "time": "2026-07-03 00:00:45 +0800",
            "positions": [
                {
                    **event["positions"][0],
                    "markPrice": "218.91000000",
                    "unRealizedProfit": "8.65522001",
                    "liquidationPrice": "205.13446759",
                }
            ],
        },
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )

    assert events_path.read_text().count("live_position_precheck") == 1


def test_append_event_dedup_writes_position_size_change(tmp_path):
    events_path = tmp_path / "events.jsonl"
    state = {}
    fields = ("type", "active_positions", "stable_positions", "stale_orders")
    event = {
        "time": "2026-07-03 00:00:00 +0800",
        "type": "live_position_precheck",
        "active_positions": 1,
        "positions": [
            {
                "symbol": "BCHUSDT",
                "positionSide": "LONG",
                "positionAmt": "4.601",
                "entryPrice": "217.02883938274286",
                "breakEvenPrice": "217.13735380243423",
                "markPrice": "218.67050468",
                "unRealizedProfit": "7.55330204",
                "leverage": "20",
                "marginType": "cross",
            }
        ],
        "stale_orders": [],
    }

    assert append_event_dedup(
        events_path,
        event,
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )
    assert append_event_dedup(
        events_path,
        {
            **event,
            "time": "2026-07-03 00:00:45 +0800",
            "positions": [{**event["positions"][0], "positionAmt": "3.101"}],
        },
        state,
        "last_live_position_precheck_signature",
        fields,
        heartbeat_seconds=300,
    )

    assert events_path.read_text().count("live_position_precheck") == 2

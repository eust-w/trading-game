from __future__ import annotations

import json
from datetime import datetime

import pytest

from quant.services.operation_journal import (
    CST,
    OperationJournalError,
    append_operation_record,
    operation_reason_from_intent,
    operation_reason_from_plan,
)


def test_append_operation_record_writes_daily_local_without_mongo_uri(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_VAULT_MONGODB_URI", raising=False)
    config = {
        "enabled": True,
        "required": False,
        "local_daily_path": str(tmp_path / "{date}-operations.jsonl"),
    }

    status = append_operation_record(
        config,
        {"operation_type": "position_entry", "symbol": "BTCUSDT", "operation_reason": "test reason"},
        now=datetime(2026, 7, 2, 1, 2, 3, tzinfo=CST),
    )

    assert status["local_status"] == "written"
    assert status["mongo_status"] == "disabled_no_uri"
    written = tmp_path / "2026-07-02-operations.jsonl"
    row = json.loads(written.read_text().strip())
    assert row["symbol"] == "BTCUSDT"
    assert row["operation_type"] == "position_entry"
    assert row["operation_id"]
    assert row["time"] == "2026-07-02 01:02:03 +0800"


def test_required_mongo_uri_raises_without_printing_secret_name(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_VAULT_MONGODB_URI", raising=False)
    config = {
        "enabled": True,
        "required": True,
        "local_daily_path": str(tmp_path / "{date}-operations.jsonl"),
    }

    with pytest.raises(OperationJournalError, match="ALPHA_VAULT_MONGODB_URI"):
        append_operation_record(
            config,
            {"operation_type": "position_entry", "symbol": "ETHUSDT"},
            now=datetime(2026, 7, 2, 1, 2, 3, tzinfo=CST),
        )


def test_operation_reason_from_intent_includes_strategy_risk_and_protection():
    intent = {
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "score": 91,
        "reasons": "15m_ema_bull,1h_trend_up",
        "risk_flags": "weak_recent_volume",
        "entry_order": {"quantity": 0.2, "notional": 600},
        "risk_orders": [
            {"type": "STOP_MARKET", "stop_price": 2950},
            {"type": "TAKE_PROFIT_MARKET", "stop_price": 3120},
        ],
        "market_snapshot": {"primary_strategy": "pullback"},
    }

    reason = operation_reason_from_intent(intent)

    assert "ETHUSDT LONG" in reason
    assert "strategy=pullback" in reason
    assert "score=91" in reason
    assert "risk_flags=weak_recent_volume" in reason
    assert "stop=2950" in reason
    assert "take_profit=3120" in reason


def test_operation_reason_from_plan_includes_volume_price_and_order_book_context():
    reason = operation_reason_from_plan(
        {
            "symbol": "PENDLEUSDT",
            "direction": "LONG",
            "primary_strategy": "trend_pullback",
            "score": 88,
            "current_stop": "1.5183",
            "new_stop": "1.5201",
            "current_take_profit": "1.5394",
            "new_take_profit": "1.5350",
            "exit_model": "adaptive_signal",
            "exit_profile": "trend_pullback",
            "exit_notes": "market_structure_defensive",
            "volume_price_regime": "volume_expansion_up_taker_neutral",
            "volume_price_score": -12,
            "order_book_imbalance": -0.0619,
            "order_book_spread_bps": 0.6545,
        }
    )

    assert "volume_price=volume_expansion_up_taker_neutral/-12" in reason
    assert "order_book=imbalance:-0.0619,spread_bps:0.6545" in reason

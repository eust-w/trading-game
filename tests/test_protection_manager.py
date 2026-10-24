from decimal import Decimal

from quant.services import protection_manager
from quant.services.protection_manager import immediate_trigger_reason, latest_order_update_ms


def test_long_stop_must_stay_below_mark_with_buffer():
    reason = immediate_trigger_reason(
        "LONG",
        "STOP_MARKET",
        Decimal("0.20010"),
        Decimal("0.19875"),
        Decimal("0.00001"),
        1,
    )

    assert "long stop" in reason


def test_long_take_profit_must_stay_above_mark_with_buffer():
    reason = immediate_trigger_reason(
        "LONG",
        "TAKE_PROFIT_MARKET",
        Decimal("0.19875"),
        Decimal("0.19875"),
        Decimal("0.00001"),
        1,
    )

    assert "long take-profit" in reason


def test_valid_short_protection_prices_pass():
    assert (
        immediate_trigger_reason(
            "SHORT",
            "STOP_MARKET",
            Decimal("10.15"),
            Decimal("10.00"),
            Decimal("0.01"),
            1,
        )
        is None
    )


def test_latest_order_update_ms_uses_newest_create_or_update_time():
    assert latest_order_update_ms(
        [
            {"createTime": "1000", "updateTime": "2000"},
            {"createTime": "3000", "updateTime": "0"},
            {"createTime": "", "updateTime": None},
        ]
    ) == 3000
    assert (
        immediate_trigger_reason(
            "SHORT",
            "TAKE_PROFIT_MARKET",
            Decimal("9.85"),
            Decimal("10.00"),
            Decimal("0.01"),
            1,
        )
        is None
    )


def test_inspect_live_protection_can_skip_direct_event_write(tmp_path, monkeypatch):
    class FakeClient:
        def hedge_mode_enabled(self):
            return False

        def position_risk(self):
            return []

        def open_all_algo_orders(self):
            return []

    monkeypatch.setattr(protection_manager, "live_client_from_config", lambda config: FakeClient())
    events_path = tmp_path / "events.jsonl"

    result = protection_manager.inspect_live_protection_before_scan({}, events_path, write_event=False)

    assert result["type"] == "live_position_precheck"
    assert result["active_positions"] == 0
    assert not events_path.exists()

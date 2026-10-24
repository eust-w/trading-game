from datetime import datetime, timedelta, timezone

from scripts.hourly_quant_review import (
    current_live_position_lines,
    feed_staleness_anomaly_notes,
    feed_staleness_lesson_notes,
    lesson_notes,
    live_event_notes,
    operation_reason_review_lines,
    pending_live_intent_lines,
    render_skip_reason_summary,
    runtime_guard_stale_feed_note,
    summarize_candidate,
    summarize_live_event,
    summarize_protection_event,
)


CST = timezone(timedelta(hours=8))


def test_pending_live_intent_lines_reports_unexecuted_entry_intent():
    intents = [
        {
            "id": "intent-1",
            "time": "2026-07-02 10:01:48 +0800",
            "intent_type": "entry",
            "symbol": "SUIUSDT",
            "direction": "LONG",
            "score": 94,
            "risk_orders": [
                {"stop_price": 0.71892405},
                {"stop_price": 0.72759885},
            ],
        }
    ]

    lines = pending_live_intent_lines(intents, all_executions=[])

    assert "Pending live intent" in lines[0]
    assert "`SUIUSDT` LONG" in lines[1]


def test_pending_live_intent_lines_skips_executed_intent():
    intents = [{"id": "intent-1", "intent_type": "entry", "symbol": "SUIUSDT"}]
    executions = [{"intent_id": "intent-1", "status": "confirmation_timeout"}]

    assert pending_live_intent_lines(intents, executions) == []


def test_summaries_surface_unknown_active_position_counts():
    live_line = summarize_live_event(
        {
            "time": "2026-07-23 04:08:44 +0800",
            "type": "live_position_precheck_error",
            "active_positions": 0,
            "positions": [],
            "active_positions_unknown": True,
            "error": "RuntimeError: precheck failed",
        }
    )
    protection_line = summarize_protection_event(
        {
            "time": "2026-07-23 04:08:44 +0800",
            "type": "protection_adjustment_skipped",
            "reason": "live_position_precheck_error",
            "active_positions": 0,
            "symbols": [],
            "active_positions_unknown": True,
        }
    )

    assert "active_positions_unknown=true" in live_line
    assert "active_positions_unknown=true" in protection_line


def test_current_live_position_lines_uses_latest_empty_successful_precheck():
    lines = current_live_position_lines(
        live_events=[
            {
                "time": "2026-07-02 16:00:34 +0800",
                "type": "live_position_precheck",
                "active_positions": 1,
                "positions": [{"symbol": "FILUSDT"}],
            },
            {
                "time": "2026-07-02 16:01:51 +0800",
                "type": "live_position_precheck",
                "active_positions": 0,
                "positions": [],
                "account": {
                    "availableBalance": "4.50",
                    "totalWalletBalance": "4.50",
                    "totalMarginBalance": "4.50",
                    "totalUnrealizedProfit": "0.00",
                },
            },
        ],
        all_executions=[],
    )

    assert lines == [
        "- Snapshot source: successful live precheck at 2026-07-02 16:01:51 +0800.",
        "- Account balance snapshot: available=4.50 wallet=4.50 margin=4.50 uPnL=0.00 USDT.",
        "- No active live positions are open in the latest exchange snapshot.",
    ]


def test_current_live_position_lines_marks_snapshot_stale_after_later_precheck_failure():
    lines = current_live_position_lines(
        live_events=[
            {
                "time": "2026-08-23 03:59:59 +0800",
                "type": "live_position_precheck",
                "active_positions": 0,
                "positions": [],
                "account": {"availableBalance": "4.59"},
            },
            {
                "time": "2026-08-23 04:01:02 +0800",
                "type": "live_position_precheck_error",
                "active_positions": 0,
                "positions": [],
                "active_positions_unknown": True,
            },
        ],
        all_executions=[],
    )

    assert lines == [
        "- Snapshot source: last successful live precheck at 2026-08-23 03:59:59 +0800; a later precheck failed at 2026-08-23 04:01:02 +0800, so current exchange position and protection state is unknown.",
        "- Account balance snapshot (last known): available=4.59 USDT.",
        "- Last known snapshot had no active live positions; this is not confirmation that the account is currently flat.",
    ]


def test_current_live_position_lines_reports_missing_snapshots_distinctly():
    assert current_live_position_lines([], all_executions=[]) == [
        "- No live position snapshots were recorded in this window."
    ]


def test_lesson_notes_keeps_margin_backoff_distinct_from_full_slots():
    notes = lesson_notes(
        scans=[],
        protection_events=[],
        live_events=[
            {
                "type": "live_entry_skipped",
                "reason": "margin_insufficient",
                "skipped_full_market_scan": True,
            }
        ],
    )

    rendered = "\n".join(notes)
    assert "margin_insufficient" in rendered
    assert "live slots are full" not in rendered


def test_lesson_notes_reports_full_slots_when_position_cap_reached():
    notes = lesson_notes(
        scans=[],
        protection_events=[],
        live_events=[
            {
                "type": "live_entry_skipped",
                "reason": "max_live_positions_reached",
                "skipped_full_market_scan": True,
            }
        ],
    )

    assert any("live slots are full" in note for note in notes)


def test_summarize_candidate_includes_volume_price_and_order_book_context():
    rendered = summarize_candidate(
        {
            "symbol": "ETHUSDT",
            "action": "REVIEW_WATCH_LONG",
            "direction": "LONG",
            "score": 64,
            "primary_strategy": "trend_pullback",
            "last_price": 1702.19,
            "price_change_pct_24h": 5.157,
            "atr_pct_15m": 0.482,
            "rsi14_15m": 57.23,
            "funding_rate": 0.00007123,
            "volume_price_regime": "weak_volume_up_taker_neutral",
            "volume_price_score": -22,
            "order_book_imbalance": -0.6892,
            "order_book_spread_bps": 0.0588,
            "risk_flags": "volume_price_divergence,order_book_ask_pressure",
        }
    )

    assert "VP=weak_volume_up_taker_neutral/-22" in rendered
    assert "OB_imb=-0.6892 spread=0.0588bps" in rendered


def test_summarize_candidate_includes_order_book_error():
    rendered = summarize_candidate(
        {
            "symbol": "ETHUSDT",
            "action": "REVIEW_WATCH_LONG",
            "direction": "LONG",
            "score": 64,
            "primary_strategy": "trend_pullback",
            "last_price": 1702.19,
            "price_change_pct_24h": 5.157,
            "atr_pct_15m": 0.482,
            "rsi14_15m": 57.23,
            "funding_rate": 0.00007123,
            "volume_price_regime": "normal_volume_up_buy_dominant",
            "volume_price_score": 4,
            "order_book_error": "depth timeout",
            "risk_flags": "order_book_unavailable",
        }
    )

    assert "OB_error=depth timeout" in rendered


def test_feed_staleness_notes_treat_empty_window_as_producer_health():
    since = datetime(2026, 7, 17, 3, 0, tzinfo=CST)
    last_seen = {
        "scan": datetime(2026, 7, 8, 18, 39, tzinfo=CST),
        "event": datetime(2026, 7, 15, 6, 46, tzinfo=CST),
        "intent": datetime(2026, 7, 8, 18, 39, tzinfo=CST),
        "execution": datetime(2026, 7, 8, 19, 20, tzinfo=CST),
    }

    anomalies = feed_staleness_anomaly_notes(last_seen, since)
    lessons = feed_staleness_lesson_notes(last_seen, since)

    assert "Core data feeds are stale" in anomalies[0]
    assert "producer health" in anomalies[0]
    assert "restart or repair the producer" in lessons[0]


def test_feed_staleness_notes_respect_runtime_guard_explanations():
    since = datetime(2026, 7, 18, 3, 0, tzinfo=CST)
    last_seen = {
        "scan": datetime(2026, 7, 8, 18, 39, tzinfo=CST),
        "event": datetime(2026, 7, 18, 3, 45, tzinfo=CST),
        "intent": datetime(2026, 7, 8, 18, 39, tzinfo=CST),
        "execution": datetime(2026, 7, 8, 19, 20, tzinfo=CST),
    }

    explained = {"scan", "intent", "execution"}

    assert feed_staleness_anomaly_notes(last_seen, since, explained) == []
    assert feed_staleness_lesson_notes(last_seen, since, explained) == []


def test_render_skip_reason_summary_uses_insufficient_balance_label():
    summary = render_skip_reason_summary({"insufficient_available_balance": 3})

    assert summary == "3 times because available balance was below the live entry floor"


def test_render_skip_reason_summary_labels_missing_account_balance():
    summary = render_skip_reason_summary({"account_available_balance_unavailable": 2})

    assert summary == "2 times because account balance snapshot was unavailable"


def test_runtime_guard_stale_feed_note_explains_execution_idle_state():
    note = runtime_guard_stale_feed_note(
        "execution",
        datetime(2026, 7, 23, 17, 22, tzinfo=CST),
        datetime(2026, 7, 24, 3, 0, tzinfo=CST),
        datetime(2026, 7, 24, 4, 0, tzinfo=CST),
        {"insufficient_available_balance": 11},
        "11 times because available balance was below the live entry floor",
        False,
    )

    assert note is not None
    assert "Execution feed is idle under the same entry guards" in note
    assert "no new entry intents reached live execution" in note


def test_live_event_notes_reports_insufficient_balance_as_guarded_inactivity():
    notes = live_event_notes(
        [
            {
                "type": "live_entry_skipped",
                "reason": "insufficient_available_balance",
                "skipped_full_market_scan": True,
            }
        ]
    )

    rendered = "\n".join(notes)
    assert "available balance" in rendered
    assert "guarded inactivity" in rendered


def test_live_event_notes_counts_precheck_error_and_backoff_entry_skips():
    notes = live_event_notes(
        [
            {"type": "live_position_precheck_backoff"},
            {"type": "live_entry_skipped", "reason": "live_position_precheck_error"},
            {"type": "live_entry_skipped", "reason": "live_position_precheck_backoff"},
        ]
    )

    rendered = "\n".join(notes)
    assert "precheck entered backoff 1 times" in rendered
    assert "Live entry was skipped 2 times because the live position precheck failed or was in backoff" in rendered


def test_operation_reason_review_lines_keep_volume_price_and_order_book_visible():
    lines = operation_reason_review_lines(
        [
            {
                "time": "2026-07-03 04:22:29 +0800",
                "mode": "live",
                "plans": [
                    {
                        "symbol": "DYDXUSDT",
                        "direction": "SHORT",
                        "score": 34,
                        "current_stop": "0.1322",
                        "new_stop": "0.1322",
                        "current_take_profit": "0.1278",
                        "new_take_profit": "0.1285",
                        "operation_reason": "protective_order_maintenance: DYDXUSDT SHORT; "
                        "strategy=trend_following; score=34; stop=0.1322->0.1322; "
                        "take_profit=0.1278->0.1285; exit_model=adaptive_signal/trend_following; "
                        "take_tighten_allowed=true; exit_notes=trend_following,low_score_defensive,"
                        "risk_flag_defensive",
                        "volume_price_regime": "normal_volume_down_taker_neutral",
                        "volume_price_score": -10,
                        "order_book_imbalance": 0.121,
                        "order_book_spread_bps": 7.6894,
                    }
                ],
            }
        ]
    )

    rendered = "\n".join(lines)
    assert "VP=normal_volume_down_taker_neutral/-10" in rendered
    assert "OB_imb=0.121 spread=7.6894bps" in rendered


def test_lesson_notes_reports_volume_price_and_order_book_risk():
    notes = lesson_notes(
        scans=[
            {
                "top_candidates": [
                    {"risk_flags": "volume_price_divergence,order_book_ask_pressure"},
                ],
            }
        ],
        protection_events=[],
        live_events=[],
    )

    rendered = "\n".join(notes)
    assert "30-day volume-price profile" in rendered

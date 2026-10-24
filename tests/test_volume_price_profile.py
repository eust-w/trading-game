from quant.screeners.binance_usdm_15m_trend_screener import (
    apply_volume_price_context,
    order_book_metrics,
)
from quant.screeners import binance_usdm_15m_trend_screener as screener
from scripts.build_volume_price_profile import build_profile_from_candles


def test_build_profile_summarizes_volume_price_regimes():
    candles = []
    price = 100.0
    for index in range(80):
        volume = 1000.0 if index < 40 else 2200.0
        taker = volume * (0.62 if index >= 40 else 0.51)
        open_price = price
        close_price = price * (1.001 if index >= 40 else 1.0001)
        candles.append(
            {
                "open_time": index,
                "open": open_price,
                "high": max(open_price, close_price) * 1.001,
                "low": min(open_price, close_price) * 0.999,
                "close": close_price,
                "quote_volume": volume,
                "taker_buy_quote_volume": taker,
            }
        )
        price = close_price

    profile = build_profile_from_candles({"TESTUSDT": candles}, min_samples=3)
    symbol_regimes = profile["symbols"]["TESTUSDT"]["regimes"]

    assert any("volume_expansion_up_buy_dominant" in regime for regime in symbol_regimes)
    assert profile["global"]["regimes"]


def test_volume_price_divergence_for_high_score_weak_volume_long_is_reviewed():
    row = {
        "symbol": "TESTUSDT",
        "action": "WATCH_LONG",
        "direction": "LONG",
        "score": 82,
        "long_score": 82,
        "short_score": 20,
        "trend_15m_pct": 0.42,
        "trend_1h_pct": 0.65,
        "volume_ratio": 0.48,
        "micro_volume_ratio": 0.75,
        "taker_buy_ratio_15m": 0.51,
        "risk_flags": "",
        "reasons": "15m_ema_bull,1h_trend_up",
    }

    adjusted = apply_volume_price_context(row, {}, {})

    assert adjusted["action"] == "REVIEW_WATCH_LONG"
    assert "volume_price_divergence" in adjusted["risk_flags"]
    assert adjusted["volume_price_score"] < 0


def test_order_book_bid_support_confirms_long_context():
    row = {
        "symbol": "TESTUSDT",
        "action": "WATCH_LONG",
        "direction": "LONG",
        "score": 76,
        "long_score": 76,
        "short_score": 20,
        "trend_15m_pct": 0.25,
        "trend_1h_pct": 0.35,
        "volume_ratio": 1.6,
        "micro_volume_ratio": 1.4,
        "taker_buy_ratio_15m": 0.61,
        "risk_flags": "",
        "reasons": "volume_confirm,15m_taker_buy_dominant",
    }
    profile = {
        "symbols": {
            "TESTUSDT": {
                "regimes": {
                    "volume_expansion_up_buy_dominant": {
                        "samples": 20,
                        "long_win_rate_4h": 0.62,
                        "short_win_rate_4h": 0.38,
                        "forward_return_4h_mean": 0.42,
                    }
                }
            }
        },
        "global": {"regimes": {}},
    }
    depth = {
        "bids": [["99.9", "20"], ["99.8", "10"]],
        "asks": [["100.1", "6"], ["100.2", "4"]],
    }

    adjusted = apply_volume_price_context(row, profile, order_book_metrics(depth))

    assert adjusted["score"] > row["score"]
    assert "volume_price_confirmed" in adjusted["reasons"]
    assert "order_book_bid_support" in adjusted["reasons"]
    assert adjusted["volume_price_regime"] == "volume_expansion_up_buy_dominant"


def test_order_book_ask_wall_flags_long_risk():
    depth = {
        "bids": [["99.9", "4"], ["99.8", "4"]],
        "asks": [["100.1", "30"], ["100.2", "2"]],
    }

    metrics = order_book_metrics(depth)

    assert metrics["imbalance"] < 0
    assert metrics["ask_wall_ratio"] > 0.8


def test_order_book_unavailable_marks_candidate_for_review():
    row = {
        "symbol": "TESTUSDT",
        "action": "WATCH_LONG",
        "direction": "LONG",
        "score": 76,
        "long_score": 76,
        "short_score": 20,
        "trend_15m_pct": 0.1,
        "trend_1h_pct": 0.1,
        "volume_ratio": 1.4,
        "micro_volume_ratio": 1.2,
        "taker_buy_ratio_15m": 0.6,
        "risk_flags": "",
        "reasons": "volume_confirm",
    }

    adjusted = apply_volume_price_context(row, {}, {"error": "timeout"})

    assert adjusted["action"] == "REVIEW_WATCH_LONG"
    assert adjusted["order_book_error"] == "timeout"
    assert "order_book_unavailable" in adjusted["risk_flags"]


def test_scan_symbol_keeps_candidate_when_order_book_fetch_fails(monkeypatch):
    def kline_rows(count: int) -> list[list[object]]:
        rows = []
        price = 100.0
        for index in range(count):
            close = price * (1.0006 if index % 5 else 0.9998)
            quote_volume = 2200.0 if index >= count - 4 else 1000.0
            rows.append(
                [
                    index,
                    f"{price:.6f}",
                    f"{max(price, close) * 1.001:.6f}",
                    f"{min(price, close) * 0.999:.6f}",
                    f"{close:.6f}",
                    "1",
                    index,
                    f"{quote_volume:.6f}",
                    10,
                    "1",
                    f"{quote_volume * 0.64:.6f}",
                ]
            )
            price = close
        return rows

    def fake_http_get(path, params=None, **kwargs):
        assert path == "/fapi/v1/klines"
        return kline_rows(int((params or {}).get("limit") or 120))

    monkeypatch.setattr(screener, "http_get", fake_http_get)
    monkeypatch.setattr(
        screener,
        "trade_flow",
        lambda symbol: {
            "recent_trade_quote": 1000.0,
            "recent_trade_buy_ratio": 0.64,
            "recent_trade_imbalance": 0.25,
        },
    )
    monkeypatch.setattr(
        screener,
        "load_order_book_metrics",
        lambda symbol, limit=20, timeout=2.0: (_ for _ in ()).throw(RuntimeError("depth timeout")),
    )

    row = screener.scan_symbol(
        {"symbol": "TESTUSDT", "quote_volume": 1000000.0, "price_change_pct_24h": 1.0, "last_price": 101.0},
        klines_limit=120,
        history={},
        funding_rates={"TESTUSDT": 0.0},
        order_book_enabled=True,
    )

    assert row["symbol"] == "TESTUSDT"
    assert "order_book_unavailable" in row["risk_flags"]
    assert row["order_book_error"] == "depth timeout"

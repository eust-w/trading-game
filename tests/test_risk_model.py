from quant.services.risk_model import adaptive_exit_plan, adaptive_exit_prices, should_tighten_take_profit


def base_config():
    return {
        "risk": {
            "min_stop_pct": 0.35,
            "adaptive_stop_min_pct": 0.55,
            "adaptive_stop_max_pct": 1.65,
            "adaptive_take_profit_min_r": 0.8,
            "adaptive_take_profit_max_r": 1.7,
            "adaptive_take_profit_min_pct": 0.65,
        }
    }


def candidate(**overrides):
    row = {
        "direction": "LONG",
        "score": 108,
        "atr_pct_15m": 0.7,
        "primary_strategy": "trend_pullback",
        "strategy_tags": "trend_pullback,trend_following",
        "risk_flags": "",
        "price_vs_ema21_pct": 0.4,
        "micro_trend_1m_pct": 0.2,
        "recent_trade_imbalance": 0.18,
        "bb_width_pct": 1.9,
    }
    row.update(overrides)
    return row


def test_trend_pullback_uses_adaptive_exit_instead_of_fixed_2r_atr():
    plan = adaptive_exit_plan(candidate(), base_config())

    assert 0.7 <= plan["stop_pct"] <= 1.0
    assert plan["take_profit_r"] != 2.0
    assert 1.1 <= plan["take_profit_r"] <= 1.5
    assert plan["take_profit_pct"] < 1.5


def test_wide_atr_and_risk_flags_cap_stop_and_reduce_target():
    plan = adaptive_exit_plan(
        candidate(
            atr_pct_15m=2.4,
            score=76,
            risk_flags="atr_too_wide,extended_above_ema21",
            price_vs_ema21_pct=5.2,
            micro_trend_1m_pct=-0.2,
            recent_trade_imbalance=-0.2,
        ),
        base_config(),
    )

    assert plan["stop_pct"] <= 1.65
    assert plan["take_profit_r"] <= 1.0


def test_adaptive_exit_prices_keep_long_stop_below_and_take_above_reference():
    prices = adaptive_exit_prices(candidate(), base_config(), reference_price=100, direction="LONG")

    assert prices["stop_loss"] < 100
    assert prices["take_profit"] > 100
    assert 0.7 <= prices["plan"]["stop_pct"] <= 1.0


def test_weak_signal_allows_take_profit_tightening():
    row = candidate(score=88, risk_flags="weak_recent_volume")
    config = {
        **base_config(),
        "risk": {
            **base_config()["risk"],
            "adaptive_take_profit_tighten_enabled": True,
        },
    }

    assert should_tighten_take_profit(row, config)


def test_adverse_volume_price_and_order_book_make_exit_plan_defensive():
    neutral = adaptive_exit_plan(candidate(score=92), base_config())
    adverse = adaptive_exit_plan(
        candidate(
            score=92,
            volume_price_score=-18,
            volume_price_regime="weak_volume_up_taker_neutral",
            risk_flags="volume_price_divergence,order_book_ask_pressure",
            order_book_imbalance=-0.32,
            order_book_ask_wall_ratio=0.68,
        ),
        base_config(),
    )

    assert "market_structure_defensive" in adverse["notes"]
    assert adverse["stop_pct"] < neutral["stop_pct"]
    assert adverse["take_profit_r"] < neutral["take_profit_r"]


def test_confirmed_volume_price_and_order_book_allow_more_exit_room():
    neutral = adaptive_exit_plan(candidate(score=92), base_config())
    confirmed = adaptive_exit_plan(
        candidate(
            score=92,
            volume_price_score=12,
            volume_price_regime="volume_expansion_up_buy_dominant",
            reasons="volume_price_confirmed,order_book_bid_support",
            order_book_imbalance=0.31,
            order_book_bid_wall_ratio=0.7,
        ),
        base_config(),
    )

    assert "market_structure_confirmed" in confirmed["notes"]
    assert confirmed["take_profit_r"] > neutral["take_profit_r"]

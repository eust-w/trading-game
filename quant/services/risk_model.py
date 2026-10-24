"""Adaptive stop-loss and take-profit sizing for paper/live intents."""

from __future__ import annotations

from typing import Any


def _risk(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("risk", {})


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _tokens(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {item.strip() for item in str(value).split(",") if item.strip()}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


STRATEGY_PROFILES = {
    "trend_pullback": {"stop_atr": 1.25, "take_r": 1.25},
    "breakout_momentum": {"stop_atr": 1.08, "take_r": 1.10},
    "capital_flow": {"stop_atr": 1.10, "take_r": 1.05},
    "volatility_compression": {"stop_atr": 1.15, "take_r": 1.55},
    "trend_following": {"stop_atr": 1.40, "take_r": 1.45},
    "funding_contrarian": {"stop_atr": 1.10, "take_r": 1.15},
    "countertrend_mean_reversion": {"stop_atr": 0.90, "take_r": 0.90},
    "none": {"stop_atr": 1.20, "take_r": 1.15},
}


COMMON_MARKET_STRUCTURE_RISKS = {
    "volume_price_divergence",
    "volume_price_profile_adverse",
    "volume_price_churn_or_exhaustion",
    "order_book_wide_spread",
    "order_book_unavailable",
}


def strategy_profile(row: dict[str, Any]) -> str:
    risk_flags = _tokens(row.get("risk_flags"))
    if "mean_reversion_countertrend" in risk_flags:
        return "countertrend_mean_reversion"
    primary = str(row.get("primary_strategy") or "")
    if primary in STRATEGY_PROFILES:
        return primary
    tags = _tokens(row.get("strategy_tags"))
    for name in (
        "trend_pullback",
        "breakout_momentum",
        "capital_flow",
        "volatility_compression",
        "trend_following",
        "funding_contrarian",
    ):
        if name in tags:
            return name
    return "none"


def _direction(row: dict[str, Any], direction: str | None = None) -> str:
    raw = direction or row.get("direction") or "LONG"
    return "SHORT" if str(raw).upper() == "SHORT" else "LONG"


def _has_adverse_micro(row: dict[str, Any], direction: str) -> bool:
    micro_trend = _float(row.get("micro_trend_1m_pct"))
    imbalance = _float(row.get("recent_trade_imbalance"))
    if direction == "LONG":
        return micro_trend < -0.12 or imbalance < -0.12
    return micro_trend > 0.12 or imbalance > 0.12


def _has_adverse_market_structure(row: dict[str, Any], direction: str) -> bool:
    risk_flags = _tokens(row.get("risk_flags"))
    if risk_flags & COMMON_MARKET_STRUCTURE_RISKS:
        return True
    if direction == "LONG" and "order_book_ask_pressure" in risk_flags:
        return True
    if direction == "SHORT" and "order_book_bid_pressure" in risk_flags:
        return True
    volume_price_score = _float(row.get("volume_price_score"))
    order_book_imbalance = _float(row.get("order_book_imbalance"))
    if volume_price_score <= -8:
        return True
    if direction == "LONG":
        return order_book_imbalance <= -0.18 or _float(row.get("order_book_ask_wall_ratio")) >= 0.55
    return order_book_imbalance >= 0.18 or _float(row.get("order_book_bid_wall_ratio")) >= 0.55


def _has_confirmed_market_structure(row: dict[str, Any], direction: str) -> bool:
    if _has_adverse_market_structure(row, direction):
        return False
    reasons = _tokens(row.get("reasons"))
    volume_confirmed = bool(reasons & {"volume_price_confirmed", "volume_price_current_confirmed"})
    volume_price_score = _float(row.get("volume_price_score"))
    order_book_imbalance = _float(row.get("order_book_imbalance"))
    if direction == "LONG":
        book_confirmed = "order_book_bid_support" in reasons or order_book_imbalance >= 0.18
    else:
        book_confirmed = "order_book_ask_support" in reasons or order_book_imbalance <= -0.18
    return (volume_confirmed or volume_price_score >= 6) and book_confirmed


def _is_extended(row: dict[str, Any], direction: str) -> bool:
    risk_flags = _tokens(row.get("risk_flags"))
    price_vs_ema21 = _float(row.get("price_vs_ema21_pct"))
    if direction == "LONG":
        return "extended_above_ema21" in risk_flags or price_vs_ema21 > 4.0
    return "extended_below_ema21" in risk_flags or price_vs_ema21 < -4.0


def adaptive_exit_plan(row: dict[str, Any], config: dict[str, Any], direction: str | None = None) -> dict[str, Any]:
    risk = _risk(config)
    if str(risk.get("exit_model", "adaptive_signal")) in {"legacy", "legacy_r_multiple", "fixed_r"}:
        atr_floor = _float(risk.get("min_stop_pct"), 0.35)
        atr_pct = max(_float(row.get("atr_pct_15m")), atr_floor)
        stop_pct = max(atr_pct * _float(risk.get("stop_atr_multiple"), 2.5), atr_floor)
        take_r = _float(risk.get("take_profit_r_multiple"), 2.0)
        return {
            "model": "legacy_r_multiple",
            "profile": "legacy",
            "atr_pct": round(atr_pct, 6),
            "stop_pct": round(stop_pct, 6),
            "take_profit_r": round(take_r, 6),
            "take_profit_pct": round(stop_pct * take_r, 6),
            "notes": [],
        }

    direction_value = _direction(row, direction)
    risk_flags = _tokens(row.get("risk_flags"))
    profile_name = strategy_profile(row)
    profile = STRATEGY_PROFILES[profile_name]

    atr_floor = _float(risk.get("min_stop_pct"), 0.35)
    atr_pct = max(_float(row.get("atr_pct_15m")), atr_floor)
    stop_min = _float(risk.get("adaptive_stop_min_pct"), max(atr_floor, 0.55))
    stop_max = _float(risk.get("adaptive_stop_max_pct"), 1.65)
    take_min_r = _float(risk.get("adaptive_take_profit_min_r"), 0.8)
    take_max_r = _float(risk.get("adaptive_take_profit_max_r"), 1.7)
    take_min_pct = _float(risk.get("adaptive_take_profit_min_pct"), 0.65)

    stop_atr = float(profile["stop_atr"])
    take_r = float(profile["take_r"])
    notes: list[str] = [profile_name]

    score = _float(row.get("score"))
    if score >= 105:
        take_r += 0.12
        notes.append("high_score_target_boost")
    elif score < 75:
        stop_atr *= 0.90
        take_r -= 0.22
        notes.append("low_score_defensive")
    elif score < 90:
        take_r -= 0.12
        notes.append("moderate_score_defensive")

    if risk_flags:
        stop_atr *= 0.94
        take_r -= 0.12
        notes.append("risk_flag_defensive")
    if "atr_too_wide" in risk_flags:
        stop_atr *= 0.78
        take_r -= 0.25
        notes.append("wide_atr_cap")
    if "atr_too_low" in risk_flags:
        stop_atr *= 1.12
        take_r -= 0.08
        notes.append("low_atr_noise_buffer")
    if _is_extended(row, direction_value):
        stop_atr *= 0.88
        take_r -= 0.20
        notes.append("extension_defensive")
    if _has_adverse_micro(row, direction_value):
        stop_atr *= 0.90
        take_r -= 0.18
        notes.append("adverse_micro_defensive")
    if _has_adverse_market_structure(row, direction_value):
        stop_atr *= 0.86
        take_r -= 0.24
        notes.append("market_structure_defensive")
    elif _has_confirmed_market_structure(row, direction_value):
        stop_atr *= 1.04
        take_r += 0.12
        notes.append("market_structure_confirmed")
    if profile_name == "volatility_compression" and _float(row.get("bb_width_pct")) < 1.5:
        take_r += 0.10
        notes.append("compression_expansion_room")

    stop_pct = _clamp(atr_pct * stop_atr, stop_min, stop_max)
    take_r = _clamp(take_r, take_min_r, take_max_r)
    take_profit_pct = max(stop_pct * take_r, take_min_pct)
    effective_take_r = take_profit_pct / stop_pct if stop_pct else take_r

    return {
        "model": "adaptive_signal",
        "profile": profile_name,
        "atr_pct": round(atr_pct, 6),
        "stop_atr_multiple": round(stop_atr, 6),
        "stop_pct": round(stop_pct, 6),
        "take_profit_r": round(effective_take_r, 6),
        "take_profit_pct": round(take_profit_pct, 6),
        "notes": notes,
    }


def adaptive_exit_prices(
    row: dict[str, Any],
    config: dict[str, Any],
    reference_price: float,
    direction: str | None = None,
) -> dict[str, Any]:
    direction_value = _direction(row, direction)
    plan = adaptive_exit_plan(row, config, direction_value)
    stop_distance = reference_price * plan["stop_pct"] / 100
    take_distance = reference_price * plan["take_profit_pct"] / 100
    if direction_value == "LONG":
        stop_loss = reference_price - stop_distance
        take_profit = reference_price + take_distance
    else:
        stop_loss = reference_price + stop_distance
        take_profit = reference_price - take_distance
    return {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "plan": plan,
    }


def should_tighten_take_profit(row: dict[str, Any], config: dict[str, Any], direction: str | None = None) -> bool:
    risk = _risk(config)
    if not _bool(risk.get("adaptive_take_profit_tighten_enabled"), False):
        return False
    direction_value = _direction(row, direction)
    risk_flags = _tokens(row.get("risk_flags"))
    score = _float(row.get("score"))
    threshold = _float(risk.get("adaptive_take_profit_tighten_score_below"), 95.0)
    return (
        bool(risk_flags)
        or score < threshold
        or _has_adverse_micro(row, direction_value)
        or _has_adverse_market_structure(row, direction_value)
        or _is_extended(row, direction_value)
    )

#!/usr/bin/env python3
"""Read-only Binance USD-M 15m trend screener.

The screener is intentionally conservative:
- public market-data endpoints only
- no account reads
- no order placement
- no symbol is a trade recommendation without manual review
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import requests


BASE_URL = "https://fapi.binance.com"
CST = timezone(timedelta(hours=8))
DEFAULT_SYMBOL_STATS = Path("data/processed/binance-usdm-30d/symbol_summary.csv")
DEFAULT_VOLUME_PRICE_PROFILE = Path("data/processed/binance-usdm-30d/volume_price_profile.json")
DEFAULT_REPORT_DIR = Path("reports/screeners")
DEFAULT_CACHE_DIR = Path("data/cache/binance-usdm")
CACHE_TTL_SECONDS = {
    "/fapi/v1/exchangeInfo": 24 * 60 * 60,
    "/fapi/v1/ticker/24hr": 5 * 60,
    "/fapi/v1/premiumIndex": 5 * 60,
}


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    taker_buy_quote_volume: float


def now_label() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d-%H%M")


def cache_path(path: str, params: dict[str, Any] | None = None) -> Path:
    name = path.strip("/").replace("/", "_")
    if params:
        suffix = "_".join(f"{key}-{value}" for key, value in sorted(params.items()))
        name = f"{name}_{suffix}"
    return DEFAULT_CACHE_DIR / f"{name}.json"


def read_cache(path: str, params: dict[str, Any] | None = None, max_age_seconds: int | None = None) -> Any | None:
    file_path = cache_path(path, params)
    if not file_path.exists():
        return None
    if max_age_seconds is not None and time.time() - file_path.stat().st_mtime > max_age_seconds:
        return None
    return json.loads(file_path.read_text())


def write_cache(path: str, params: dict[str, Any] | None, data: Any) -> None:
    file_path = cache_path(path, params)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False))


def http_get(path: str, params: dict[str, Any] | None = None, retries: int = 5, timeout: float = 30, use_cache: bool = True) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                BASE_URL + path,
                params=params or {},
                timeout=timeout,
                headers={"User-Agent": "alpha-vault-screener/1.0"},
            )
            response.raise_for_status()
            data = response.json()
            if use_cache and path in CACHE_TTL_SECONDS and not params:
                write_cache(path, params, data)
            return data
        except Exception as exc:  # network layer is unstable in this environment
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 30))
    if use_cache and path in CACHE_TTL_SECONDS and not params:
        cached = read_cache(path, params, CACHE_TTL_SECONDS[path])
        if cached is not None:
            return cached
    detail = str(last_error)[:160] if last_error else "unknown"
    raise RuntimeError(f"{path} failed after {retries} attempts: {type(last_error).__name__}: {detail}")


def dec(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value))


def read_symbol_history(path: Path) -> dict[str, dict[str, Decimal]]:
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        rows = csv.DictReader(handle)
        return {
            row["symbol"]: {
                "net": dec(row["net"]),
                "realized_pnl": dec(row["realized_pnl"]),
                "commission": dec(row["commission"]),
                "insurance_clear": dec(row["insurance_clear"]),
                "trade_count": dec(row["trade_count"]),
            }
            for row in rows
            if row.get("symbol") and row["symbol"] != "ACCOUNT"
        }


def read_volume_price_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def load_tradeable_symbols() -> set[str]:
    data = http_get("/fapi/v1/exchangeInfo")
    symbols = set()
    for item in data.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
        ):
            symbols.add(item["symbol"])
    return symbols


def load_liquid_symbols(max_symbols: int, min_quote_volume: float) -> list[dict[str, Any]]:
    tradeable = load_tradeable_symbols()
    tickers = http_get("/fapi/v1/ticker/24hr")
    rows = []
    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if symbol not in tradeable:
            continue
        quote_volume = float(ticker.get("quoteVolume") or 0)
        if quote_volume < min_quote_volume:
            continue
        rows.append(
            {
                "symbol": symbol,
                "quote_volume": quote_volume,
                "price_change_pct_24h": float(ticker.get("priceChangePercent") or 0),
                "last_price": float(ticker.get("lastPrice") or 0),
            }
        )
    rows.sort(key=lambda item: item["quote_volume"], reverse=True)
    return rows[:max_symbols]


def load_symbol_metas(symbols: list[str]) -> list[dict[str, Any]]:
    wanted = list(dict.fromkeys(symbols))
    tradeable = load_tradeable_symbols()
    tickers = http_get("/fapi/v1/ticker/24hr")
    by_symbol = {ticker.get("symbol", ""): ticker for ticker in tickers}
    rows = []
    for symbol in wanted:
        if symbol not in tradeable:
            continue
        ticker = by_symbol.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "quote_volume": float(ticker.get("quoteVolume") or 0),
                "price_change_pct_24h": float(ticker.get("priceChangePercent") or 0),
                "last_price": float(ticker.get("lastPrice") or 0),
            }
        )
    return rows


def load_funding_rates() -> dict[str, float]:
    data = http_get("/fapi/v1/premiumIndex")
    rows = data if isinstance(data, list) else [data]
    rates: dict[str, float] = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol:
            rates[symbol] = float(row.get("lastFundingRate") or 0)
    return rates


def parse_klines(rows: list[list[Any]]) -> list[Candle]:
    candles = []
    for row in rows:
        candles.append(
            Candle(
                open_time=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[7]),
                taker_buy_quote_volume=float(row[10]) if len(row) > 10 else 0.0,
            )
        )
    return candles


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append((value - out[-1]) * multiplier + out[-1])
    return out


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        change = cur - prev
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) <= period:
        return 0.0
    true_ranges = []
    for prev, cur in zip(candles[-period - 1 : -1], candles[-period:]):
        true_ranges.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return sum(true_ranges) / period


def pct(value: float) -> float:
    return value * 100


def slope_pct(values: list[float], lookback: int) -> float:
    if len(values) <= lookback or values[-lookback] == 0:
        return 0.0
    return (values[-1] / values[-lookback] - 1) * 100


def bollinger_width_pct(values: list[float], period: int = 20) -> float:
    if len(values) < period:
        return 0.0
    window = values[-period:]
    middle = statistics.mean(window)
    if middle == 0:
        return 0.0
    std = statistics.pstdev(window)
    return (4 * std / middle) * 100


def taker_buy_ratio(candles: list[Candle], lookback: int) -> float:
    window = candles[-lookback:] if len(candles) >= lookback else candles
    quote_volume = sum(c.quote_volume for c in window)
    if quote_volume <= 0:
        return 0.5
    return sum(c.taker_buy_quote_volume for c in window) / quote_volume


def trade_flow(symbol: str, limit: int = 120) -> dict[str, float]:
    trades = http_get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit}, use_cache=False)
    buy_quote = 0.0
    sell_quote = 0.0
    for trade in trades:
        quote = float(trade.get("p", 0)) * float(trade.get("q", 0))
        if trade.get("m"):
            sell_quote += quote
        else:
            buy_quote += quote
    total = buy_quote + sell_quote
    return {
        "recent_trade_quote": total,
        "recent_trade_buy_ratio": buy_quote / total if total else 0.5,
        "recent_trade_imbalance": (buy_quote - sell_quote) / total if total else 0.0,
    }


def volume_price_regime(volume_ratio: float, trend_pct: float, taker_buy_ratio: float) -> str:
    if volume_ratio >= 3.5:
        volume_state = "volume_spike"
    elif volume_ratio >= 1.2:
        volume_state = "volume_expansion"
    elif volume_ratio < 0.7:
        volume_state = "weak_volume"
    else:
        volume_state = "normal_volume"

    if trend_pct >= 0.2:
        price_state = "up"
    elif trend_pct <= -0.2:
        price_state = "down"
    else:
        price_state = "flat"

    if taker_buy_ratio >= 0.58:
        flow_state = "buy_dominant"
    elif taker_buy_ratio <= 0.42:
        flow_state = "sell_dominant"
    else:
        flow_state = "taker_neutral"
    return f"{volume_state}_{price_state}_{flow_state}"


def _tokens(value: Any) -> list[str]:
    return [item for item in str(value or "").split(",") if item]


def _join_tokens(values: list[str]) -> str:
    return ",".join(dict.fromkeys(item for item in values if item))


def _profile_regime_stats(profile: dict[str, Any], symbol: str, regime: str, min_samples: int = 12) -> dict[str, Any] | None:
    symbol_stats = (
        profile.get("symbols", {})
        .get(symbol, {})
        .get("regimes", {})
        .get(regime)
    )
    if symbol_stats and int(symbol_stats.get("samples", 0)) >= min_samples:
        return symbol_stats
    global_stats = profile.get("global", {}).get("regimes", {}).get(regime)
    if global_stats and int(global_stats.get("samples", 0)) >= min_samples:
        return global_stats
    return symbol_stats or global_stats


def order_book_metrics(depth: dict[str, Any], levels: int = 20) -> dict[str, float]:
    def side_notional(rows: list[list[Any]]) -> list[float]:
        notionals = []
        for price, qty, *_rest in rows[:levels]:
            notionals.append(float(price) * float(qty))
        return notionals

    bids = side_notional(depth.get("bids") or [])
    asks = side_notional(depth.get("asks") or [])
    bid_total = sum(bids)
    ask_total = sum(asks)
    total = bid_total + ask_total
    best_bid = float((depth.get("bids") or [["0", "0"]])[0][0])
    best_ask = float((depth.get("asks") or [["0", "0"]])[0][0])
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    return {
        "bid_notional": round(bid_total, 4),
        "ask_notional": round(ask_total, 4),
        "imbalance": round((bid_total - ask_total) / total, 4) if total else 0.0,
        "spread_bps": round(((best_ask - best_bid) / mid) * 10000, 4) if mid else 0.0,
        "bid_wall_ratio": round((max(bids) / bid_total), 4) if bid_total else 0.0,
        "ask_wall_ratio": round((max(asks) / ask_total), 4) if ask_total else 0.0,
    }


def load_order_book_metrics(symbol: str, limit: int = 20, timeout: float = 2.0) -> dict[str, Any]:
    depth = http_get("/fapi/v1/depth", {"symbol": symbol, "limit": limit}, retries=1, timeout=timeout, use_cache=False)
    return order_book_metrics(depth, levels=limit)


def apply_volume_price_context(
    row: dict[str, Any],
    profile: dict[str, Any] | None = None,
    order_book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adjusted = dict(row)
    direction = str(adjusted.get("direction") or "")
    score = float(adjusted.get("score") or 0)
    long_score = float(adjusted.get("long_score") or 0)
    short_score = float(adjusted.get("short_score") or 0)
    volume_ratio = float(adjusted.get("volume_ratio") or 0)
    micro_volume_ratio = float(adjusted.get("micro_volume_ratio") or 0)
    trend_15m = float(adjusted.get("trend_15m_pct") or 0)
    trend_1h = float(adjusted.get("trend_1h_pct") or 0)
    taker_buy = float(adjusted.get("taker_buy_ratio_15m") or 0.5)
    regime = volume_price_regime(volume_ratio, trend_1h or trend_15m, taker_buy)
    reasons = _tokens(adjusted.get("reasons"))
    risk_flags = _tokens(adjusted.get("risk_flags"))
    notes: list[str] = []
    delta = 0.0

    stats = _profile_regime_stats(profile or {}, str(adjusted.get("symbol") or ""), regime)
    if stats and direction in {"LONG", "SHORT"}:
        forward_4h = float(stats.get("forward_return_4h_mean") or 0)
        if direction == "LONG":
            win_rate = float(stats.get("long_win_rate_4h") or 0)
            expected = forward_4h
        else:
            win_rate = float(stats.get("short_win_rate_4h") or 0)
            expected = -forward_4h
        if win_rate >= 0.55 and expected >= 0.12:
            delta += 8
            reasons.append("volume_price_confirmed")
            notes.append(f"profile_win={win_rate:.2f}")
        elif win_rate <= 0.45 or expected <= -0.12:
            delta -= 10
            risk_flags.append("volume_price_profile_adverse")
            notes.append(f"profile_adverse={win_rate:.2f}")

    if direction == "LONG":
        if (trend_15m > 0.15 or trend_1h > 0.2) and (volume_ratio < 0.7 or taker_buy < 0.52):
            delta -= 12
            risk_flags.append("volume_price_divergence")
            notes.append("uptrend_without_volume_buying")
        elif volume_ratio >= 1.2 and taker_buy >= 0.58:
            delta += 4
            reasons.append("volume_price_current_confirmed")
    elif direction == "SHORT":
        if (trend_15m < -0.15 or trend_1h < -0.2) and (volume_ratio < 0.7 or taker_buy > 0.48):
            delta -= 12
            risk_flags.append("volume_price_divergence")
            notes.append("downtrend_without_volume_selling")
        elif volume_ratio >= 1.2 and taker_buy <= 0.42:
            delta += 4
            reasons.append("volume_price_current_confirmed")

    if (volume_ratio > 3.5 or micro_volume_ratio > 4.0) and 0.47 <= taker_buy <= 0.53:
        delta -= 10
        risk_flags.append("volume_price_churn_or_exhaustion")
        notes.append("large_volume_without_direction")

    book = order_book or {}
    if book:
        if book.get("error"):
            adjusted["order_book_error"] = str(book["error"])[:120]
            delta -= 4
            risk_flags.append("order_book_unavailable")
            notes.append("order_book_unavailable")
        for key in ("imbalance", "spread_bps", "bid_wall_ratio", "ask_wall_ratio"):
            if key in book:
                adjusted[f"order_book_{key}"] = book[key]
        imbalance = float(book.get("imbalance") or 0)
        spread_bps = float(book.get("spread_bps") or 0)
        bid_wall = float(book.get("bid_wall_ratio") or 0)
        ask_wall = float(book.get("ask_wall_ratio") or 0)
        if spread_bps > 8:
            delta -= 8
            risk_flags.append("order_book_wide_spread")
        if direction == "LONG":
            if imbalance >= 0.18 and bid_wall >= ask_wall:
                delta += 4
                reasons.append("order_book_bid_support")
            if imbalance <= -0.18 or ask_wall >= 0.55:
                delta -= 6
                risk_flags.append("order_book_ask_pressure")
        elif direction == "SHORT":
            if imbalance <= -0.18 and ask_wall >= bid_wall:
                delta += 4
                reasons.append("order_book_ask_support")
            if imbalance >= 0.18 or bid_wall >= 0.55:
                delta -= 6
                risk_flags.append("order_book_bid_pressure")

    score += delta
    if direction == "LONG":
        long_score += delta
    elif direction == "SHORT":
        short_score += delta
    adjusted["score"] = round(score, 2)
    adjusted["long_score"] = round(long_score, 2)
    adjusted["short_score"] = round(short_score, 2)
    adjusted["volume_price_score"] = round(delta, 2)
    adjusted["volume_price_regime"] = regime
    adjusted["volume_price_notes"] = _join_tokens(notes)
    adjusted["reasons"] = _join_tokens(reasons)
    adjusted["risk_flags"] = _join_tokens(risk_flags)
    action = str(adjusted.get("action") or "")
    if adjusted["risk_flags"] and action not in {"BLOCK", "SKIP"} and not action.startswith("REVIEW_"):
        adjusted["action"] = "REVIEW_" + action
    return adjusted


def history_adjustment(symbol: str, history: dict[str, dict[str, Decimal]]) -> tuple[int, list[str]]:
    row = history.get(symbol)
    if not row:
        return 0, []
    score = 0
    notes = []
    if row["net"] > Decimal("40"):
        score += 8
        notes.append("past_winner")
    if row["net"] < Decimal("-50"):
        score -= 18
        notes.append("past_major_loser")
    elif row["net"] < Decimal("0"):
        score -= 6
        notes.append("past_loser")
    if row["insurance_clear"] < 0:
        score -= 25
        notes.append("insurance_clear_block")
    if row["trade_count"] >= 100 and row["net"] < 0:
        score -= 10
        notes.append("high_freq_loss")
    return score, notes


def primary_strategy(strategy_tags: list[str], reasons: list[str], risk_flags: list[str]) -> str:
    tags = set(strategy_tags)
    if "insurance_clear_block" in reasons:
        return "blocked_by_history"
    if "mean_reversion_countertrend" in risk_flags:
        return "countertrend_mean_reversion"
    for name in [
        "trend_pullback",
        "breakout_momentum",
        "capital_flow",
        "trend_following",
        "funding_contrarian",
        "volatility_compression",
    ]:
        if name in tags:
            return name
    return "none"


def score_symbol(
    meta: dict[str, Any],
    candles_15m: list[Candle],
    candles_1m: list[Candle],
    candles_1h: list[Candle],
    history: dict[str, dict[str, Decimal]],
    funding_rate: float = 0.0,
    recent_flow: dict[str, float] | None = None,
    volume_price_profile: dict[str, Any] | None = None,
    order_book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    closes = [c.close for c in candles_15m]
    vols = [c.quote_volume for c in candles_15m]
    closes_1h = [c.close for c in candles_1h]
    closes_1m = [c.close for c in candles_1m]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema55 = ema(closes, 55)
    ema21_1h = ema(closes_1h, 21)
    ema55_1h = ema(closes_1h, 55)
    last = closes[-1]
    rsi14 = rsi(closes)
    atr14 = atr(candles_15m)
    atr_pct = (atr14 / last * 100) if last else 0.0
    vol_recent = statistics.mean(vols[-4:]) if len(vols) >= 4 else statistics.mean(vols)
    vol_base = statistics.mean(vols[-32:-4]) if len(vols) >= 36 else statistics.mean(vols)
    vol_ratio = vol_recent / vol_base if vol_base else 0.0
    trend_15m = slope_pct(ema21, 8)
    trend_1h = slope_pct(ema21_1h, 6)
    price_vs_ema21 = (last / ema21[-1] - 1) * 100 if ema21[-1] else 0.0
    price_vs_ema55 = (last / ema55[-1] - 1) * 100 if ema55[-1] else 0.0
    bb_width = bollinger_width_pct(closes)
    micro_trend_1m = slope_pct(closes_1m, 8)
    micro_trend_5m = slope_pct(closes_1m, 5)
    micro_quote_recent = statistics.mean([c.quote_volume for c in candles_1m[-5:]]) if len(candles_1m) >= 5 else 0.0
    micro_quote_base = statistics.mean([c.quote_volume for c in candles_1m[-30:-5]]) if len(candles_1m) >= 35 else micro_quote_recent
    micro_volume_ratio = micro_quote_recent / micro_quote_base if micro_quote_base else 0.0
    taker_buy_ratio_1m = taker_buy_ratio(candles_1m, 5)
    taker_buy_ratio_15m = taker_buy_ratio(candles_15m, 4)
    flow = recent_flow or {"recent_trade_quote": 0.0, "recent_trade_buy_ratio": 0.5, "recent_trade_imbalance": 0.0}
    recent_buy_ratio = float(flow.get("recent_trade_buy_ratio", 0.5))
    recent_imbalance = float(flow.get("recent_trade_imbalance", 0.0))
    high_20 = max(c.high for c in candles_15m[-20:])
    low_20 = min(c.low for c in candles_15m[-20:])
    breakout_distance = (last / high_20 - 1) * 100 if high_20 else 0.0
    breakdown_distance = (last / low_20 - 1) * 100 if low_20 else 0.0

    long_score = 0
    short_score = 0
    reasons: list[str] = []
    risk_flags: list[str] = []
    strategy_tags: list[str] = []

    if ema9[-1] > ema21[-1] > ema55[-1]:
        long_score += 24
        reasons.append("15m_ema_bull")
        strategy_tags.append("trend_following")
    if ema9[-1] < ema21[-1] < ema55[-1]:
        short_score += 24
        reasons.append("15m_ema_bear")
        strategy_tags.append("trend_following")
    if ema21_1h[-1] > ema55_1h[-1]:
        long_score += 16
        reasons.append("1h_trend_up")
    if ema21_1h[-1] < ema55_1h[-1]:
        short_score += 16
        reasons.append("1h_trend_down")

    if trend_15m > 0.15:
        long_score += 10
    if trend_15m < -0.15:
        short_score += 10
    if trend_1h > 0.2:
        long_score += 8
    if trend_1h < -0.2:
        short_score += 8

    if 1.2 <= vol_ratio <= 3.5:
        long_score += 10
        short_score += 10
        reasons.append("volume_confirm")
    elif vol_ratio > 5:
        risk_flags.append("abnormal_volume")
        long_score -= 12
        short_score -= 12
    elif vol_ratio < 0.7:
        risk_flags.append("weak_recent_volume")
        long_score -= 8
        short_score -= 8

    if micro_volume_ratio >= 1.5 and taker_buy_ratio_1m >= 0.58 and recent_imbalance >= 0.12:
        long_score += 12
        reasons.append("micro_capital_inflow")
        strategy_tags.append("capital_flow")
    if micro_volume_ratio >= 1.5 and taker_buy_ratio_1m <= 0.42 and recent_imbalance <= -0.12:
        short_score += 12
        reasons.append("micro_capital_outflow")
        strategy_tags.append("capital_flow")
    if micro_trend_1m > 0.12 and taker_buy_ratio_1m >= 0.55:
        long_score += 6
        reasons.append("1m_micro_bull")
    if micro_trend_1m < -0.12 and taker_buy_ratio_1m <= 0.45:
        short_score += 6
        reasons.append("1m_micro_bear")
    if taker_buy_ratio_15m >= 0.58:
        long_score += 5
        reasons.append("15m_taker_buy_dominant")
    if taker_buy_ratio_15m <= 0.42:
        short_score += 5
        reasons.append("15m_taker_sell_dominant")
    if micro_volume_ratio > 4 and abs(recent_imbalance) < 0.08:
        risk_flags.append("micro_churn_without_direction")
        long_score -= 6
        short_score -= 6

    if 48 <= rsi14 <= 68:
        long_score += 8
    elif rsi14 > 78:
        risk_flags.append("long_chase_rsi")
        long_score -= 20
    if 32 <= rsi14 <= 52:
        short_score += 8
    elif rsi14 < 22:
        risk_flags.append("short_oversold_rsi")
        short_score -= 20

    if 0.2 <= atr_pct <= 2.5:
        long_score += 6
        short_score += 6
    elif atr_pct > 4:
        risk_flags.append("atr_too_wide")
        long_score -= 20
        short_score -= 20
    elif atr_pct < 0.12:
        risk_flags.append("atr_too_low")
        long_score -= 5
        short_score -= 5

    if price_vs_ema21 > 4.5:
        risk_flags.append("extended_above_ema21")
        long_score -= 14
    if price_vs_ema21 < -4.5:
        risk_flags.append("extended_below_ema21")
        short_score -= 14

    if breakout_distance >= -0.25 and price_vs_ema21 < 3.0:
        long_score += 8
        reasons.append("near_20bar_breakout")
        strategy_tags.append("breakout_momentum")
    if breakdown_distance <= 0.25 and price_vs_ema21 > -3.0:
        short_score += 8
        reasons.append("near_20bar_breakdown")
        strategy_tags.append("breakout_momentum")

    if ema21_1h[-1] > ema55_1h[-1] and trend_1h > 0.2 and -0.8 <= price_vs_ema21 <= 1.2 and 45 <= rsi14 <= 62:
        long_score += 12
        reasons.append("trend_pullback_long")
        strategy_tags.append("trend_pullback")
    if ema21_1h[-1] < ema55_1h[-1] and trend_1h < -0.2 and -1.2 <= price_vs_ema21 <= 0.8 and 38 <= rsi14 <= 55:
        short_score += 12
        reasons.append("trend_pullback_short")
        strategy_tags.append("trend_pullback")

    if bb_width and bb_width < 1.8 and atr_pct < 0.8 and vol_ratio >= 1.0:
        long_score += 4
        short_score += 4
        reasons.append("volatility_compression")
        strategy_tags.append("volatility_compression")

    if price_vs_ema21 > 5 and rsi14 >= 75:
        short_score += 12
        risk_flags.append("mean_reversion_countertrend")
        reasons.append("overextended_mean_reversion_short")
        strategy_tags.append("mean_reversion")
    if price_vs_ema21 < -5 and rsi14 <= 25:
        long_score += 12
        risk_flags.append("mean_reversion_countertrend")
        reasons.append("overextended_mean_reversion_long")
        strategy_tags.append("mean_reversion")

    if funding_rate >= 0.001:
        short_score += 6
        reasons.append("positive_funding_short_bias")
        strategy_tags.append("funding_contrarian")
    elif funding_rate <= -0.001:
        long_score += 6
        reasons.append("negative_funding_long_bias")
        strategy_tags.append("funding_contrarian")

    long_has_trigger = any(
        item in reasons
        for item in [
            "volume_confirm",
            "near_20bar_breakout",
            "trend_pullback_long",
            "negative_funding_long_bias",
            "volatility_compression",
            "micro_capital_inflow",
            "1m_micro_bull",
            "15m_taker_buy_dominant",
        ]
    )
    short_has_trigger = any(
        item in reasons
        for item in [
            "volume_confirm",
            "near_20bar_breakdown",
            "trend_pullback_short",
            "positive_funding_short_bias",
            "volatility_compression",
            "micro_capital_outflow",
            "1m_micro_bear",
            "15m_taker_sell_dominant",
        ]
    )
    if long_score >= 60 and not long_has_trigger:
        long_score -= 12
        risk_flags.append("no_long_entry_trigger")
    if short_score >= 60 and not short_has_trigger:
        short_score -= 12
        risk_flags.append("no_short_entry_trigger")

    if meta["price_change_pct_24h"] > 5 and not long_has_trigger:
        long_score -= 10
        risk_flags.append("long_after_large_24h_rise_without_trigger")
    if meta["price_change_pct_24h"] < -5 and not short_has_trigger:
        short_score -= 10
        risk_flags.append("short_after_large_24h_drop_without_trigger")

    history_score, history_notes = history_adjustment(meta["symbol"], history)
    long_score += history_score
    short_score += history_score
    reasons.extend(history_notes)

    if "insurance_clear_block" in history_notes:
        action = "BLOCK"
        direction = "NONE"
        final_score = min(long_score, short_score)
    elif long_score >= short_score and long_score >= 45:
        action = "WATCH_LONG"
        direction = "LONG"
        final_score = long_score
    elif short_score > long_score and short_score >= 45:
        action = "WATCH_SHORT"
        direction = "SHORT"
        final_score = short_score
    else:
        action = "SKIP"
        direction = "NONE"
        final_score = max(long_score, short_score)

    if risk_flags and action != "BLOCK":
        action = "REVIEW_" + action

    row = {
        "symbol": meta["symbol"],
        "action": action,
        "direction": direction,
        "score": round(final_score, 2),
        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),
        "last_price": last,
        "price_change_pct_24h": round(meta["price_change_pct_24h"], 3),
        "quote_volume_24h": round(meta["quote_volume"], 2),
        "rsi14_15m": round(rsi14, 2),
        "atr_pct_15m": round(atr_pct, 3),
        "volume_ratio": round(vol_ratio, 3),
        "micro_volume_ratio": round(micro_volume_ratio, 3),
        "micro_trend_1m_pct": round(micro_trend_1m, 3),
        "micro_trend_5m_pct": round(micro_trend_5m, 3),
        "taker_buy_ratio_1m": round(taker_buy_ratio_1m, 3),
        "taker_buy_ratio_15m": round(taker_buy_ratio_15m, 3),
        "recent_trade_buy_ratio": round(recent_buy_ratio, 3),
        "recent_trade_imbalance": round(recent_imbalance, 3),
        "recent_trade_quote": round(float(flow.get("recent_trade_quote", 0.0)), 2),
        "funding_rate": round(funding_rate, 8),
        "bb_width_pct": round(bb_width, 3),
        "trend_15m_pct": round(trend_15m, 3),
        "trend_1h_pct": round(trend_1h, 3),
        "price_vs_ema21_pct": round(price_vs_ema21, 3),
        "price_vs_ema55_pct": round(price_vs_ema55, 3),
        "primary_strategy": primary_strategy(strategy_tags, reasons, risk_flags),
        "strategy_tags": ",".join(dict.fromkeys(strategy_tags)),
        "reasons": ",".join(dict.fromkeys(reasons)),
        "risk_flags": ",".join(dict.fromkeys(risk_flags)),
    }
    return apply_volume_price_context(row, volume_price_profile or {}, order_book or {})


def write_outputs(rows: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    label = now_label()
    csv_path = report_dir / f"{label}-binance-usdm-15m-trend.csv"
    md_path = report_dir / f"{label}-binance-usdm-15m-trend.md"
    fields = [
        "symbol",
        "action",
        "direction",
        "score",
        "long_score",
        "short_score",
        "last_price",
        "price_change_pct_24h",
        "quote_volume_24h",
        "primary_strategy",
        "strategy_tags",
        "rsi14_15m",
        "atr_pct_15m",
        "volume_ratio",
        "micro_volume_ratio",
        "micro_trend_1m_pct",
        "micro_trend_5m_pct",
        "taker_buy_ratio_1m",
        "taker_buy_ratio_15m",
        "recent_trade_buy_ratio",
        "recent_trade_imbalance",
        "recent_trade_quote",
        "volume_price_score",
        "volume_price_regime",
        "volume_price_notes",
        "order_book_imbalance",
        "order_book_spread_bps",
        "order_book_bid_wall_ratio",
        "order_book_ask_wall_ratio",
        "order_book_error",
        "funding_rate",
        "bb_width_pct",
        "trend_15m_pct",
        "trend_1h_pct",
        "price_vs_ema21_pct",
        "price_vs_ema55_pct",
        "reasons",
        "risk_flags",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    watch_rows = [row for row in rows if "WATCH" in row["action"] and row["action"] != "BLOCK"]
    blocked = [row for row in rows if row["action"] == "BLOCK"]
    lines = [
        "# Binance USD-M 15m 快速筛选",
        "",
        f"- 生成时间：{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %z')}",
        "- 数据源：Binance USD-M Futures public market data",
        "- 交易边界：只读筛选，不下单；候选币必须人工复核止损、资金费、盘口和当前持仓。",
        "- 经验约束：亏损集中币降权，出现过 `INSURANCE_CLEAR` 的 symbol 阻断，异常放量/ATR/RSI 追涨杀跌标记为 REVIEW。",
        "",
        "## 候选列表",
        "",
        "| Symbol | Strategy | Action | Dir | Score | VP | OB | 15m | 1h | Taker1m | Flow | RSI | ATR% | Reasons | Risk |",
        "|---|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in watch_rows[:25]:
        order_book_label = (
            f"imb={row.get('order_book_imbalance', '-')}"
            f"/spr={row.get('order_book_spread_bps', '-')}"
        )
        lines.append(
            f"| `{row['symbol']}` | {row['primary_strategy']} | {row['action']} | {row['direction']} | {row['score']} | {row.get('volume_price_regime', '-')}({row.get('volume_price_score', 0)}) | {order_book_label} | {row['trend_15m_pct']} | {row['trend_1h_pct']} | {row['taker_buy_ratio_1m']} | {row['recent_trade_imbalance']} | {row['rsi14_15m']} | {row['atr_pct_15m']} | {row['reasons']} | {row['risk_flags']} |"
        )
    if not watch_rows:
        lines.append("| - | SKIP | NONE | - | - | - | - | - | - | - | 当前无足够干净候选 | - |")

    lines += [
        "",
        "## 阻断列表",
        "",
        "| Symbol | Reason |",
        "|---|---|",
    ]
    for row in blocked[:20]:
        lines.append(f"| `{row['symbol']}` | {row['reasons']} / {row['risk_flags']} |")
    if not blocked:
        lines.append("| - | 无 |")

    lines += [
        "",
        "## 使用方式",
        "",
        "- `WATCH_LONG` / `WATCH_SHORT` 只是候选，不是入场指令。",
        "- `REVIEW_*` 表示有趋势信号但存在追涨、超跌、放量异常或 ATR 风险，必须降仓或跳过。",
        "- `BLOCK` 表示历史复盘中有严重风控问题，完成单独复盘前不主动交易。",
        "",
    ]
    md_path.write_text("\n".join(lines))
    return csv_path, md_path


def scan_symbol(
    meta: dict[str, Any],
    klines_limit: int,
    history: dict[str, dict[str, Decimal]],
    funding_rates: dict[str, float],
    volume_price_profile: dict[str, Any] | None = None,
    order_book_enabled: bool = True,
    order_book_levels: int = 20,
    order_book_timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    symbol = meta["symbol"]
    klines_15m = parse_klines(
        http_get("/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "limit": klines_limit})
    )
    klines_1h = parse_klines(
        http_get("/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": max(80, klines_limit // 2)})
    )
    klines_1m = parse_klines(
        http_get("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 60}, use_cache=False)
    )
    recent_flow = trade_flow(symbol)
    order_book: dict[str, Any] = {}
    if order_book_enabled:
        try:
            order_book = load_order_book_metrics(
                symbol,
                limit=max(5, min(int(order_book_levels), 100)),
                timeout=max(0.2, float(order_book_timeout_seconds)),
            )
        except Exception as exc:
            order_book = {"error": str(exc) if str(exc) else type(exc).__name__}
    if len(klines_15m) < 60 or len(klines_1h) < 50 or len(klines_1m) < 20:
        raise ValueError("not_enough_klines")
    return score_symbol(
        meta,
        klines_15m,
        klines_1m,
        klines_1h,
        history,
        funding_rates.get(symbol, 0.0),
        recent_flow,
        volume_price_profile,
        order_book,
    )


def scan_market(
    max_symbols: int = 40,
    min_quote_volume: float = 30_000_000,
    klines_limit: int = 120,
    history_path: Path = DEFAULT_SYMBOL_STATS,
    volume_price_profile_path: Path = DEFAULT_VOLUME_PRICE_PROFILE,
    order_book_enabled: bool = True,
    order_book_levels: int = 20,
    order_book_timeout_seconds: float = 2.0,
    concurrency: int = 1,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    history = read_symbol_history(history_path)
    volume_price_profile = read_volume_price_profile(volume_price_profile_path)
    metas = load_liquid_symbols(max_symbols, min_quote_volume)
    funding_rates = load_funding_rates()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    worker_count = max(1, min(int(concurrency), len(metas) or 1))

    if worker_count == 1:
        for index, meta in enumerate(metas, start=1):
            symbol = meta["symbol"]
            try:
                rows.append(
                    scan_symbol(
                        meta,
                        klines_limit,
                        history,
                        funding_rates,
                        volume_price_profile,
                        order_book_enabled,
                        order_book_levels,
                        order_book_timeout_seconds,
                    )
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc) if str(exc) else type(exc).__name__})
            if progress_callback:
                progress_callback(index, len(metas), symbol)
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    scan_symbol,
                    meta,
                    klines_limit,
                    history,
                    funding_rates,
                    volume_price_profile,
                    order_book_enabled,
                    order_book_levels,
                    order_book_timeout_seconds,
                ): meta
                for meta in metas
            }
            for future in as_completed(futures):
                meta = futures[future]
                symbol = meta["symbol"]
                try:
                    rows.append(future.result())
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc) if str(exc) else type(exc).__name__})
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(metas), symbol)

    rows.sort(key=lambda row: (0 if "WATCH" in row["action"] else 1, -float(row["score"]), row["symbol"]))
    return rows, errors, len(metas)


def scan_symbols(
    symbols: list[str],
    klines_limit: int = 120,
    history_path: Path = DEFAULT_SYMBOL_STATS,
    volume_price_profile_path: Path = DEFAULT_VOLUME_PRICE_PROFILE,
    order_book_enabled: bool = True,
    order_book_levels: int = 20,
    order_book_timeout_seconds: float = 2.0,
    concurrency: int = 1,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    history = read_symbol_history(history_path)
    volume_price_profile = read_volume_price_profile(volume_price_profile_path)
    metas = load_symbol_metas(symbols)
    funding_rates = load_funding_rates()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    worker_count = max(1, min(int(concurrency), len(metas) or 1))

    if worker_count == 1:
        for index, meta in enumerate(metas, start=1):
            symbol = meta["symbol"]
            try:
                rows.append(
                    scan_symbol(
                        meta,
                        klines_limit,
                        history,
                        funding_rates,
                        volume_price_profile,
                        order_book_enabled,
                        order_book_levels,
                        order_book_timeout_seconds,
                    )
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc) if str(exc) else type(exc).__name__})
            if progress_callback:
                progress_callback(index, len(metas), symbol)
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    scan_symbol,
                    meta,
                    klines_limit,
                    history,
                    funding_rates,
                    volume_price_profile,
                    order_book_enabled,
                    order_book_levels,
                    order_book_timeout_seconds,
                ): meta
                for meta in metas
            }
            for future in as_completed(futures):
                meta = futures[future]
                symbol = meta["symbol"]
                try:
                    rows.append(future.result())
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc) if str(exc) else type(exc).__name__})
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(metas), symbol)

    rows.sort(key=lambda row: (0 if "WATCH" in row["action"] else 1, -float(row["score"]), row["symbol"]))
    return rows, errors, len(metas)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-symbols", type=int, default=40)
    parser.add_argument("--min-quote-volume", type=float, default=30_000_000)
    parser.add_argument("--klines-limit", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--history", default=str(DEFAULT_SYMBOL_STATS))
    parser.add_argument("--volume-price-profile", default=str(DEFAULT_VOLUME_PRICE_PROFILE))
    parser.add_argument("--disable-order-book", action="store_true")
    parser.add_argument("--order-book-levels", type=int, default=20)
    parser.add_argument("--order-book-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    args = parser.parse_args()

    rows, errors, symbols_scanned = scan_market(
        max_symbols=args.max_symbols,
        min_quote_volume=args.min_quote_volume,
        klines_limit=args.klines_limit,
        history_path=Path(args.history),
        volume_price_profile_path=Path(args.volume_price_profile),
        order_book_enabled=not args.disable_order_book,
        order_book_levels=args.order_book_levels,
        order_book_timeout_seconds=args.order_book_timeout_seconds,
        concurrency=args.concurrency,
    )
    csv_path, md_path = write_outputs(rows, Path(args.report_dir))
    print(
        json.dumps(
            {
                "symbols_scanned": symbols_scanned,
                "rows": len(rows),
                "errors": errors[:10],
                "csv": str(csv_path),
                "report": str(md_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

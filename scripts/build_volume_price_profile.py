#!/usr/bin/env python3
"""Build a read-only 30-day volume/price profile for Binance USD-M symbols."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant.screeners.binance_usdm_15m_trend_screener import (
    BASE_URL,
    CST,
    Candle,
    load_liquid_symbols,
    load_symbol_metas,
    parse_klines,
    volume_price_regime,
)

import requests


DEFAULT_OUTPUT_JSON = Path("data/processed/binance-usdm-30d/volume_price_profile.json")
DEFAULT_OUTPUT_CSV = Path("data/processed/binance-usdm-30d/volume_price_profile.csv")


def candle_value(candle: Candle | dict[str, Any], key: str) -> float:
    if isinstance(candle, dict):
        return float(candle[key])
    return float(getattr(candle, key))


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[index]


def true_range_pct(candles: list[Candle | dict[str, Any]], index: int) -> float:
    current = candles[index]
    previous_close = candle_value(candles[index - 1], "close")
    high = candle_value(current, "high")
    low = candle_value(current, "low")
    close = candle_value(current, "close")
    if close <= 0:
        return 0.0
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    return true_range / close * 100


def regime_sample(candles: list[Candle | dict[str, Any]], index: int) -> dict[str, float | str]:
    quote_volumes = [candle_value(candle, "quote_volume") for candle in candles]
    closes = [candle_value(candle, "close") for candle in candles]
    volume_recent = mean(quote_volumes[max(0, index - 3) : index + 1])
    volume_base = mean(quote_volumes[max(0, index - 32) : max(0, index - 4)])
    volume_ratio = volume_recent / volume_base if volume_base else 0.0
    taker_quote = candle_value(candles[index], "taker_buy_quote_volume")
    candle_quote = candle_value(candles[index], "quote_volume")
    taker_buy_ratio = taker_quote / candle_quote if candle_quote else 0.5
    trend_1h = (closes[index] / closes[index - 4] - 1) * 100 if index >= 4 and closes[index - 4] else 0.0
    trend_4h = (closes[index] / closes[index - 16] - 1) * 100 if index >= 16 and closes[index - 16] else 0.0
    forward_1h = (closes[index + 4] / closes[index] - 1) * 100 if closes[index] else 0.0
    forward_4h = (closes[index + 16] / closes[index] - 1) * 100 if closes[index] else 0.0
    open_price = candle_value(candles[index], "open")
    high = candle_value(candles[index], "high")
    low = candle_value(candles[index], "low")
    close = closes[index]
    candle_range = max(high - low, 0.0)
    body_ratio = abs(close - open_price) / candle_range if candle_range else 0.0
    upper_shadow_ratio = (high - max(open_price, close)) / candle_range if candle_range else 0.0
    lower_shadow_ratio = (min(open_price, close) - low) / candle_range if candle_range else 0.0
    regime = volume_price_regime(volume_ratio, trend_1h, taker_buy_ratio)
    return {
        "regime": regime,
        "volume_ratio": volume_ratio,
        "taker_buy_ratio": taker_buy_ratio,
        "trend_1h_pct": trend_1h,
        "trend_4h_pct": trend_4h,
        "atr_pct": mean([true_range_pct(candles, item) for item in range(max(1, index - 13), index + 1)]),
        "body_ratio": body_ratio,
        "upper_shadow_ratio": upper_shadow_ratio,
        "lower_shadow_ratio": lower_shadow_ratio,
        "forward_return_1h": forward_1h,
        "forward_return_4h": forward_4h,
    }


def summarize_samples(samples: list[dict[str, float | str]]) -> dict[str, Any]:
    forward_1h = [float(sample["forward_return_1h"]) for sample in samples]
    forward_4h = [float(sample["forward_return_4h"]) for sample in samples]
    volume_ratios = [float(sample["volume_ratio"]) for sample in samples]
    taker_ratios = [float(sample["taker_buy_ratio"]) for sample in samples]
    return {
        "samples": len(samples),
        "forward_return_1h_mean": round(mean(forward_1h), 5),
        "forward_return_4h_mean": round(mean(forward_4h), 5),
        "forward_return_4h_p25": round(percentile(forward_4h, 0.25), 5),
        "forward_return_4h_p75": round(percentile(forward_4h, 0.75), 5),
        "long_win_rate_4h": round(sum(1 for value in forward_4h if value > 0.08) / len(forward_4h), 5) if forward_4h else 0.0,
        "short_win_rate_4h": round(sum(1 for value in forward_4h if value < -0.08) / len(forward_4h), 5) if forward_4h else 0.0,
        "volume_ratio_mean": round(mean(volume_ratios), 5),
        "taker_buy_ratio_mean": round(mean(taker_ratios), 5),
    }


def build_profile_from_candles(
    candles_by_symbol: dict[str, list[Candle | dict[str, Any]]],
    min_samples: int = 12,
) -> dict[str, Any]:
    global_buckets: dict[str, list[dict[str, float | str]]] = {}
    symbol_profiles: dict[str, Any] = {}
    for symbol, candles in candles_by_symbol.items():
        if len(candles) < 60:
            continue
        symbol_buckets: dict[str, list[dict[str, float | str]]] = {}
        for index in range(36, len(candles) - 16):
            sample = regime_sample(candles, index)
            regime = str(sample["regime"])
            symbol_buckets.setdefault(regime, []).append(sample)
            global_buckets.setdefault(regime, []).append(sample)
        symbol_profiles[symbol] = {
            "samples": sum(len(values) for values in symbol_buckets.values()),
            "regimes": {
                regime: summarize_samples(samples)
                for regime, samples in sorted(symbol_buckets.items())
                if len(samples) >= min_samples
            },
        }

    return {
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %z"),
        "interval": "15m",
        "lookback_days": 30,
        "min_samples": min_samples,
        "symbols": symbol_profiles,
        "global": {
            "samples": sum(len(values) for values in global_buckets.values()),
            "regimes": {
                regime: summarize_samples(samples)
                for regime, samples in sorted(global_buckets.items())
                if len(samples) >= min_samples
            },
        },
    }


def fetch_15m_klines(symbol: str, start: datetime, end: datetime) -> list[Candle]:
    rows: list[list[Any]] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor < end_ms:
        response = requests.get(
            BASE_URL + "/fapi/v1/klines",
            params={"symbol": symbol, "interval": "15m", "startTime": cursor, "endTime": end_ms - 1, "limit": 1500},
            timeout=30,
            headers={"User-Agent": "alpha-vault-volume-price-profile/1.0"},
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + 15 * 60 * 1000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.05)
    return parse_klines(rows)


def write_csv_summary(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "scope",
        "symbol",
        "regime",
        "samples",
        "forward_return_1h_mean",
        "forward_return_4h_mean",
        "forward_return_4h_p25",
        "forward_return_4h_p75",
        "long_win_rate_4h",
        "short_win_rate_4h",
        "volume_ratio_mean",
        "taker_buy_ratio_mean",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for regime, stats in (profile.get("global", {}).get("regimes") or {}).items():
            writer.writerow({"scope": "global", "symbol": "", "regime": regime, **stats})
        for symbol, symbol_profile in (profile.get("symbols") or {}).items():
            for regime, stats in (symbol_profile.get("regimes") or {}).items():
                writer.writerow({"scope": "symbol", "symbol": symbol, "regime": regime, **stats})


def parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="Comma-separated symbols. Defaults to liquid scan universe.")
    parser.add_argument("--max-symbols", type=int, default=80)
    parser.add_argument("--min-quote-volume", type=float, default=0)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-samples", type=int, default=12)
    parser.add_argument("--out-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--out-csv", default=str(DEFAULT_OUTPUT_CSV))
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    symbols = parse_symbols(args.symbols)
    metas = load_symbol_metas(symbols) if symbols else load_liquid_symbols(args.max_symbols, args.min_quote_volume)
    candles_by_symbol: dict[str, list[Candle]] = {}
    errors = []
    for meta in metas:
        symbol = meta["symbol"]
        try:
            candles_by_symbol[symbol] = fetch_15m_klines(symbol, start, end)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
    profile = build_profile_from_candles(candles_by_symbol, min_samples=args.min_samples)
    profile["source_symbols"] = [meta["symbol"] for meta in metas]
    profile["errors"] = errors

    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    write_csv_summary(Path(args.out_csv), profile)
    print(json.dumps({"profile": str(json_path), "symbols": len(candles_by_symbol), "errors": errors[:10]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

# Paper Signal Service Runbook

## Purpose

This service continuously scans Binance USD-M public market data and creates order intents. By default it only records paper state. Legacy live mode includes automatic protection writes as well as entry confirmation. See `docs/implementation-status.md` before using any live-mode command below.

## Start Once

```bash
python3 quant/services/paper_signal_service.py --once
```

The service prints per-symbol scan progress by default:

```text
[scan] 01/80 BTCUSDT
[scan] 02/80 ETHUSDT
```

The public service config scans up to 40 USDT perpetual contracts by 24h
quote volume, with a 30,000,000 USDT floor and 6 concurrent workers. In concurrent mode, progress lines are
printed as symbols finish, so the order is not guaranteed to match quote-volume
ranking. If Binance returns HTTP 429 or temporary IP restrictions, lower
`scanner.concurrency` to 20 or 12.

Hide progress when running from cron or another supervisor:

```bash
python3 quant/services/paper_signal_service.py --once --no-progress
```

## Run For Several Ticks

```bash
python3 quant/services/paper_signal_service.py --iterations 3
```

Without `--once`, the service runs continuously. The default paper scan sleep
interval is 60 seconds after the previous scan finishes. In `--confirm-live` mode, the service also applies a configured minimum
sleep while it has active live positions or an entry/precheck backoff window, so
read-only position checks and protection maintenance do not run in a tight loop:

```bash
python3 quant/services/paper_signal_service.py --confirm-live
```

## Outputs

```text
data/processed/paper-signal-service/state.json
data/processed/paper-signal-service/events.jsonl
data/processed/paper-signal-service/intents.jsonl
data/processed/paper-signal-service/scan_audit.jsonl
data/processed/live-execution/executions.jsonl
data/processed/operation-journal/YYYY-MM-DD-operations.jsonl
```

`scan_audit.jsonl` records every scan's top candidates and market snapshot:
price, 24h change, ATR, RSI, funding rate, trend, volume ratio, strategy tags,
and risk flags. `intents.jsonl` records the trade plan at the time of entry.
`executions.jsonl` records live order, flatten, and protection-order actions.
Every position action and stop-loss / take-profit action also carries an
`operation_reason`. The same operation record is mirrored to the daily operation
journal JSONL and, when `ALPHA_VAULT_MONGODB_URI` is configured, synchronously
inserted into MongoDB collection `alpha_vault.operation_audit` by default.

Markdown report output is off by default. Enable it only when needed:

```bash
python3 quant/services/paper_signal_service.py --once --write-report
```

## Safety Rules

- Market scope is Binance USD-M USDT perpetual contracts only. The scanner filters `quoteAsset == "USDT"` and `contractType == "PERPETUAL"` from `https://fapi.binance.com`.
- Live execution refuses non-`https://fapi.binance.com`, non-USDT, non-perpetual, or non-TRADING symbols before any order is sent.
- `intents.jsonl` is an approval-required order-intent log, not an executed order log.
- The service can hold at most one paper position.
- Live-confirm mode also enforces `risk.max_live_positions` before creating any
  new entry intent. The public default is `1`, so once Binance already has one active
  USD-M positions, the service only manages protection orders and skips new
  entries.
- Existing live positions are checked before each market scan so the service
  knows how many live slots are already occupied. It then scans only the
  currently held symbols and uses that fresh market data to update stop-loss /
  take-profit protection. Held-symbol checks include 15m/1h trend, 1m micro
  trend, taker buy/sell quote ratio, and recent aggregate-trade imbalance.
- If current live positions have already reached `risk.max_live_positions`, the
  service skips the later full-market opportunity scan and does not create any
  new entry intent.
- If Binance rejects a live entry with `-2019 Margin is insufficient`, the
  service uses `service.live_margin_insufficient_backoff_seconds` before
  another full-market entry attempt, while still keeping protection maintenance
  active for existing positions.
- Protection checks and protection-order adjustments for multiple open positions
  run concurrently. Within a single position, the service still cancels old
  orders before placing the new stop-loss and take-profit pair.
- It uses historical review data to block symbols with `INSURANCE_CLEAR`.
- It calculates stop loss and take profit from ATR-based risk.
- Exit distances depend on the selected risk model and configuration. Read
  `risk_model.py` and the current JSON; historical fixed-ATR descriptions are not current defaults.
- Once price has moved at least `risk.break_even_activation_pct` in the
  position's favor, the protection floor should no longer sit below the
  exchange break-even price. If Binance temporarily drops both current
  protective orders from the open-order snapshot, the rebuild path should reuse
  the most recent stop/take levels instead of recreating a weaker pair.
- If a held position is no longer suitable, the service stops hard-carrying it
  regardless of current profit or loss. It replaces protection with a tight exit
  bracket near mark price. By default, a strong opposite signal needs opposite
  score `65` plus both 15m EMA and 1h trend reversal; a weak hold signal below
  score `45` plus an opposite trend warning can also trigger the exit bracket.
  The default exit bracket is about `0.18%` around mark price.
- The public paper notional cap is 50% of configured paper equity, with a 1% risk budget. This is not an enforced account-wide available-margin cap.
- A candidate is ignored unless it reaches the configured score threshold.
- Entry scoring is not only 15m/1h trend. It also rewards aligned short-term
  capital flow such as 1m micro trend, 1m taker-buy dominance, 15m taker
  dominance, recent aggregate-trade imbalance, and recent 1m volume expansion.
  Opposite micro capital flow can also make an existing position unsuitable and
  trigger the tight exit bracket.
- Public paper config writes local audit records with optional MongoDB. Historical standalone live tools retain their required-journal defaults. Validate the intended audit configuration before any live use.

## Promotion Checklist

Before any real execution layer is considered:

- [ ] Run the paper service for at least two weeks.
- [ ] Review every paper stop loss and take profit.
- [ ] Confirm slippage and fee assumptions.
- [ ] Confirm Binance conditional order API behavior in a test environment.
- [ ] Add a manual approval gate.
- [ ] Add maximum daily loss, cooldown, and kill switch.

## Approved Live Execution

Legacy entry execution is separate from the scanner and includes an entry confirmation dialog. This does not cover every protection or cancellation write. The commands below are reference material, not part of the paper quick start.

Dry run:

```bash
python3 quant/services/live_execution.py --dry-run
```

Execute latest intent with a macOS confirmation dialog:

```bash
python3 quant/services/live_execution.py
```

Run scanner in live-confirm mode. This ignores paper open-position state,
checks existing live positions before the opportunity scan, scans held symbols
first, adjusts stop-loss / take-profit protection, skips the full-market scan if
the live-position cap is already reached, otherwise scans the broader market and
creates a live intent from the latest scan candidate. A confirmation dialog is
shown before any real Binance entry order is sent:

```bash
python3 quant/services/paper_signal_service.py --once --confirm-live
```

For the long-running live-confirm service, restart through the repository
helper so all stdout/stderr for the same calendar day is appended to one file:

```bash
python3 scripts/restart_paper_signal_service.py --enable-live-restart
```

Daily service logs are written to:

```text
reports/service/logs/paper_signal_service_confirm_live_YYYYMMDD.log
```

Use `--dry-run` to see the matched process, screen session, and target log path
without stopping or starting anything.

The public helper scopes process matching and screen names to this checkout.
It does not adopt or stop legacy processes started with relative service paths.

Existing live-position protection management:

- Reads current Binance USD-M positions after every scan.
- Uses the latest scanned ATR and mark price to calculate updated stop-loss and
  take-profit levels.
- Only tightens protection: LONG stops move up, SHORT stops move down; take
  profit only extends in the winning direction.
- If a position is missing protective orders, it proposes a new stop-loss and
  take-profit pair.
- In the current config, protection adjustments run directly after the scan.
  Entry orders still require the live-confirm execution path.

## Hourly Review

Generate a local review from the latest JSONL logs:

```bash
python3 scripts/hourly_quant_review.py --hours 1
```

Reports are written to:

```text
reports/service/hourly-reviews/
```

Each hourly review must include operation reasons and a strategy assessment for
position entries, rejections, protection cancellation, stop-loss placement, and
take-profit placement. Mark each item as `reasonable`, `needs_review`, or
`needs_adjustment`, and call out whether the strategy or risk gates should be
changed before the next live attempt.

For terminal confirmation instead of macOS dialog, set:

```json
"execution": {
  "mode": "confirm_then_live",
  "terminal_confirm": true
}
```

Execute a specific intent:

```bash
python3 quant/services/live_execution.py --intent-id <intent-id>
```

If the market entry succeeds but stop-loss / take-profit placement fails, the
executor attempts an immediate reduce-only market flatten and records the result
in:

```text
data/processed/live-execution/executions.jsonl
```

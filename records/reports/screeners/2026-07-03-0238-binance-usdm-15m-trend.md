> Historical source record, sanitized for publication. Quantitative values, identifiers and private paths have been withheld. Dates and qualitative observations are retained. This is not a current account statement, verified performance record, or independently revalidated audit.

# Binance USD-M 15m 快速筛选

- 生成时间：2026-07-03 [TIME] [VALUE]
- 数据源：Binance USD-M Futures public market data
- 交易边界：只读筛选，不下单；候选币必须人工复核止损、资金费、盘口和当前持仓。
- 经验约束：亏损集中币降权，出现过 `INSURANCE_CLEAR` 的 symbol 阻断，异常放量/ATR/RSI 追涨杀跌标记为 REVIEW。

## 候选列表

| Symbol | Strategy | Action | Dir | Score | VP | OB | 15m | 1h | Taker1m | Flow | RSI | ATR% | Reasons | Risk |
|---|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|
| `SOLUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,micro_capital_inflow,1m_micro_bull,trend_pullback_long,past_loser | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `XRPUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_buy_dominant([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,15m_taker_buy_dominant,trend_pullback_long | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `ETHUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence,order_book_ask_pressure |
| `BNBUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_sell_dominant([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,15m_taker_sell_dominant,trend_pullback_long | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `HYPEUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,1m_micro_bear | no_long_entry_trigger,volume_price_profile_adverse |
| `SLXUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,past_winner,order_book_bid_support | weak_recent_volume,[PRIVATE_ID],volume_price_divergence |
| `DOGEUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,1m_micro_bear,trend_pullback_long,past_loser | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |

## 阻断列表

| Symbol | Reason |
|---|---|
| `BTCUSDT` | 15m_ema_bull,1h_trend_up,trend_pullback_long,past_loser,insurance_clear_block / weak_recent_volume |

## 使用方式

- `WATCH_LONG` / `WATCH_SHORT` 只是候选，不是入场指令。
- `REVIEW_*` 表示有趋势信号但存在追涨、超跌、放量异常或 ATR 风险，必须降仓或跳过。
- `BLOCK` 表示历史复盘中有严重风控问题，完成单独复盘前不主动交易。

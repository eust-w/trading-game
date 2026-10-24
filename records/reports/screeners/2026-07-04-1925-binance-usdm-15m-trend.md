> Historical source record, sanitized for publication. Quantitative values, identifiers and private paths have been withheld. Dates and qualitative observations are retained. This is not a current account statement, verified performance record, or independently revalidated audit.

# Binance USD-M 15m 快速筛选

- 生成时间：2026-07-04 [TIME] [VALUE]
- 数据源：Binance USD-M Futures public market data
- 交易边界：只读筛选，不下单；候选币必须人工复核止损、资金费、盘口和当前持仓。
- 经验约束：亏损集中币降权，出现过 `INSURANCE_CLEAR` 的 symbol 阻断，异常放量/ATR/RSI 追涨杀跌标记为 REVIEW。

## 候选列表

| Symbol | Strategy | Action | Dir | Score | VP | OB | 15m | 1h | Taker1m | Flow | RSI | ATR% | Reasons | Risk |
|---|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|
| `LITUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,1m_micro_bear,trend_pullback_long | volume_price_profile_adverse |
| `GUAUSDT` | trend_pullback | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,1m_micro_bear,trend_pullback_short,volume_price_confirmed | weak_recent_volume,volume_price_divergence,order_book_wide_spread,order_book_bid_pressure |
| `GRAMUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | volume_price_profile_adverse,volume_price_divergence |
| `OUSDT` | breakout_momentum | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,1m_micro_bear,near_20bar_breakdown | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `RIFUSDT` | trend_following | WATCH_SHORT | SHORT | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,1m_micro_bear |  |
| `INJUSDT` | capital_flow | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,micro_capital_outflow,1m_micro_bear | weak_recent_volume,volume_price_divergence |
| `SLXUSDT` | trend_pullback | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,trend_pullback_short,negative_funding_long_bias,past_loser | weak_recent_volume,volume_price_divergence |
| `XMRUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,1m_micro_bear,trend_pullback_long,past_loser | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `AVAXUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | normal_volume_down_buy_dominant([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,15m_taker_buy_dominant,past_loser,volume_price_confirmed | volume_price_divergence |
| `GRASSUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | volume_price_profile_adverse,volume_price_divergence,order_book_ask_pressure |
| `FETUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | normal_volume_down_buy_dominant([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,15m_taker_buy_dominant,volatility_compression,past_loser,volume_price_confirmed | volume_price_divergence,order_book_bid_pressure |
| `NOMUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,1m_micro_bear,volume_price_confirmed | weak_recent_volume,volume_price_divergence,order_book_bid_pressure |
| `XRPUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | weak_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume,volume_price_profile_adverse,volume_price_divergence |
| `HMSTRUSDT` | capital_flow | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,micro_capital_inflow,1m_micro_bull | atr_too_wide,extended_above_ema21,volume_price_profile_adverse |
| `TAOUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,past_loser | volume_price_profile_adverse |
| `ENAUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,volume_confirm,15m_taker_buy_dominant,past_loser | volume_price_profile_adverse,volume_price_divergence |
| `HBARUSDT` | capital_flow | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_sell_dominant([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,micro_capital_outflow,1m_micro_bear,15m_taker_sell_dominant | no_long_entry_trigger,volume_price_profile_adverse,volume_price_divergence |
| `ONDOUSDT` | breakout_momentum | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,near_20bar_breakdown,past_loser | weak_recent_volume,volume_price_divergence |
| `REUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,1m_micro_bull,negative_funding_long_bias | volume_price_profile_adverse,volume_price_divergence |
| `BCHUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up | weak_recent_volume,volume_price_divergence |
| `BEATUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | normal_volume_up_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,volume_price_confirmed | [PRIVATE_ID],volume_price_divergence,order_book_ask_pressure |
| `NEARUSDT` | capital_flow | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_down_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,micro_capital_inflow | weak_recent_volume,volume_price_divergence |
| `TRUMPUSDT` | breakout_momentum | REVIEW_WATCH_SHORT | SHORT | [VALUE] | weak_volume_flat_taker_neutral([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up,near_20bar_breakdown | weak_recent_volume,volume_price_divergence |
| `MIRAUSDT` | countertrend_mean_reversion | REVIEW_WATCH_LONG | LONG | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=-/spr=- | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,[PRIVATE_ID],negative_funding_long_bias | extended_above_ema21,mean_reversion_countertrend,volume_price_profile_adverse,volume_price_divergence,order_book_unavailable |
| `ZKPUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [PRIVATE_ID]([VALUE]) | imb=[VALUE]/spr=[VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_up | [PRIVATE_ID],volume_price_divergence |

## 阻断列表

| Symbol | Reason |
|---|---|
| - | 无 |

## 使用方式

- `WATCH_LONG` / `WATCH_SHORT` 只是候选，不是入场指令。
- `REVIEW_*` 表示有趋势信号但存在追涨、超跌、放量异常或 ATR 风险，必须降仓或跳过。
- `BLOCK` 表示历史复盘中有严重风控问题，完成单独复盘前不主动交易。

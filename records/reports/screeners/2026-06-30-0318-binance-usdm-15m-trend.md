> Historical source record, sanitized for publication. Quantitative values, identifiers and private paths have been withheld. Dates and qualitative observations are retained. This is not a current account statement, verified performance record, or independently revalidated audit.

# Binance USD-M 15m 快速筛选

- 生成时间：2026-06-30 [TIME] [VALUE]
- 数据源：Binance USD-M Futures public market data
- 交易边界：只读筛选，不下单；候选币必须人工复核止损、资金费、盘口和当前持仓。
- 经验约束：亏损集中币降权，出现过 `INSURANCE_CLEAR` 的 symbol 阻断，异常放量/ATR/RSI 追涨杀跌标记为 REVIEW。

## 候选列表

| Symbol | Strategy | Action | Dir | Score | 15m Trend | 1h Trend | RSI | ATR% | Funding | 24h% | Reasons | Risk |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `OPUSDT` | trend_pullback | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long |  |
| `ORDIUSDT` | trend_pullback | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].646e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long |  |
| `ARBUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `ARXUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `AVAXUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].559e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `HYPEUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 5e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `SKYAIUSDT` | trend_pullback | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 5e-[VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,trend_pullback_short | weak_recent_volume |
| `UNIUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `MANTAUSDT` | trend_following | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,volume_confirm,past_winner | extended_below_ema21 |
| `1000SHIBUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `ADAUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].26e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `AGLDUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,negative_funding_long_bias | weak_recent_volume |
| `DOTUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].414e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `INUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `LTCUSDT` | breakout_momentum | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,near_20bar_breakout | weak_recent_volume |
| `PENGUUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].63e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `RIFUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `SUIUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `WIFUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 5e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `XLMUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |
| `JTOUSDT` | trend_pullback | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].753e-[VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,trend_pullback_short,past_loser,high_freq_loss | weak_recent_volume |
| `FARTCOINUSDT` | trend_pullback | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,trend_pullback_long | weak_recent_volume |
| `PUMPUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 5e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,past_loser | weak_recent_volume |
| `SOLUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,past_loser | weak_recent_volume |
| `BCHUSDT` | trend_following | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE].01e-[VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up | weak_recent_volume |

## 阻断列表

| Symbol | Reason |
|---|---|
| `BTCUSDT` | 15m_ema_bull,1h_trend_up,trend_pullback_long,past_loser,insurance_clear_block / weak_recent_volume |
| `BEATUSDT` | 1h_trend_up,past_major_loser,insurance_clear_block,high_freq_loss / weak_recent_volume |
| `龙虾USDT` | 15m_ema_bull,1h_trend_up,trend_pullback_long,past_major_loser,insurance_clear_block / weak_recent_volume |
| `RAVEUSDT` | 15m_ema_bull,1h_trend_up,past_loser,insurance_clear_block,high_freq_loss / weak_recent_volume |

## 使用方式

- `WATCH_LONG` / `WATCH_SHORT` 只是候选，不是入场指令。
- `REVIEW_*` 表示有趋势信号但存在追涨、超跌、放量异常或 ATR 风险，必须降仓或跳过。
- `BLOCK` 表示历史复盘中有严重风控问题，完成单独复盘前不主动交易。

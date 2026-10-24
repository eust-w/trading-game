> Historical source record, sanitized for publication. Quantitative values, identifiers and private paths have been withheld. Dates and qualitative observations are retained. This is not a current account statement, verified performance record, or independently revalidated audit.

# Binance USD-M 15m 快速筛选

- 生成时间：2026-06-30 [TIME] [VALUE]
- 数据源：Binance USD-M Futures public market data
- 交易边界：只读筛选，不下单；候选币必须人工复核止损、资金费、盘口和当前持仓。
- 经验约束：亏损集中币降权，出现过 `INSURANCE_CLEAR` 的 symbol 阻断，异常放量/ATR/RSI 追涨杀跌标记为 REVIEW。

## 候选列表

| Symbol | Action | Dir | Score | 15m Trend | 1h Trend | RSI | ATR% | VolRatio | 24h% | Reasons | Risk |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `BCHUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,near_20bar_breakout |  |
| `SUIUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `UNIUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `MANTAUSDT` | WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,past_winner |  |
| `SOLUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,past_loser |  |
| `ADAUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `AVAXUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `HYPEUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `NEARUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `XLMUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm |  |
| `XRPUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,near_20bar_breakout |  |
| `LINKUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,past_loser |  |
| `PUMPUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm,past_loser |  |
| `FILUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,volume_confirm |  |
| `SKYAIUSDT` | REVIEW_WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down | weak_recent_volume |
| `ETHUSDT` | REVIEW_WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,volume_confirm | long_chase_rsi |
| `1000PEPEUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,volume_confirm |  |
| `AAVEUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_up,past_loser |  |
| `BNBUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,volume_confirm |  |
| `TAOUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down,volume_confirm |  |
| `DOTUSDT` | WATCH_LONG | LONG | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bull,1h_trend_down |  |
| `JTOUSDT` | WATCH_SHORT | SHORT | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | [VALUE] | 15m_ema_bear,1h_trend_down,past_loser,high_freq_loss |  |

## 阻断列表

| Symbol | Reason |
|---|---|
| `BTCUSDT` | 15m_ema_bull,1h_trend_down,past_loser,insurance_clear_block /  |
| `BEATUSDT` | 15m_ema_bull,1h_trend_up,volume_confirm,past_major_loser,insurance_clear_block,high_freq_loss /  |
| `RAVEUSDT` | 15m_ema_bull,1h_trend_up,past_loser,insurance_clear_block,high_freq_loss / weak_recent_volume,atr_too_wide |

## 使用方式

- `WATCH_LONG` / `WATCH_SHORT` 只是候选，不是入场指令。
- `REVIEW_*` 表示有趋势信号但存在追涨、超跌、放量异常或 ATR 风险，必须降仓或跳过。
- `BLOCK` 表示历史复盘中有严重风控问题，完成单独复盘前不主动交易。

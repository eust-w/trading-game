# Quant

这里放简单量化、币种筛选、回测和策略实验。

建议先按这个顺序做：

1. 数据读取：只读公共 K 线、成交量、资金费率。
2. 指标计算：涨跌幅、ATR、RSI、均线、成交量放大、资金费率异常。
3. 币种筛选：输出候选币，不直接下单。
4. 回测：验证筛选条件在历史数据上的表现。
5. 风控模拟：加入手续费、滑点、止损、最大回撤。

## 推荐最小脚本

- `screeners/volume_momentum.py`: 成交量和动量筛选。
- `screeners/mean_reversion.py`: 超跌反弹筛选。
- `backtests/simple_signal_backtest.py`: 单信号回测。
- `lib/indicators.py`: 通用指标。
- `lib/data_loader.py`: 数据读取。

## 当前可用脚本

```bash
python3 quant/screeners/binance_usdm_15m_trend_screener.py
```

用途：

- 只读扫描 Binance USD-M USDT 永续合约。
- 命令行默认取 24h 成交额靠前且成交额超过 `30000000` USDT 的前 40 个 symbol，并发数 6；公开服务配置同样使用 40 个标的上限和 6 并发。
- 使用 15m K 线计算 EMA、RSI、ATR、成交量放大和 20 根 K 线突破/跌破位置。
- 使用 1h 趋势做方向过滤。
- 输出 `primary_strategy` 和 `strategy_tags`，当前覆盖趋势跟随、突破动量、趋势回踩、极端偏离回归、波动压缩和资金费率反向观察。
- 结合过去 30 天真实交易复盘，对亏损集中币降权，对出现过 `INSURANCE_CLEAR` 的币阻断。

输出：

```text
reports/screeners/YYYY-MM-DD-HHMM-binance-usdm-15m-trend.csv
reports/screeners/YYYY-MM-DD-HHMM-binance-usdm-15m-trend.md
```

扩大扫描范围：

```bash
python3 quant/screeners/binance_usdm_15m_trend_screener.py --max-symbols 80 --min-quote-volume 20000000 --concurrency 80
```

## 原则

- 筛选脚本默认只读，不碰交易接口。
- 每个脚本输出 CSV 或 Markdown 到 `reports/`。
- 回测必须记录参数，避免结果不可复现。

## 当前策略族

- `trend_following`: 15m EMA9/21/55 顺排，1h EMA21/55 同方向确认。
- `breakout_momentum`: 价格接近 20 根 15m K 线高点或低点，并结合趋势和成交量。
- `trend_pullback`: 1h 趋势仍在，15m 价格回到 EMA21 附近，避免追太远。
- `countertrend_mean_reversion`: RSI 和价格偏离 EMA21 都极端时，只作为 REVIEW 反转观察。
- `volatility_compression`: 布林宽度和 ATR 收缩后出现成交量确认，作为可能变盘观察。
- `funding_contrarian`: 资金费率过高偏向观察空头，资金费率过低偏向观察多头。

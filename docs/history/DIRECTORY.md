# Directory Guide

## `docs/`

- `principles/`: 长期有效的交易原则、风控原则、仓位原则。
- `postmortems/`: 重要亏损、盈利、错过机会的复盘。
- `playbooks/`: 可重复执行的打法，例如突破、回踩、趋势跟随、事件驱动。
- `market-notes/`: 对市场阶段、宏观、资金费率、情绪的观察。
- `glossary/`: 术语表和指标解释。

## `journal/`

- `daily/`: 每日盘前计划、盘中记录、盘后总结。
- `weekly/`: 周度统计、主要错误、下周重点。
- `monthly/`: 月度收益、最大回撤、策略有效性评估。

## `research/`

- `coins/`: 单个币种研究。
- `sectors/`: 赛道研究，例如 AI、RWA、L2、Meme、DePIN。
- `exchanges/`: 交易所规则、费用、合约限制、API 行为。
- `data-sources/`: 数据接口、字段说明、限频和数据质量记录。

## `quant/`

- `screeners/`: 币种筛选脚本，例如成交量、涨跌幅、波动率、资金费率、趋势强度。
- `backtests/`: 回测入口。
- `strategies/`: 策略规则实现。
- `notebooks/`: 临时探索和图表。
- `lib/`: 复用函数，例如指标计算、K线读取、费用模型。
- `tests/`: 单元测试和回归测试。

## `data/`

- `raw/`: 原始下载数据。
- `processed/`: 清洗后的数据。
- `external/`: 第三方导出数据。
- `samples/`: 可提交的小样本数据。

## `reports/`

- `screeners/`: 每次筛选输出。
- `backtests/`: 回测结果。
- `screens/`: 交易截图、K线截图、账户截图。

## `ops/`

- `checklists/`: 入场、加仓、止损、止盈、换仓检查清单。
- `runbooks/`: 固定操作流程，例如如何更新数据、如何跑筛选、如何复盘。

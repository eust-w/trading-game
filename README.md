# 交易游戏 / Trading Game

面向 Binance U 本位 USDT 永续合约的研究项目：把交易想法、行情筛选、模拟信号、风控实验与复盘记录放在一起，追踪规则如何形成、哪些条件失效、实现还缺什么。

这是从 Alpha Vault 整理出的公开源码快照。默认运行模拟信号；包含历史实盘执行与保护单模块，但不承诺收益，也未通过生产级实盘安全验收。本次公开发布没有连接私人账户、启动交易服务或执行交易。

## 快速开始

Python 3.12+。从仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock

# 查看命令帮助，不联网、不读取账户
python quant/screeners/binance_usdm_15m_trend_screener.py --help

# 只读公共行情筛选，不需要 API 密钥
python quant/screeners/binance_usdm_15m_trend_screener.py --max-symbols 10 --concurrency 3

# 单轮模拟信号，不启用实盘模式
python quant/services/paper_signal_service.py --once
```

公共行情需要网络可达；超时、限流或缓存回退应按报告中的状态解释。输出保存在被 Git 忽略的 `data/` 和 `reports/`。

## 内容导航

| 内容 | 入口 |
|---|---|
| 最初的单仓位策略及完整决策规则 | [原始设计](docs/strategy-spec.md) |
| 模块、数据流与执行边界 | [架构](docs/architecture.md) |
| 目标与当前代码的差异 | [实现限制](docs/implementation-status.md) |
| 行情因子、筛选与历史损失过滤 | [筛选器](quant/screeners/binance_usdm_15m_trend_screener.py) |
| 模拟信号、风险模型与事件记录 | [服务目录](quant/services/) |
| 成交复盘、月报、量价画像与服务诊断 | [工具脚本](scripts/) |
| 方法论、交易教训及复盘模板 | [文档](docs/) |
| 脱敏历史报告、运行笔记和原始记录索引 | [记录档案](records/README.md) |
| 公开范围与脱敏规则 | [发布说明](docs/publication.md) |

## 运行边界

默认配置为 `intent_only`，一个模拟仓位，市场扫描间隔 60 秒、40 个候选上限、6 个并发请求；不需要私人 API 凭据或 MongoDB。

历史实盘模块会访问真实 Binance U 本位账户。`--confirm-live` 包含保护单维护，不能理解为“所有写操作都会弹窗确认”；`scripts/restart_paper_signal_service.py` 会重启历史实盘模式服务。不要将这些命令用作快速开始。具体风险见[实现限制](docs/implementation-status.md)。

公开配置的 `max_live_positions=1` 是默认值，不是不可绕过的全局不变量。原始设计中的净风险收益比、三档止盈、4h 确认和单轮一个主要动作尚未全部实现。历史记录和通过的单元测试均不能证明这些目标已经满足。

## 测试

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
python scripts/check_publication.py
python -m pip_audit -r requirements-dev.lock
```

测试默认禁止真实 HTTP 请求，只使用离线数据和替身。依赖锁文件由 `uv pip compile` 生成。可选 MongoDB 依赖已包含在开发锁文件中，用于审计和测试。

## English

Trading Game is a Binance USD-M research snapshot with market screening, paper signals, risk models, execution/protection experiments, trade-review tools and a sanitized engineering archive. The default configuration is paper-only. Legacy live modules remain available for source review; they are not production-certified. The original single-position strategy is a design specification, not a claim that every rule is implemented. Raw account exports, credentials and infrastructure identifiers are excluded.

MIT licensed. See [LICENSE](LICENSE).

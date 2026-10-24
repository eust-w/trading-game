# Alpha Vault

用于沉淀加密货币交易经验、复盘、币种研究、简单量化筛选和回测实验。

## 目录结构

```text
.
├── docs/                  # 方法论、规则、术语、复盘框架
├── journal/               # 日/周/月交易记录
├── research/              # 币种、赛道、交易所、数据源研究
├── quant/                 # 量化筛选、回测、策略实验代码
├── data/                  # 本地数据缓存，不提交真实敏感数据
├── scripts/               # 一次性工具脚本和命令入口
├── config/                # 非敏感配置样例
├── reports/               # 筛选、回测、截图等输出结果
└── ops/                   # 操作手册、检查清单、风控运行规则
```

## 建议工作流

1. 交易前：在 `ops/checklists/` 维护入场检查清单，在 `research/coins/` 记录币种判断。
2. 交易中：只记录事实，不改写计划；关键截图放到 `reports/screens/`。
3. 交易后：在 `docs/postmortems/` 或 `journal/daily/` 复盘盈亏、执行偏差和下一次规则。
4. 筛选币种：在 `quant/screeners/` 写只读筛选逻辑，输出到 `reports/screeners/`。
5. 回测策略：在 `quant/backtests/` 写回测脚本，输出到 `reports/backtests/`。

## 安全边界

- 不提交 API Key、私钥、助记词、交易所密码、验证码。
- 交易所凭据只通过环境变量或本机私密文件读取。
- 默认先做只读数据分析；自动下单必须单独设计风控和确认流程。
- `data/raw/`、`reports/` 可能包含账户或交易细节，提交前必须检查。

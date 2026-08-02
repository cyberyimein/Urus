# Urus Stage 2 实现说明

## 当前基线

- 单一 FastAPI 应用和单一 Vue 应用，前后端独立启动。
- SQLAlchemy 最小 `Run`、`StepRun`、`Snapshot` 模型与可从空库执行的 Alembic migration。
- 七个可替换的 workflow step：`1a`、`1b`、`2`、`3a`、`3b`、`4`、`5`。
- `1B`、`3B` 的条件分支和 mock 事件开关；未命中条件时正常 `skipped`。
- 第 2 步可使用 Moomoo LV1 快照采集期权链；不会调用订阅接口，并在采集前后核对订阅额度。
- 按到期日聚合 DEX、GEX、Gamma Wall、Max Pain、Expected Move，并保留逐行权价明细。
- mock Anomalo、行情与决策 adapter 仍保持隔离，不扩大 Stage 2 的修改范围。
- API 错误统一为 `{ error: { code, message, details? } }`，运行、步骤和 snapshot 均可查询。
- Dashboard、Options、Runs、Run Detail 页面；Options 是独立的 Stage 1 风格数据验证工作台。
- `OutputStep` 生成小型 frontend read model，snapshot 只保存 JSON，不保存大规模行情。

## 关键技术选择

- 后端使用同步 SQLAlchemy session，保持本轮 mock 流程简单；数据库边界通过 repository 收口。
- 运行同步完成，避免为轻量占位流程引入队列；步骤接口保持独立，后续可替换为真实实现。
- 前端只有一个 API client；组件不直接访问数据库或 provider。
- 视觉变量独立定义在 Urus CSS 中。

## 已知限制

VEX/Vanna、做市商真实持仓方向、开平仓识别、组合腿识别、逐笔期权历史、宏观数据、个股技术指标、事件检索、AI prompt、交易日历和自动调度不属于本分支。开发验证只允许 QQQ、INTC；生产配置可扩展到 SPY、SMH、IGV 及自选股。

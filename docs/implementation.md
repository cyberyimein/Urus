# Urus Stage 2 实现说明

## 框架与阶段 1A

- 单一 FastAPI 应用和单一 Vue 应用，前后端独立启动。
- SQLAlchemy 最小 `Run`、`StepRun`、`Snapshot` 模型与一份可从空库执行的 Alembic migration。
- 七个可替换的 workflow step：`1a`、`1b`、`2`、`3a`、`3b`、`4`、`5`。
- `1B`、`3B` 的条件分支和 mock 事件开关；未命中条件时正常 `skipped`。
- 第 2 步可使用 Moomoo LV1 快照采集期权链；不会调用订阅接口，并在采集前后核对订阅额度。
- 按到期日聚合 DEX、GEX、Gamma Wall、Max Pain、Expected Move，并保留逐行权价明细、建模正负 Gamma 区间与符号切换位。
- mock Anomalo 与决策 adapter 仍保持隔离。
- API 错误统一为 `{ error: { code, message, details? } }`，运行、步骤和 snapshot 均可查询。
- Dashboard、Runs、Run Detail 页面；期权作为 Dashboard 的 `期权 / 2` Tab，直接复用 Stage 1 页面骨架。
- `OutputStep` 生成小型 frontend read model，snapshot 只保存 JSON，不保存大规模行情。
- 阶段 1A 的 FRED 与 Yahoo 日频宏观 adapter 已独立接入；Yahoo chart 每次运行请求 `^VIX/^TNX/^TYX`，可用时作为 VIX/10Y/30Y 选定值；FRED 提供官方 2Y，并保留 VIX/10Y/30Y 交叉值；2s10s 使用选定的 10Y 与 FRED 2Y 计算，不伪装成官方 2Y 数据。
- 所有 API 时间统一按带 `+00:00` offset 的 UTC 输出；前端固定显示 `JST`，不会把数据库中 SQLite 取回的 naive UTC 当作浏览器本地时间。
- 阶段 1A 已增加懒加载的 Moomoo/OpenD adapter：一次批量读取配置的 ETF 代理快照、交易时段、盘前/盘后字段，并读取 QQQ 的最多 260 根日线摘要指标。美国指数不通过 Moomoo 请求，直接 VIX 的策略跳过状态保留在 read model。
- QQQ 日线摘要通过共享技术指标模块计算 20 日年化实现波动率、ATR14、ATR14%、布林带 20/2；每项保留 `as_of`、`sample_count`、`source`，后续 3A 可复用同一模块。
- OpenD 真实数据和未实现的 placeholder/unavailable 步骤在 read model 中分开标记；OpenD 连接失败会保留为失败步骤和错误 snapshot。

## 关键技术选择

- 后端使用同步 SQLAlchemy session，保持本轮 mock 流程简单；数据库边界通过 repository 收口。
- 运行同步完成，避免为轻量占位流程引入队列；步骤接口保持独立，后续可替换为异步/后台执行。
- `OutputStep` 生成小型 frontend read model，snapshot 只保存 JSON，不保存真实大规模行情。
- 前端只有一个 API client；组件不直接访问数据库、Moomoo 或 Anomalo，也不计算交易指标。
- 视觉变量独立定义在 Urus CSS 中，参考 sibling-project 的深橄榄色、暖白和等宽元数据风格，但不共享运行时文件。

## 已知限制

- 目前阶段 1A 的 QQQ/代理 ETF 快照、QQQ 日线指标和 FRED/Yahoo 宏观链路已接入；阶段 2 的期权快照与结构计算在启用 Moomoo 时为 live，未启用时为 placeholder。1B/3B 仍按条件跳过，4 是 placeholder，3A 是 unavailable；没有事件日历、INTC 个股模块真实采集、账户风险、AI prompt 或自动调度。
- VEX/Vanna、做市商真实持仓方向、开平仓识别、组合腿识别和逐笔期权历史不属于本阶段。期权范围包含 SPY、QQQ、SMH、IGV 与配置的 15 个上市个股关注标的；SPCX 是私募标的，明确不发起期权链请求。正负 Gamma 区间基于 Call 正、Put 负的净 GEX 模型，不代表已知做市商净仓位。
- FRED 日频宏观源需要 `FRED_ENABLED=true` 才会请求；Yahoo 每次运行请求需要 `YAHOO_ENABLED=true`。市场广度、5 分钟历史、5 年日线归档、60/120/252 日收益、行业热力图和实时订阅、逐笔、盘口、期货属于延期项；交易日历和提前收盘在启用自动调度前必须补齐。
- 本地启动时会 `create_all` 以降低首次运行摩擦；部署和版本演进仍应执行 Alembic migration。
- 没有登录、权限、多租户、Sentry、Prometheus、容器编排或移动端完整适配。
- `MOOMOO_ENABLED=true` 才会启用阶段 1A OpenD；Anomalo 即使配置为 enabled 也没有真实 HTTP wiring。

## 下一阶段接入点

阶段 1A 与阶段 2 已在同一工作流和 Dashboard 中合流。下一步进入 3A，接入 INTC 真实行情并复用同一技术指标模块，另计算个股相对 QQQ、行业 ETF 相对 QQQ。交易日历与提前收盘应在启用每天两次自动调度前补齐。

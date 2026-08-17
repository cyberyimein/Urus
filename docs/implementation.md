# Urus Stage 2 / Stage 3A 实现说明

## 框架与阶段 1A

- 单一 FastAPI 应用和单一 Vue 应用，前后端独立启动。
- SQLAlchemy 保存 `Run`、`StepRun`、`Snapshot`，并用规范化期权表持久化分析批次、标的、到期日、原始合约、Gamma Profile 点位与 Gamma Flip；Alembic 可从空库升级到当前 schema。
- 七个可替换的 workflow step：`1a`、`1b`、`2`、`3a`、`3b`、`4`、`5`。
- `1B`、`3B` 的条件分支和 mock 事件开关；未命中条件时正常 `skipped`。
- 第 2 步可使用 Moomoo LV1 快照采集期权链；不会调用订阅接口，并在采集前后核对订阅额度。
- 按到期日使用完整期权链聚合 DEX、GEX、Gamma Wall、Max Pain、Expected Move，再将前端逐行权价明细限制在现价附近；保留建模正负 Gamma 行权价区间与符号切换位。
- Spot Gamma Profile 使用每份合约的 IV、OI、Strike 与剩余时间在假设现价网格上重算 Black–Scholes Gamma，并插值所有零交叉；当前利率、股息率、范围和点数均为显式配置，主 Gamma Flip 取离现价最近的零点。
- mock Anomalo 与决策 adapter 仍保持隔离。
- API 错误统一为 `{ error: { code, message, details? } }`，运行、步骤和 snapshot 均可查询。
- Dashboard、Runs、Run Detail 页面；期权作为 Dashboard 的 `期权 / 2` Tab，直接复用 Stage 1 页面骨架。
- `OutputStep` 生成小型 frontend read model；snapshot JSON 仍是前端读取模型，大规模期权输入与计算结果单独保存在 SQLite 规范化表中。
- 阶段 1A 的 FRED 与 Yahoo 日频宏观 adapter 已独立接入；Yahoo chart 每次运行请求 `^VIX/^TNX/^TYX`，可用时作为 VIX/10Y/30Y 选定值；FRED 提供官方 2Y，并保留 VIX/10Y/30Y 交叉值；2s10s 使用选定的 10Y 与 FRED 2Y 计算，不伪装成官方 2Y 数据。
- 所有 API 时间统一按带 `+00:00` offset 的 UTC 输出；前端固定显示 `JST`，不会把数据库中 SQLite 取回的 naive UTC 当作浏览器本地时间。
- 阶段 1A 已增加懒加载的 Moomoo/OpenD adapter：一次批量读取配置的 ETF 代理快照、交易时段、盘前/盘后字段，并读取 QQQ 的最多 260 根日线摘要指标。美国指数不通过 Moomoo 请求，直接 VIX 的策略跳过状态保留在 read model。
- QQQ 日线摘要通过共享技术指标模块计算收益窗口、移动平均、实现波动率、ATR14、ATR14%、布林带 20/1、20/2、20/3 与带宽；同时计算 MACD(12,26,9) 的 DIF/DEA/柱体、交叉和动量、RSI14，以及成交量 Effort vs Result 信号。`rsi_context` 进一步用此前 20/60 日区间、均线、MACD、成交量、收盘位置和 ATR 区分高位突破/衰竭与低位下跌/反转；它是上下文证据，不直接生成买卖指令。每项保留 `as_of`、`sample_count`、`source`；本轮新结果的技术特征版本为 `technical_v3`。
- 阶段 3A 复用同一 Moomoo/OpenD adapter，以 QQQ 作为基准批量采集全量 `INSTRUMENT_VALIDATION_SYMBOLS`（默认包含 SPY、SMH、IGV 与 15 个公开关注股）。每个标的返回快照、复权日线、1/5/20/60/120/252 日收益、MA10/20/50/100/200、波动/ATR/多轨布林、MACD、量价信号以及相对 QQQ 收益、Beta、相关性；采集前后记录股票订阅和历史 K 线额度。
- 3A 前端按“ETF、半导体、光概念、SaaS、大科技、航天与新兴”主题 Tab 展示摘要表；SMH 同时出现在“ETF”和“半导体”两个分区，NOK 归入“光概念”，NOW/ORCL 归入“SaaS”，不再单列通信。完整字段按标的折叠展开。详情包含最新完成 K 线 OHLCV、全部收益/均线/波动窗口、多轨布林、MACD、Effort vs Result 与相对 QQQ 指标。原始逐日 K 线仍以 SQLite 归档为主，不在主表逐行铺开。
- 行情模型同时保留正规交易 `regular_price`、盘前价和盘后价；前端不再把“常规价”和“扩展时段价”混成一个数。布林带各偏差轨道的上轨、中轨、下轨和 `%B`、20/2 带宽均保存在技术指标结果中。
- 阶段 3A 的 SQLite migration `0003_instrument_technical_persistence` 新增分析批次、标的快照和逐日 K 线表，原始日线与 frontend snapshot 在同一事务保存，read model 仅暴露公开字段，不泄露内部持久化 payload。
- OpenD 真实数据和未实现的 placeholder/unavailable 步骤在 read model 中分开标记；OpenD 连接失败会保留为失败步骤和错误 snapshot。

## 关键技术选择

- 后端使用同步 SQLAlchemy session，保持本轮 mock 流程简单；数据库边界通过 repository 收口。
- 运行同步完成，避免为轻量占位流程引入队列；步骤接口保持独立，后续可替换为异步/后台执行。
- `OutputStep` 生成小型 frontend read model；repository 在同一事务中保存 snapshot 与对应的规范化期权数据，任一写入失败时整批回滚。
- 前端只有一个 API client；组件不直接访问数据库、Moomoo 或 Anomalo，也不计算交易指标。
- 视觉变量独立定义在 Urus CSS 中，采用深橄榄色、暖白和等宽元数据风格，但不共享外部项目运行时文件。

## 已知限制

- 目前阶段 1A 的 QQQ/代理 ETF 快照、QQQ 日线指标和 FRED/Yahoo 宏观链路已接入；阶段 2 的期权快照与结构计算在启用 Moomoo 时为 live，未启用时为 placeholder；阶段 3A 已从 QQQ/INTC/SMH 技术验证扩展到配置的全量核心 ETF 与公开关注股。1B/3B 仍按条件跳过，4 是 placeholder；没有事件日历、个股财务与事件、账户风险、AI prompt 或自动调度。
- 动态利率/股息率、VEX/Vanna、做市商真实持仓方向、开平仓识别、组合腿识别和逐笔期权历史不属于本阶段。期权范围包含 SPY、QQQ、SMH、IGV 与配置的 15 个上市个股关注标的；SPCX 是私募标的，明确不发起期权链请求。正负 Gamma 与 Spot Gamma Profile 都基于 Call 正、Put 负的持仓方向假设，不代表已知做市商净仓位。
- MACD 与 Effort vs Result 是收盘日线完成后的描述性信号：放量/缩量阈值、宽幅阈值和单日涨跌阈值均记录在指标 payload 中；`volume_effort_result.combination` 保留放量/正常量/缩量 × 上涨/下跌/横盘的完整组合，前端不再把中性组合隐去。不构成交易建议；成交量缺失时单独标记为 `unavailable`，不伪造信号。
- RSI 复合状态只使用已完成日线，突破/跌破比较区间显式排除当前 K 线，避免把当天自身计入此前高低点。AI 数据包保留 `rsi_context` 的分类、延续/反转分及触发 flags，同时提示词要求结合相对 QQQ 强弱、市场/题材环境、事件风险和质量，禁止把单一分类直接映射为交易动作。
- FRED 日频宏观源需要 `FRED_ENABLED=true` 才会请求；Yahoo 每次运行请求需要 `YAHOO_ENABLED=true`。市场广度、5 分钟历史、5 年日线归档、行业热力图和实时订阅、逐笔、盘口、期货属于延期项；自动调度使用 `exchange-calendars` 的 `XNYS` 交易日历，并处理日历定义的提前收盘。
- 本地启动时会 `create_all` 以降低首次运行摩擦；部署和版本演进仍应执行 Alembic migration。
- 没有登录、权限、多租户、Sentry、Prometheus、容器编排或移动端完整适配。
- `MOOMOO_ENABLED=true` 才会启用阶段 1A OpenD；Anomalo 即使配置为 enabled 也没有真实 HTTP wiring。

## Spot Gamma Profile 精度改进（后续）

当前模型版本为 `spot_gamma_v1`，使用采集批次中显式记录的固定无风险利率、固定股息率、Profile 范围与点数。SQLite 已保留每份期权合约的 Strike、OI、IV、报价、Delta、Gamma、到期日、合约乘数和采集时间，因此后续可以在不重新抓取历史链的情况下，用新模型重新计算并对比结果。本阶段只完成持久化，不实现以下精度扩展：

- 按采集时间匹配的动态无风险利率，并保存利率来源、期限与 `as_of`。
- 按标的和除息日匹配的动态股息率/离散股息，区分 ETF 分配与个股股息。
- 0DTE 使用精确剩余秒数、交易日历和提前收盘时间，不再依赖最短一小时的统一下限。
- 统一 Moomoo IV 的单位、缺失值和异常值规则，并记录数据清洗版本。
- 为重算结果保留模型版本和参数版本；与 OptionCharts 对比时必须对齐标的、到期范围、OI 截止时间、现价与采集时间。
- 当前 Call 正、Put 负仍是持仓方向假设；在缺少做市商真实净仓与开平仓数据时，Gamma Flip 只能解释为模型结果。

## 阶段 3A 验收记录

- 真实 OpenD 验证时间：2026-08-03；端点为配置的 `MOOMOO_HOST:MOOMOO_PORT`。
- 请求集合为 `QQQ + INTC + SMH`，三只标的均返回快照和 357 根复权日线，技术指标质量为 `ok`；相对 QQQ 的 5/20/60 日收益、20/60 日 Beta 和相关性均按共同交易日对齐计算。
- 采集前后股票实时订阅额度保持不变；历史 K 线额度只按 OpenD 实际新增的历史请求变化，结果写入 `quota_audit`，不会为了 3A 建立实时订阅。
- 前端 Dashboard 的“个股与行业 ETF”页按主题 Tab 显示各组 live 数量；整次工作流仍可能显示 `mixed`，原因是 1B/3B/4 尚未实现，不是 3A 数据不可用。

## 下一阶段接入点

阶段 1A、阶段 2 与阶段 3A 已在同一工作流和 Dashboard 中合流。下一步为 3B 个股事件/财务摘要、更多关注列表标的和行业基准；自动调度的交易日历与提前收盘边界已补齐。

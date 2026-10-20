# Urus 盘中动量与日线指标增强：分阶段开发计划

状态：P0–P7 最小切片及同库去重／离峰维护收尾验收通过。用户收盘后重新授权继续，2026-10-17 JST 已部署；backend 557 项、collector 157 项及前端构建通过。真实历史策略效果与统一21＋2全窗口稳态另行验证。\
版本：v0.5\
日期：2026-10-17\
策略基线：[盘中强弱与动量预警策略设计 v0.5](intraday-momentum-strategy-design.md)。\
新增扩展：CTA 风格的系统性趋势风险，安排在核心盘中能力之后。

2026-10-19 开盘后发现 G1 报价 NaN 会连带丢失分钟线引用；已按用户要求进行本地最小修复，尚未部署，现网仍运行10月17日版本。修复与验收见 [G1 NaN 待部署说明](reviews/intraday-quote-nan-fix-20261019.md)。本轮不扩入历史回补，交易时间不更换运行环境。

同日新增本地前端修正：日内页四个接口统一按5分钟钟表边界读取，隐藏标签页跳过，恢复补读；MA5与蜡烛中心对齐。该UI修改也未部署，若与G1补丁一并发布，需同时构建Urus API内的前端资源及collector。见 [刷新与MA5修正](reviews/intraday-refresh-ui-fix-20261019.md)。

范围调整（2026-10-15）：暂不开发通知系统，包括外部推送、浏览器通知、弹窗、声音、已读／未读和通知发送队列。保留后台信号计算、事件持久化、网页自动读取与历史筛选的阶段目标。首个盘中页面先只读已有采集存储，发布 worker 是否必要由后续持久化需求决定。

### 最新运行环境状态（2026-10-17，收盘后）

此前临近开盘禁令已遵守；用户最新“已经收盘了 可以继续”重新授权收盘后部署。API 与同一个 Urus collector 已切换，原六项任务不变，Eoflora 应用容器未替换。已保留旧容器、原配置和今天的一致性备份。详见 [收盘后部署记录](reviews/intraday-deployment-20261017.md)。下一交易日首槽才开始产生真实盘中数据，不手动制造历史或补跑盘中槽。

### 最终本地结果与代码设计（2026-10-16）

同库规范化、跨日指标恢复及维护边界已完成；root 验收 backend 551 项、luna-builder 独立验收 collector 157 项全部通过，前端 vue-tsc＋Vite 通过。最终源码身份、额度／性能证据及容量限制见 [最终本地验收](reviews/intraday-normalized-storage-acceptance-20261016.md)。

| 责任 | 代码位置 | 已实现内容 |
| --- | --- | --- |
| 同库存储 | `urus-moomoo/.../storage/equity_history.py`、`storage/sqlite.py` | 去重bar修订、quote batch、compact refs、2／21会话保留与容量门控 |
| 分组与优先级 | `urus-moomoo/.../services/scheduler.py`、`runtime.py` | 1＋3＋1、45秒provider预算、同cutoff、失败不借旧槽、Eoflora优先 |
| 本地维护 | 同一scheduler/runtime | 开盘前90分钟独立维护，开盘前30分钟硬截止，事务回滚及pending |
| 读取与指标 | `backend/app/intraday/references.py`、`history_features.py` | 只读按watermark/hash恢复，MACD/MA5/ATR/RVOL跨日输入，无长读事务阻塞EOF提交 |
| 页面和事件 | `backend/app/api/intraday.py`、`event_service.py`、`event_retention.py` | 页面只读、事件消费、2会话事件保留，保护活动setup |

- 继续复用既有 urus-moomoo 同一调度进程与 collector.sqlite。无手动日内采集、无新增采集容器或跨容器锁服务。其他既有API主动采集的全局协调不扩入本轮范围。
- 每根5m K收盘T后，T＋60／120／180秒三组采集，T＋240秒仅本地合并；第五分钟股票provider请求留空。业务A/B/C/D与执行G1/G2/G3分开，超时不追赶，EOF任一到期失败待重试则股票让步。
- 原始股票证据／报价／事件2个真实交易会话；5m指标输入21会话。真实修订保留旧版本供活动引用，过期缺失明确不可用。不清理legacy、Eoflora、期权或日线。
- 维护通过新的 `maintain_intraday_equities`＋`pre-open`任务实现，原6任务结构不改。整个dry-run／fresh apply受开盘前30分钟截止保护，删除与成功日期同事务；失败pending，不占用盘中股票窗口，也不阻断EOF。当地交易所开盘时间随DST变化，不能固定东京钟点。
- MACD需100根连续有效5m预热；RVOL仅此前20日同槽，至少10个有效日。日常积累为主，不无界回补，不把缺数据变成零或正式信号。
- 指标150MiB／raw512MiB／股票逻辑1GiB为门控预算；表／索引实际页、共享主库和WAL另测。100股两日完整fixture约88MiB，23日5m＋2日1m的bars-only约184MiB，统一21＋2全窗口长测未完成，不把两组相加冒充实测。详见 [容量记录](reviews/intraday-storage-budget-20261016.md)。
- 预检时46代码全部已在历史quota窗口，used46／remain54，三组15／16／15股真实请求12.661／11.818／11.291秒；新增代码0。该证据有时间边界，未来启用前需刷新。
- **用户现在禁止部署。** 以下阶段记录保留实施历史；不得使用已经撤回的条件部署许可自动上线。

### 本轮实施约束与简化（2026-10-15）

用户指定执行 chat `01a13ecd-b453-71d1-9496-545e6fa652fd` 负责编程，主协调 chat 负责审查与验收，`luna-builder` 子 agent 负责测试。共享工作区的既有改动必须保留，阶段按测试先行、实现、独立验收推进。

**今天是交易日：未经用户针对具体操作的明确指示，不部署、不重启、不替换运行容器，不修改生产配置或执行生产迁移。**本轮使用本地离线数据和隔离测试库，不连接生产 OpenD 进行新采集。

- 从 P0/P1 日线 MACD 三层数据开始，每阶段通过后再继续下一阶段；实施状态以验收记录为准。
- 优先复用现有指标、JSON 输出、投影、查询和页面。不为计划中的文件清单机械创建模块，也不提前建立通用指标框架。
- P0 只补当前切片必要的公式与契约；盘中运行、队列和 ORM 等定义延至实际需要的阶段。
- P1 暂不新增数据库表或自动历史补算服务。新计算生成新增字段，旧冻结证据保留原样，字段缺失展示“未计算”；历史重新生成另行明确操作。
- 版本更新必须保留历史结果读取能力，不能因全局 `FEATURE_VERSION` 变化使旧观察组和截面整体不可用。
- P2 先验证现有存储和发布流程能否复用，再决定是否需要新表、worker 或接口。数据契约、时间质量、准入与幂等要求不因简化而省略。
- 下文新增文件、类型和表名是职责设计建议，只有存在实际行为和必要性时才创建；以最小可用交付为验收重点。
- P3 首个切片采用已有 sidecar SQLite 的只读边界，读取最新股票采集槽并进行有界、确定性的纯计算投影。此切片替代下文原建议的 HTTP 摄取、ORM 镜像和常驻指标 worker；GET 不写库、不访问 provider。待事件持久化或回放确有需要时再增加写入链路。

### 已完成的本地验收切片

- 2026-10-16 部署前新增分时切片通过：现有 urus-moomoo 单调度进程内 1＋3＋1、Eoflora 各到期槽优先、同 cutoff、T＋240 本地整槽发布、缺片 partial 与重启幂等；独立真实 Runtime→Store→P3 集成 7 项、collector 全量 119 项、backend 全量 526 项和 frontend 构建通过。所有测试均为本地隔离，生产仅读额度与既有 Eoflora 输出，并做少量已占额度 SPY 历史比较。21 日历史／2 日保留／容量保护仍未实现，尚不部署。日内无手动采集入口，不新增采集容器或锁服务。见 [部署前验收结论](reviews/intraday-predeploy-verdict-20261016.md)。

- 2026-10-16：P7 最小离线回放与本地收尾通过；P7 最终 24 项、P4–P7 联合 138 项通过，后端全量 518 项（最后两条审计测试加入前执行）、采集器全量 70 项通过，最终前端构建通过。新增审计测试加入后 P7 及阶段联合复验通过。见 [P7 协调验收](reviews/intraday-p7-acceptance-20261016.md)。真实历史效果与生产容量未验收，所有部署／启用／生产 shadow 均未执行。

- 2026-10-16：P6 最小 CTA 风格代理切片通过；纯模型 24 项、service/API 26 项，与 P4/P5 联合 114 项通过，前端构建及隔离 Playwright 通过。包含真实本地 P2→P3→P6 契约桥接、短历史分层和缺主库不建库。见 [P6 协调验收](reviews/systematic-risk-p6-acceptance-20261016.md)。该切片不包括 P7，后续状态见上方最新记录。

- 2026-10-16：P4 事件与 P5 最小结构策略切片通过本地联合验收，后端 49＋15＝64 项通过，前端构建及隔离 Playwright 通过。日内页已有事件面板、S1–S8 策略筛选、冻结线、父事件依据和当日简化九转说明；终态清线、漏槽恢复、未来不可见和真实 JSON 持久化有验证。见 [P4 协调验收](reviews/intraday-p4-acceptance-20261016.md) 与 [P5 独立测试](reviews/intraday-p5-test-report-20261016.md)。该切片不包括 P6/P7，后续状态见上方最新记录；所有新运行入口默认关闭，未部署。

- 2026-10-15：P0/P1 的日线 MACD H／ΔH／Δ²H 最小切片通过；新增验收 20 项、既有相关回归 75 项、前端构建及 10 项本地 Playwright 流程通过。见 [阶段验收记录](reviews/intraday-momentum-p1-acceptance-20261015.md)。未部署，后续盘中阶段尚未计为完成。
- 同日补充需求已通过本地验收：个股详情增加 H／ΔH／Δ²H 正负状态与过零历史标记，扩展版本 v2；新增标记测试 8 项，与前轮合计 28 项通过，既有回归 75 项及隔离 Playwright 通过。该切片当时不含独立日内 UI，后续已由 P3/P4 补齐。
- 用户再次要求继续作业并强调交易时间不动部署环境：当前推进 P2 的独立股票分组采集最小切片，先复用现有原始契约与 SQLite，暂不搭建通用摄取／发布框架。
- P2 本地切片已验收：股票独立采集、400 分批、额度准入、D 轮换、共享节流、槽位幂等与明确分钟区间；新增验收 18 项、采集器回归 52 项、后端额度／期权回归 23 项通过。见 [P2 验收记录](reviews/intraday-equities-p2-acceptance-20261015.md)。真实分钟时间边界和账户额度未验证；未启用或部署。下一步为本节收紧后的 P3 只读投影和日内页面。
- P3 最小切片已验收：只读股票投影、独立 `/intraday` 页面、蜡烛／MA5、MACD 三层及图上标记、ATR／VWAP／RS、覆盖与延迟状态、60 秒只读刷新。后端验收 39 项、相关回归 103 项、前端构建与严格隔离 Playwright 通过。见 [P3 验收记录](reviews/intraday-p3-acceptance-20261015.md)。该切片当时不含后续事件策略；当日数据的 MACD 100 根预热及跨日 RVOL 基线仍未补齐，未部署。
- 2026-10-16：按用户要求继续 P3 后续工作，P4/P5 结果见上方最新记录。部署环境继续冻结，不因日期变化自行解除限制。

## 1. 交付原则与依赖

先让日 K 观察获得 MACD 三层数据，再完成股票盘中事实链路，最后逐步增加事件策略。每阶段必须能独立验收、关闭新功能并保留已有观察能力。

```text
P0 契约与版本
  └─ P1 共享 MACD 核心 + 日线展示
       └─ P2 分组采集与额度预算
            └─ P3 盘中指标与强弱投影
                 └─ P4 MA5 / 动量 / 急跌事件与网页
                      └─ P5 突破 / A-V / 九转
                           └─ P6 CTA 风格趋势风险
                                └─ P7 回放评估、完整验收与上线
```

- P0–P1 是第一份可交付成果：用户在现有日线页面看到 H、ΔH、Δ²H。
- P2–P4 是可用的盘中 MVP：大盘、板块、重点股票的强弱、MA5 与异动预警。
- P5 扩展价格结构；P6 是独立可关闭的背景模块，不阻塞盘中 MVP。
- 每阶段先写后端／采集器行为测试，再实现。前端不新增单元测试；必要交互验证使用 Playwright 脚本，不使用 Computer Use 模拟点击。
- 开发中运行改动相关测试；系统级测试与容量验证集中在 P7，避免开工前执行全套测试。
- 使用当前工作区已有改动作为上下文，不回滚其他工作。实际开始实施时重新检查代码和部署版本，本文路径是定位入口，不是可直接套用的补丁。
- 额度、任务范围与行情权限在实施时重新查询。2026-10-14 的 51 已用／49 剩余仅用于解释预算，不能作为部署配置。

## 2. 当前代码基础与已确认差异

### 非 MACD 数据优先级（2026-10-15 补充）

| 数据 | 要展示的定制量 | 日 K／盘中口径 | 阶段 |
| --- | --- | --- | --- |
| 均线位置 | 价格距离、均线斜率、站回／跌破、连续守住根数 | 日线复用现有 MA20/50/200；盘中优先 MA5 | P3–P4；日线现有展示保持兼容 |
| 相对强弱 RS | 相对基准收益、优势扩大／缩小、组内排名 | 日线复用既有窗口；盘中 RS15/RS30 | P3 |
| 成交量 | RVOL、放量上涨／下跌、缩量回踩 | 日线相对过去完整日均量；盘中相对历史相同时段 | P3–P4 |
| VWAP | 价格距离、收复／失守、持续位于上／下方时间 | 当前范围为 session 内指标，不直接当日线 MA 使用 | P3–P4 |
| 波动 | ATR%、本根涨跌／ATR、波幅扩张 | 标明日线 SMA ATR 与盘中 Wilder ATR 区别 | P3–P4 |
| 价格结构 | 距前高低点／区间边界距离、突破与回撤幅度 | 昨日高低／日线窗口与盘中 ORB／局部区间分别标记 | P5 |
| 广度 | 上涨、VWAP 上方和 RS 改善比例及其变化 | 仅表示观察池；附有效样本和共同分母 | P3 |
| RSI、ADX/DMI、布林带宽 | RSI 水平与变化；趋势强度与方向；压缩／扩张 | 候选扩展；日线已有指标可继续展示，新盘中公式不列为当前必交付项 | 待回放验证后另排 |

原始 OHLCV、成交额和时间质量是上述指标的基础。大部分指标可以本地派生，不需分别向 moomoo 请求；完整 VWAP、时段 RVOL 和多标的广度分别依赖当天累计数据、跨日时段历史和成员覆盖，不能把“可本地计算”解释成输入已经齐备。

对非 MACD 指标默认先提供水平与一阶变化，不机械给所有指标增加二阶差分。均线斜率不等于价格方向，RVOL 增加不等于买盘，ADX 只表示趋势强度；必须结合价格方向和量价结果解释。新增候选指标不得挤占 P1 的日线 MACD 优先交付。

### 现有实现定位

| 边界 | 现有入口 | 本计划中的处理 |
| --- | --- | --- |
| 日线指标与序列 | `backend/app/analytics/technical.py` | 提取纯 MACD 核心，保持日线其他公式稳定 |
| 日线冻结与图形投影 | `backend/app/decision_harness/market_evidence.py`、`contracts.py` | 新 feature/projection 版本，不覆盖旧 Dataset |
| 日线组与截面 | `group_observation.py`、`cross_section.py`（同上目录） | 增加三层 MACD 投影与筛选 |
| 日线存储／接口 | `backend/app/models/daily_evidence.py`、`repositories/daily_evidence.py`、`api/daily_evidence.py` | 可追溯衍生投影、批量读取、无读取时重算 |
| 独立采集运行时 | `urus-moomoo/src/urus_moomoo/runtime.py`、`config.py`、`contracts.py` | 增加股票独立盘中操作和版本化输出 |
| 日历／调度／限频／额度 | `urus-moomoo/src/urus_moomoo/services/` | 统一队列、有界批次、复用准入和持久化 pacing |
| 现有分钟线 | `integrations/moomoo_intraday_options.py`、`storage/intraday.py`（采集器内） | 抽取共用获取逻辑，保持既有期权数据链路兼容 |
| 网页 | `frontend/src/views/`、`components/decision/DecisionChartWorkspace.vue` | 日线增量展示＋独立盘中工作台 |

代码检查发现必须显式处理的差异：

1. `_calculate_macd()` 目前输出 `histogram`、`previous_histogram`，没有显式一阶／二阶差分。它的 `bullish_accelerating` 等旧标签仅比较 H 与上一根，不能改解释成 Δ²H。
2. 当前 MACD 使用初始 SMA 播种 EMA，返回值舍入至 6 位。新差分必须使用未舍入序列计算，兼容字段可继续原舍入方式。
3. 当前日线 ATR14 是最近 14 个 true range 的简单平均；盘中提案采用 Wilder ATR。二者应分别标记 `atr14_sma` 与 `atr14_wilder` 的公式版本，不借本次改动静默替换日线历史语义。
4. 独立采集器也有 `analytics/technical.py`。新增共享指标以后端冻结证据计算为权威，采集器输出不得成为第二套日线／盘中判定来源；先盘点既有消费者，不顺手重构整个独立包。
5. 现有 coordinator 的锁覆盖整个 run。分组优先级不能抢占正在持锁的期权任务，必须通过有界批次和安全释放实现。

## 3. P0：领域契约、版本与实施基线

### 指标与产出

本阶段不计算新指标。固定 H、ΔH、Δ²H、MA5、VWAP、RS、ATR、RVOL 的输入、单位、周期、预热、缺失和版本语义；区分日线正式证据、5 分钟正式证据与 CTA 收盘假设情景。

### 代码与数据设计

- 更新 `CONTEXT.md`：增加盘中运行、事件、采集组、晚发现事件、模型风险情景；现有收市 Observation Run 保持独立语义。
- 新增 `docs/adr/` 中对应 ADR（编号按实际目录确定）：决定数据归属、发布协议、版本迁移与读取不采集原则。
- 新增 `backend/app/analytics/momentum.py`：计划承载周期无关的 MACD 纯函数与类型。
- 新增 `backend/app/intraday/contracts.py`：定义 `BarWindow`、`FeatureValue`、`IntradayEvidence`、`StrategyEvent`、`CollectionCoverage`。
- `FeatureValue` 至少包含 value、unit、timeframe、formula_version、as_of、available、reason、warmup_status；null 表示不可用，不能用零代替。
- 版本键至少包括 bar policy、adjustment policy、feature version、rule version。参数 hash 与代码版本共同进入证据身份。
- 冻结分钟区间的 start/end 约定：adapter 规范化 provider 时间，计算层不猜测时间字符串语义。
- 区分 `formula_available` 和 `signal_eligible`：MACD 首批数值可展示为预热中，正式盘中事件要求更长稳定历史。不能因盘中要求 100 根而让旧日线突然失去原可用字段。

### 测试与验收

- 先写契约验证测试：未知版本拒绝、字段缺失、null/zero、时区、重复逻辑身份。
- 验收：数据类型、读写所有者、版本升级、跨日处理、计划权威来源有明确决定；后续阶段无需自行猜测。

## 4. P1：MACD 三层共享计算与日 K 观察交付

### 必须实现的指标

| 指标 | 公式／行为 |
| --- | --- |
| DIF / DEA / H | EMA12−EMA26；EMA9(DIF)；H=DIF−DEA |
| 一阶差分 | `histogram_delta[t] = H[t]−H[t−1]` |
| 二阶差分 | `histogram_delta2[t] = H[t]−2H[t−1]+H[t−2]` |
| 状态标签 | 正柱扩大／收窄、负柱扩大／收窄；上升加快／减慢、下降加快／减慢 |
| 可选标准化 | 各原始指标除以同一时点、同周期、明确版本的前置 ATR；不差分已标准化 H |

### 代码设计

1. `analytics/momentum.py` 提供 `calculate_macd_series(closes, spec)` 和 `classify_histogram(h, delta, delta2, tolerances)`；返回与输入索引对齐的序列。纯函数不接数据库、网络或系统时间。
2. `analytics/technical.py` 的 `_calculate_macd()` 与 `calculate_technical_series()` 调用同一核心；保留旧字段、旧标签兼容，新增明确的 `histogram_state`、`slope_state`、`acceleration_state`，不重命名后直接改变旧策略行为。
3. 为 H、ΔH、Δ²H 分别记录可用性。正常连续序列下首次 H 在第 34 根可计算，ΔH 在第 35 根、Δ²H 在第 36 根；稳定预热资格另行标记。非预期缺 bar 不压缩后再差分。
4. `decision_harness/contracts.py` 增加指标版本；`market_evidence.py` 生成新版本结果。`group_observation.py` 和 `cross_section.py` 增加成员级字段和筛选定义，禁止对不同股票原始 H 简单求平均代表板块动量。
5. `schemas/daily_evidence.py` 与前端 `types/dailyEvidence.ts`、`types/api.ts` 对齐响应。旧结果缺新字段时显示“未计算此版本”，不渲染为 0。
6. 修改 `InstrumentDecisionView.vue`、`GroupObservationView.vue`、`IndicatorCrossSectionView.vue` 及图表组件：展示三项数值／标签、支持各自序列切换及筛选；周期和截至时间可见。
7. 老 Dataset 补算用显式后台命令和新派生记录，保存 `source_dataset_id + source_bar_hash + feature_version` 唯一身份。若现有投影表不足，新增 `DailyFeatureProjection` 表及 Alembic 迁移；不就地改写旧 Dataset、策略结果或历史提醒。GET 请求只读已存投影。

### 测试与验收

- 扩展 `backend/tests/test_technical.py`，新增 `test_momentum.py`：独立手算序列、34/35/36 根边界、零值、缺口、正负组合、精度、标准化顺序。
- 日线契约测试放在 `test_daily_evidence.py` 及相关截面测试：旧版本兼容、同版本重试幂等、新版本不覆盖原结果。
- 关键例子：H=`[-0.2,-0.4,-0.5]` 得到 ΔH=−0.1、Δ²H=+0.1，文案必须是“仍在恶化，但恶化减速”。
- 验收：日 K 个股和组成员均能看见三项数据；数值、序列、筛选一致；不会把旧 `accelerating` 标签当作二阶结果；不新增 provider 调用。

## 5. P2：股票分钟线、分组准入与稳定发布

### 数据产出

本阶段输出股票批量报价、规范化 1 分钟 OHLCV/turnover、计划身份、采集时间、缺失与延迟状态；暂不生成买卖或结构事件。

### 采集代码设计

- 新增采集器 `integrations/moomoo_intraday_equities.py`：提供股票快照与分钟线方法。将现有 `_minute_bars()` 的通用逻辑提取到独立 helper，期权适配器复用；回归保证旧期权任务输出不变。
- `config.py` 新增 `capture_intraday_equities` 操作及 A/B/C/D 采集组参数；`runtime.py` 明确分支，不能套用包含期权链的 `capture_intraday`。
- 新增 `services/intraday_plan.py`：`build_plan(universe, groups, cache, quota, session)` 产生去重代码、频率、准入理由、预算与版本。初版权威是部署端本地配置，网页只读；不同时引入可冲突的网页写配置路径。
- 业务观察组与采集组分离。跨组相同 symbol 按最高频率采一次，Focus List 不自动改变采集范围。
- 快照每轮合批，最多 400 代码一请求；历史分钟线逐 symbol。A/B/C 每 5 分钟；D 拆三个轮换批次，每 15 分钟。D 报价变化只用于筛查，不能冒充完整 K 线事件。
- `services/quota.py` 每次新增请求前准入；已有窗口代码复用。20%／至少 10 的默认预留可配置；轮换不释放历史额度。
- `scheduler.py` 与 `pacing.py` 增加预算感知批次。批次原始结果先落盘，再释放锁并调度下一批；持久化 next_allowed_at，不在释放锁时重置限频。既有期权任务需同样在可恢复边界拆批，无法拆分时明确串行阻塞预算。
- 一个 symbol 失败不丢弃成功批次，额度失败、权限失败、网络失败分别记录；按截止点、批次和 attempt 组织重试，不能无限重试低流动性报价。

### 持久化与发布代码设计

推荐沿用独立进程和版本化契约，避免两个应用共同写同一个 SQLite：

1. collector 本地 SQLite 保存 capture envelope、批次事实和发布账本；新增 `storage/equities.py`，不将新股票任务伪装成期权 capture run。
2. 新增 `equities_publisher.py`：通过内网 HTTP 将有界批次发送至后端摄取入口；发送在 provider 锁外，独立 worker、有重试上限与退避。
3. 后端新增 `models/intraday.py`、`repositories/intraday.py`、`schemas/intraday.py`、`api/intraday.py`，由后端独占写其事实投影及事件表。
4. 外层沿用 capture envelope，内部新增 `equities payload v1`。摄取校验 schema、scope、content hash、时间与 payload 大小；复用现有发布身份验证模式，不因局域网而允许任意来源提交事实。
5. 唯一身份：采集逻辑槽 `(plan_version, session_date, cutoff, batch_id)`；attempt 独立。相同 capture_id/hash 重试返回已有结果，同 ID 不同 hash 拒绝。对更晚修订创建 revision，不覆盖旧事实。
6. `IntradayBarRevision` 保留 symbol/provider/timeframe/adjustment/bar_end/revision、observed_at 与 capture_id；读模型选择指定 cutoff 当时可见的版本。
7. 每轮基于计划预期 symbol 集合建立 `IntradayRun`；到齐或到达 deadline 后封存质量与范围。迟到结果单独版本，不修改已发布信号的输入。
8. 既有期权分钟存储继续运行。首版容忍少量 SPY/QQQ/IWM 获取重复，计入联合预算；只有验证 cutoff/复权/新鲜度一致后才复用已有捕获，不以消除重复为由改变原始证据身份。

### 预算与测试

- 沿用策略文档的示例：35 个核心＋25 个轮换，基础每轮约 44–45 请求；额外额度查询、期权、分页和重试单独计入。
- 目标核心证据延迟 P95≤90 秒、全轮≤180 秒是待实测指标，不是按 API 上限推导的保证。
- 新增采集器 `test_intraday_equities.py`、`test_intraday_plan.py`、`test_equities_publisher.py`；扩展 scheduler/quota 相关测试。
- 覆盖 400/401 快照分批、单 symbol 分钟请求、跨组去重、同代码多周期、配额保留、重启批次恢复、持锁阻塞、发布失败不阻塞采集。
- 后端测试覆盖重复摄取、hash 冲突、迟到修订、部分成功及只读查询不采集。
- 验收：可在不启用任何新策略时运行事实链路；某轮失败可解释，股票扩容不自动扩张期权链范围。

## 6. P3：盘中指标、基准对齐与强弱面板

### 本轮最小交付契约（2026-10-15）

- 独立 `/intraday` 页面和导航入口；后端 `GET /api/intraday/overview`、`GET /api/intraday/symbols/{symbol}`。复用配置中的 sidecar 数据库路径，新增只读功能开关默认关闭，不提供网页启用采集的操作。
- 只读取 `scheduled_captures` 指向的最新 `capture_intraday_equities` 契约，校验 capture envelope、内容 hash、交易日、调度槽和 UTC 时间区间。部分成功可展示覆盖，不能悄悄退回上一完整槽伪装当前数据。读库采用只读连接，缺库不创建文件。
- 响应标明 schema/formula version、capture ID/hash、trading_date、scheduled_at、cutoff、captured_at、freshness、quality/coverage、warnings。首次仅投影单个最新槽，不拼接历史捕获；D 组本轮未采分钟时明确显示轮换／仅报价。
- 采用离线交易所日历确定 regular session（包括半日市），不因读取页面调用 OpenD 日历。将明确的 1 分钟 UTC start/end 聚合为从开盘锚定的完成 5 分钟区间，缺一分钟、重复冲突或非法 OHLCV 的区间不可用，不前值填补。指标不得跨越缺口压缩时间，优先对连续有效后缀计算。
- 本轮实现 MA5、MA5 距离和斜率、Wilder ATR14、session VWAP、MACD H/ΔH/Δ²H 及正负变化标记、相对 SPY 的 RS15/RS30。RS 使用 `100×[(C_t/C_{t-h}-1)-(B_t/B_{t-h}-1)]`，h 为 3／6 根 5 分钟；因此分别需要 4／7 个收盘点，按相同起止时刻且完整连续窗口对齐，缺基准不可用。MACD 复用已有纯核心，34/35/36 根显示对应数学值，100 根前标明预热未完成且不可触发正式策略。
- 最新槽仅含当日分钟数据，一个普通美股交易日最多 78 根 5 分钟 K，因此本轮 MACD 无法达到 100 根预热。后续需要显式历史初始化／跨日合格数据后才能启用正式 MACD 事件；不把“值已可计算”当作“策略已就绪”。
- VWAP 仅在从当日开盘至共同截止点的分钟、成交量和成交额完整时可用。RVOL 缺历史同时间段基线时返回 null 和原因；不以当日简单均量冒充。观察池广度附分子、有效分母、请求池数量和截止点，不称为全市场广度。A/B/C/D 是采集角色，不冒充行业观察组。
- UI 展示大盘 ETF（A）、板块 ETF（B）、重点股（C）、轮换股（D），可筛选角色和选择标的；详情展示完成 5 分钟 K、MA5、MACD 三层值与标记。日期和横轴明确美东 HH:mm，报价时间与 K 线截止时间分别标注。缺数据、关闭、损坏、陈旧、预热不足均有清楚状态，无示例行情伪装运行结果。
- 先做 backend 行为测试红灯，再开发；最后使用隔离 SQLite 和 Playwright 脚本验收页面。暂无通知，也不将本切片称为已实现 P4–P6 的事件策略。

### 必须实现的指标

| 指标 | 设计口径 |
| --- | --- |
| 5 分钟 K | 从完整 1 分钟区间聚合，处理 provider 结束时间标记 |
| MA5 | 最近 5 根完整 5 分钟收盘价；斜率、ATR 归一距离 |
| MACD 三层 | 复用 P1 纯核心，独立 5 分钟预热与版本 |
| ATR14 Wilder | 盘中使用 Wilder 平滑，不能混用日线 SMA ATR |
| Session VWAP | 当天 regular session 完整累计成交额／成交量 |
| RS15/RS30 | 与相同起止时间的基准收益差，单位百分点 |
| RVOL | 当前时段量／前 20 日同时间段中位数，至少 10 个有效日 |
| 观察池广度 | 上涨比例、VWAP 上方比例、RS 改善比例，附分母与共同样本 |
| 状态×变化 | 强／中性／弱与增强／稳定／减弱；缺失时不可判定 |

### 代码设计

以下保留完整阶段的职责草案；本轮以以上“最小交付契约”为准，先合并纯计算到小模块，不实施 `worker.py` 或事实镜像表。

- `backend/app/intraday/bars.py`：`aggregate_completed_bars()`、交易日历区间校验、预热窗口选择；不对缺 bar 自动前值填充。
- `features.py`：MA5、Wilder ATR、VWAP、RVOL、MACD 组合计算；`relative_strength.py`：基准 join 和共同截止点；`breadth.py`：冻结成员及共同有效分母。
- `evidence.py`：`freeze_run()` 将 source revision IDs、输入 hash、计划、参数版本与 features 固化为 `IntradayEvidence`。部分 coverage 明确保留，不能因基准缺失让 RS 变成 0。
- `worker.py`：消费已封存 run，幂等计算。失败留队列状态，不在 GET 请求临时计算；共享事务内写结果与进度，跨进程 worker 使用领取租约和超时恢复。
- 一轮冻结后产生个股、组和市场投影。ETF 状态与组成员广度分别呈现，不能用 ETF 替代未覆盖成员。
- 新增 `frontend/src/views/IntradayObservationView.vue`、`types/intraday.ts`、`api/intraday.ts`，注册 `router/index.ts`，提供大盘、板块、标的三层只读表格。
- 普通池若只有报价，就只显示报价能力；若分钟线 15 分钟轮换，显示“截至…／延迟采集”，不给当前 5 分钟完整判断。

### 测试与验收

- 新增 `test_intraday_bars.py`、`test_intraday_features.py`、`test_intraday_evidence.py`：聚合边界、缺分钟、跨日 MA、VWAP 重置、ATR 公式、基准缺失、复权、RVOL样本不足。
- 统一输入同时进入日线与盘中 MACD 纯核心，计算值一致；时间语义、预热与输出身份分别处理。
- 验收：用户能看到 5 分钟 MA5、H/ΔH/Δ²H、RS 和覆盖信息；数据未就绪时不制造完整状态；页面刷新不会增加 OpenD 调用。

## 7. P4：MA5、动量、异动事件与网页展示（盘中 MVP）

### 本轮实施约束（2026-10-16）

用户要求继续 P3 后续开发，且不部署。继续由指定开发 chat 实现、`luna-builder` 独立测试、协调 chat 审查；只运行离线与临时库测试，不连接生产、OpenD、SSH 或容器，不改私有运行配置。

- 复用 P3 验证后的采集投影，新增最小纯评估器、显式 `run_once`、事件／状态／消费进度的事务存储和只读事件查询；不提前搭建通用插件策略框架。GET 不计算或写事件。
- MA5 首版观察缓冲修订为 `max(0.10 × ATR14[t-1], 0.0002 × C[t-1])`，后项是 2bp 的价格比例，不是最小报价单位。采用新规则版本 `atr_price_buffer_v1`，避免为当前观察用途增加元数据请求或假设全部股票 tick 为 0.01。ATR 缺失／零值时不可用。
- P3 的 `ma5_slope` 继续保持相邻 MA5 的价格差，不改字段语义。策略归一化斜率若需要，使用独立字段。
- S1 比较 t 与 t−2 的同窗口 RS（10 分钟变化），再用两个相邻有效槽确认；初始最小变化缓冲为 0.05 个百分点，标明参数与规则版本。自身收益和相对收益分别保留。
- 放量标签需要有效跨日同时间段 RVOL；缺基线时明确不可用，不用当日均量替代。价格急跌独立于 RVOL。MACD 三层仍为证据标签，100 根预热不足不生成正式 MACD 策略事件。
- 量能能力的最小输入为 `features.rvol_context`：至少 10 个有效历史日、正值同时间段成交量中位数、`quality_status=ready`、基线截止交易日早于当前日、时间槽与当前 5 分钟区间一致。评估器以当前量／历史中位数计算 RVOL，并冻结基线说明；孤立的 `rvol=1.8` 数值不算合格历史证据。输入合格但未达到阈值只是条件未触发，不标为能力不可用。
- 正式事件要求源分钟边界策略已确认。P3 仍可展示未确认边界的数据和提示；P4 的 `quality.minute_timestamp_boundary_verified` 仅在源契约明确为真时放行，否则能力不可用。当前 P2 记录该项为 false，不能在未验证的情况下自行改成 true；本轮用明确标记的离线 fixture 验证规则，不接真实 OpenD。
- 迟到修订不改写实时已处理槽；离线重算使用独立回放身份。候选确认、失效和过期的发生／处理时间分开保存，确认价位和失效价位固定于候选创建时。
- 后台消费默认关闭，事件数据写入与源采集库分离。任何本轮启动的服务只允许使用临时目录和独占 loopback 端口，结束后清理本轮进程。

### 必须实现的策略

- S1 相对强弱增强／减弱及状态变化。
- S2 MA5 站回、跌破、回踩守住、反抽失败、乖离扩大。
- S3 放量拉升／下挫、独立价格异常急跌、指数与观察池同步下挫。
- MACD 三层只作为明确证据标签；单独二阶变化提供详情与筛选，不做通知。

### 代码设计

- 新增 `intraday/strategies/base.py`：`evaluate(evidence, prior_state, spec) -> transitions`；输入不可变、无 provider 调用、无隐藏系统时间。
- 新增 `strategies/relative_strength.py`、`ma5.py`、`impulse.py`；规则参数来自版本化 spec，沿用策略文档初始值。
- 新增 `events.py`、`state_machine.py`：候选／确认／失效／过期迁移；冻结确认位和失效位，不随每轮输入漂移。
- `IntradaySetupState` 保存当前状态与最后处理 cutoff；`IntradayEvent` 保存不可变迁移，唯一键 `(setup_id, transition_seq)`，setup 身份包含 symbol、strategy、timeframe、rule_version、起始证据。
- 相同 cutoff 重试不重复迁移。迟到 revision 用独立回放身份，不能倒退实时状态；使用数据库事务或行版本冲突检测处理并发。
- 严重异常独立于 RVOL 可用性，先质量检查，再发布；普通冷却不能屏蔽风险升级、确认或失效。
- 新增只读 API `GET /api/intraday/overview`、`/symbols/{symbol}`、`/events?after=cursor`、`/health`；游标使用单调 ID，不能仅依赖相同毫秒时间戳。
- 不新增 events/read 接口、阅读状态表或通知队列；事件状态仅指策略候选／确认／失效／过期。
- 网页每 10–15 秒读取已有结果，展示状态、事件列表和详细依据。初次加载／重连按截止时间恢复列表，不弹窗、不播放声音。

### 测试与验收

- `test_intraday_ma5.py`、`test_intraday_impulse.py`、`test_intraday_events.py`、`test_intraday_api.py`：阈值边界、两根确认、风险绕过普通冷却、状态幂等、过期与补采、游标分页。
- Playwright 验证页面关闭后重新打开能查看事件；重复刷新不重复记录；未知与过期状态可见。这里只做定向流程验证，完整系统测试留到 P7。
- 验收：后台独立运行、网页可浏览和筛选事件；5 分钟价格在 MA5 上下的位置与对应解释一致；不包含通知系统。

## 8. P5：结构策略、A/V 与简化九转

### 接续实施切片（2026-10-16）

P4 验收后继续本阶段，复用已有事件持久化、只读接口和日内事件面板，不再另建通知或策略框架。先写确定性行为测试，再实现价格结构纯函数并接入 P4。本地验收通过不等于允许部署。

- 首个版本只消费 P3 已有的当日完整 5 分钟 bar，不额外请求历史。ORB 的 09:30–09:45 区间必须三根齐全且交易日相同；区间冻结后也要满足前置 ATR 预热，不能为开盘早期信号捏造 ATR。
- S4/S5 使用与 P4 相同的前置 ATR／价格缓冲。S5 区间只取当前 bar 之前的六根，范围／前置 ATR 不超过 2.5；当前真实波幅大于前置 ATR 才称为波幅扩张。突破建立候选，后续完整 bar 守住冻结确认位才确认；不移动既有边界。
- S6 首版采用可解释的六根前置 bar 顺序结构：V 的参考高点必须早于参考低点，A 相反；两点幅度至少为两倍前置 ATR，并且极值后已有一根完整反向 bar。当前 bar 站回／跌破 MA5 后才建立候选，冻结极值后的反弹高／回落低为确认位；必须由之后的 bar 突破确认。没有极值后的反向 bar 或确认位时不可用，不事后补画信号。
- S7 仅关联已记录的 S4/S5 父 setup，在其后连续三根内收回冻结区间并超过缓冲时记录失败；保留父身份，不能根据最新滚动区间重建历史父事件。
- S8 首个可交付版本明确为 `simplified_setup_session_v1`，交易日切换或 bar 缺口重置，同方向达到九次只记一次“衰竭观察”，持续十次不重复。它是当日版本，不宣称已经实现原草案的跨日连续版本；跨日版本待可靠历史输入到位后另加规则版本。
- 缺数据、时间边界未经验证、过时源、冷却、TTL、冻结线和未来不可见规则沿用 P4。MACD 背离仍延期；缺 RVOL 只缺支持证据，不伪造放量。页面保留各策略身份，不为“合并主要事件”提前建设聚类系统。

### 指标与策略

| 策略 | 必需的新状态 |
| --- | --- |
| S4 ORB | 冻结开盘 15 分钟高低点及形成时刻 |
| S5 区间突破／延续 | 前 6 根区间、压缩程度、冻结边界、回踩阶段 |
| S6 A/V | 已知单向行情、候选极值、后续确认位、实际可见时刻 |
| S7 假突破 | 原突破事件身份、返回区间时刻、3 根观察窗口 |
| S8 简化九转 | `C[t]` 与 `C[t−4]` 连续比较计数；相等／不成立重置 |

### 代码设计

- 新增 `intraday/structure.py` 管理因果拐点与冻结区间；不使用事后会重画的 ZigZag 结果触发实时事件。
- 新增 `strategies/opening_range.py`、`range_breakout.py`、`reversal.py`、`failed_breakout.py`、`exhaustion.py`。
- 复用 P4 迁移器、生命周期、事件聚合。一次放量突破伴随 MA5 站回时只有一个主要展示事件，保留所有策略依据与内部身份。
- 九转单独标为“简化 Setup”，跨日政策沿用策略文档；不把 9 作为必反转或完整 DeMARK Sequential。
- MACD 背离仅在拐点契约及可见时间测试通过后实现；否则维持“延期”，已有 H/ΔH/Δ²H 不受影响。
- 网页详情画出当时冻结的确认／失效线；历史版本使用原线，不从最新价格反推。

### 测试与验收

- `test_intraday_structure.py` 和各策略行为测试：先后顺序、区间形成前不触发、未来 bar 不影响已发事件、候选超时、失效边界固定、九转重置。
- 验收：A/V 候选、确认、失效有独立时间；回放中删除未来数据不会改变过去的已知事件；“九转”不能单独升级为反转确认。

## 9. P6：CTA 风格系统性趋势风险（扩展）

### 首版简化约束（2026-10-16）

在 P5 验收之后独立推进，默认关闭。优先复用现有不可变日线 Dataset 与其冻结 chart 的价格、hash 和 adjustment 信息，在只读 service 中构造模型基线；只有确实无法复用其身份和保存语义时才增表，不机械创建下文建议的 ORM。第一版不加后台采集任务，不增加 moomoo 请求。

- 昨日正式状态与“假设今天以当前报价收盘”的情景分开。昨日基线必须截至前一完整交易日，日线缺口、未来输入、过时或不明报价均显式降级。
- 昨日趋势信号取 `sign(SMA_fast − SMA_slow)`，相等为 0；目标年化波动率初值 10%，敞口上限 1。参数和公式版本需进入基线身份，不标注为真实基金参数。
- 波动率取最近 20 个日对数收益的样本标准差（ddof=1）乘 `sqrt(252)`，至少需 21 个有效收盘。零波动不可用。情景只替换窗口中的一个假设今日收益，不累计每次 5 分钟报价为多笔日收益。
- SMA 翻转价使用前 `fast−1`、`slow−1` 个已知收盘求解析根，并用直接代入复核；不足窗口或非正根不能展示成有效价位。
- 价格口径必须一致：日线复权方式与当前报价无法证明兼容时，情景不可用。不得为让卡片出现数值而把复权日线直接拼上未复权报价，或增加隐式在线补采。
- UI 仅一张 CTA 风格代理背景卡，列模型、条件翻转价、假设敞口变化及不可用原因。无真实 CTA 流量、自动交易或推送；模型纯计算与只读装配分别验收。

### 定位与指标

只模拟透明的趋势跟随与波动目标规则，界面注明“CTA 风格代理模型”。不声称知道实际基金仓位、订单、美元流量或卖盘因果，不改变 Urus 不自动交易的边界。

| 指标 | 首版候选设计 |
| --- | --- |
| 多速度趋势 | 日线 SMA(10,20)、SMA(20,60)、SMA(50,200) 交叉，独立模型，不合成为概率 |
| 条件翻转价 | 固定昨日及以前日线，以假设今日收盘价 p 求今天均线差为零的价位 |
| 实现波动率 | 20 个日对数收益的年化波动率，公式及样本定义版本化 |
| 模型目标敞口 | `clip(signal × target_vol / estimated_vol, −cap, cap)`，仅假设模型敞口 |
| 敞口变化分解 | 固定波动看趋势变化、固定趋势看波动变化，并展示联合变化；剩余交互项单独标记 |
| 多层风险标签 | 盘中急跌＋趋势接近翻转＋模型多头敞口下降，同时列出证据 |

这些窗口与参数是首版研究候选，需样本外验证，不代表真实 CTA 使用的参数。第一版不同时加入所有动量／通道模型，避免模型高度相关却被误认为投票置信度。

### 代码设计

- 新增 `backend/app/systematic_risk/contracts.py`、`models.py`（纯计算模型）、`scenarios.py`、`service.py`；数据库模型另放 `app/models/systematic_risk.py`，避免混淆领域模型与 ORM。
- 本轮最小接口使用 `models.evaluate_trend_scenario(closes, assumed_close, *, fast, slow, target_vol=0.10, cap=1.0, volatility_window=20)`，直接返回昨日基线、假设今日收盘情景、翻转价及敞口变化分解；不为文件清单拆分无必要的 contracts/scenarios 模块。
- 只读装配接口为 `service.build_overview(session, projection, *, now, market_calendar="XNYS")`，复用 P3 投影中的 ETF 报价和既有 `DailyDecisionDatasetModel`／`DecisionChartProjectionModel`。输出基线 Dataset ID、chart/input hash、最后日线日期、复权口径、稳定 baseline ID；情景另有报价时间及稳定 scenario ID。GET 不落库、不调用 freeze 或 provider。
- 每个 ETF 查询有界的最新包含该标的的冻结 chart；校验已知时间、内容 hash、日线连续性和前一真实交易日。健康 ETF 不受其他 D 组缺数据引起的顶层 stale 屏蔽。首版仅在日线 `adjustment=NONE` 与报价 `price_basis=raw` 已知一致时装配情景；缺因子的 QFQ 输入明确不可用，不隐式替换历史数据。
- 每日冻结 `SystematicRiskBaseline`：输入 Dataset/hash、model_version、参数、昨日正式趋势与波动。
- 每 5 分钟复用新鲜 ETF 报价创建 `SystematicRiskScenario`，字段含 `assumed_close`、`quote_as_of`、`baseline_id`、`provisional=true`。正式状态只在收盘合格日线到齐后更新。
- 翻转根使用包括假设今日收盘价的两条 SMA 求解；负数／无解／多阈值按模型返回显式状态。未来接入非单调模型时不得默认只有一个翻转点。
- 波动情景将假设日收益加入完整日序列，不将每根 5 分钟收益当一个日收益或重复累加。没有足够日线的长模型不可用。
- 波动升高导致多头减仓是卖向变化，但空头缩减是回补；输出方向必须依据原模型敞口符号。
- 仅 SPY/QQQ/IWM 起步，复用现有日线与报价，不新增期货订阅。ETF 代理不转换成期货点位，不代表跨资产 CTA。
- 新增只读 API `/api/systematic-risk/overview`，盘中页面提供背景卡片；是否启用与核心事件分开控制。

### 测试与验收

- `test_systematic_risk.py`：解析根与直接代入结果一致、无解、零波动、敞口上限、多空降杠杆方向、相同 baseline 重算幂等。
- `test_systematic_risk_scenarios.py`：盘中情景不能改写正式日线；未来数据不可见；缺报价降级；不重复计入日收益。
- 验收：能解释“哪个模型、在什么收盘假设下、为何变化”；界面无实际 CTA 流量和触发必然性的表述。

## 10. P7：回放评估、容量验收与渐进上线

### 本轮执行边界（2026-10-16）

本轮只推进离线回放和完整本地验收。下文的生产 shadow、真实覆盖／延迟观察、启用及上线均保留为后续待授权工作，不能因“继续计划”而执行。真实分钟边界、账户额度、连续交易日覆盖和 provider 延迟不以模拟测试替代。

本轮最小回放切片采用文件输入和纯函数，不新增数据库、后台任务或通用回测框架：

- `intraday.replay.replay_observations(observations, *, spec=None)`：每项含原 capture、显式 `observed_at`、`provenance=original_observed|reconstructed`。先验证 capture hash 与时间，再按实际观察顺序调用 P3 投影和同一 P4/P5 evaluator；按 task 隔离状态，重复／晚到旧槽不覆盖实时式状态。没有原始观察时间不能推定为当时已知，重建输入分组统计。同一 task 在一次回放内只接受一种来源类型，混合输入显式跳过，须分开回放，避免原始事件借用重建状态完成确认。
- `intraday.outcomes.measure_outcome(event, bars, *, horizon_minutes, market_calendar="XNYS")`：固定 15／30／60 分钟，使用事件 cutoff 收盘作参考。只接受同交易日连续完整窗口；返回后续价格变化、最大向上／向下变化、冻结位首次触及顺序。两线同 bar 触及时标“顺序未知”；没有价格方向的 RS 标签不强推成多空交易。
- 显式 CLI `python -m app.intraday.replay --input <observations.json> --output <report.json>`，只读输入，输出文件须新建，不覆盖已有报告。不导入生产配置，不连接 sidecar／主库/provider，不写实时状态。报告分原始观察／重建来源，保留输入身份、规则版本、事件及不可用原因，不输出交易胜率或收益承诺。
- 后续真实历史样本、参数时间切分、基准策略增益和生产容量仍需独立证据；上述本地切片通过不把它们标为完成。

### 评估数据与代码

- 新增 `backend/app/intraday/replay.py`、`outcomes.py`、`evaluation.py` 和显式 CLI 脚本；离线回放与实时计算使用同一 feature/strategy 核心。
- 读取 capture observed_at 与 bar revision，按当时可见数据重建；若只剩事后修订 bar，标记 reconstructed，不与实时可见回放混为一组。
- 保存 15/30/60 分钟结果、最大有利／不利变化、确认与失效顺序、事件数量和延迟。无逐笔证据时同一 bar 同触确认／失效标为顺序未知。
- 固定 outcome、参数与时间切分后再评估；按交易日隔离训练／验证，outcome 窗口不得跨入另一集合泄漏。
- 比较简单 MA5、单 MACD 与新增条件的增益，分趋势／震荡、时段、标的类型报告样本数。未经定义成交成本与执行规则，不输出交易胜率或收益承诺。

### 完整验收

1. 后端与采集器相关全套测试、迁移验证、前端构建；无前端新增单元测试。
2. Playwright 使用固定数据覆盖日线三层 MACD、盘中各层、事件分页／筛选、数据缺失与重连恢复。
3. 使用隔离测试配置模拟完整交易日、提前收市、断线、重启、额度满、期权慢请求及 API 发布失败。外部行情不用于可重复单元测试。
4. 生产先小范围 shadow：只记录事件；观察多个实际交易日，记录覆盖与延迟，不为测试主动增加大量新标的。
5. 达到核心组覆盖、P95≤90 秒目标且无连续漏槽；超限需解释并降低范围／调整任务，不隐藏慢轮。验收普通日与半日市路径，实际无半日市时用确定性重放覆盖。
6. 核对 5 分钟分钟槽、收盘最后 bar、发布、指标、事件各层账本，不能仅靠 `/health` 成功判定完整。
7. 磁盘每日增长、读查询成本、索引和备份恢复经过验证后再扩大 C 组；归档或删除需另行明确授权。

### 上线与回退

- 建议分别设置 `intraday_equities_enabled`、`intraday_features_enabled`、`intraday_events_enabled`、`systematic_risk_enabled`；名称为待实施配置，不代表当前存在。
- schema 采用增加式迁移，兼容旧字段；首次新采集上线前备份并检查旧消费者读取。部署和生产迁移是后续具体操作，本文不代替审批或授权。
- 回退先停用新任务／worker，再回退兼容应用版本；保留新证据和事件。不把回滚等同于删除表或用备份覆盖生产库。
- 仅提供网页观察；通知功能若未来需要，另行立项，不作为本计划验收条件。

## 11. 预期新增模块与接口清单

以下为建议路径，落地可在保持职责清晰的前提下合并小文件；避免为目录形式拆出无行为的空层。

```text
backend/app/
  analytics/momentum.py             # 日线与盘中共用的纯 MACD 核心
  intraday/
    contracts.py, bars.py, features.py
    relative_strength.py, breadth.py, evidence.py
    worker.py, events.py, state_machine.py, structure.py
    strategies/                     # S1–S8 纯规则
    replay.py, outcomes.py, evaluation.py
  systematic_risk/                   # P6 代理模型与条件情景
  models/intraday.py
  models/systematic_risk.py
  repositories/intraday.py
  schemas/intraday.py
  api/intraday.py

urus-moomoo/src/urus_moomoo/
  integrations/moomoo_intraday_equities.py
  services/intraday_plan.py
  storage/equities.py
  equities_publisher.py

frontend/src/
  types/intraday.ts
  api/intraday.ts
  views/IntradayObservationView.vue
```

数据库新增表应围绕实际查询设计索引：`(symbol, bar_end_at)`、`(run_id, symbol)`、`(session_date, cutoff)`、`(symbol, event_id)`、发布待办状态。overview 一次批量读取最新投影，避免每个 symbol 单独查库或加载全部原始 JSON。

统一计算入口示意（设计签名，不是现有 API）：

```python
calculate_macd_series(closes, spec) -> MacdSeries
build_plan(universe, groups, cache, quota, session) -> CollectionPlan
ingest_capture(envelope) -> IngestReceipt
freeze_run(run_id, feature_spec) -> IntradayEvidence
evaluate(evidence, prior_state, rule_spec) -> list[EventTransition]
evaluate_scenario(baseline, assumed_close, quote_as_of) -> RiskScenario
```

## 12. 阶段完成记录要求

每阶段交付记录至少包含：实现指标与公式版本、修改模块、迁移／配置变化、执行测试和结果、明确未完成项、数据兼容性与回退方式。

不得把“写好代码”“页面能打开”“单次运行成功”单独作为阶段完成依据。特别是 P2 要有跨进程发布和恢复证据，P4 要有事件状态正确性，P7 要有联合采集及数据质量的完整证据。

CTA 背景参考（2026-10-14 已查询；实现时再核对适用模型）：

- [AQR：Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)
- [Man：不同速度的趋势跟随](https://www.man.com/insights/need-for-speed-trend-following)
- [Man：波动目标策略](https://www.man.com/insights/the-impact-of-volatility-targeting)

moomoo 限额和其他策略参考统一见策略基线文档，避免两份文档维护不同额度口径。

## 13. 2026-10-20 日内页面与标的池设置补充（已收盘后部署）

- 日内页采用 216px 紧凑标的栏，业务主题分组可折叠、搜索和内部滚动；旧 capture 没有主题映射时明确退回采集角色展示。采集角色与 G1/G2/G3 时段不当作行业分组。
- 报价时间和指标缺失原因默认收起，MACD 三层常驻中文名称、公式及正负含义。H 使用 DIF−DEA，ΔH 为相邻柱值差，Δ²H 为相邻斜率差；加速度转正不等于价格已上涨。
- 设置页增加独立日内池：从现有 Universe 选择，SPY/QQQ/IWM 固定，保存安排在下一 XNYS 交易日生效。显示 active/pending 与日期；当前交易日配置不随保存改变。PUT 使用 `{base_revision, selected_symbols}`，冲突需重新读取。
- 后端首次沿用旧 collector capture 的实际请求池，不用缺省验证代码替代；无法完整解析时明确不可用。设置不修改全局 Universe 的采集规则。
- collector 首个到期股票任务读取并按交易日冻结计划到原 collector.sqlite；维护、三个分片和最终合并使用相同成员、分组与计划 ID，重启不改当天配置。新成员走已有历史额度准入，既有 Eoflora 任务优先，仍沿用 1+3+1 时间安排。
- 本轮验收含后端/collector 离线测试及 Playwright 固定数据浏览器测试，不写前端 UI 单元测试。具体结果见本轮验收记录；代码、构建与测试通过不代表已部署。

部署完成记录：2026-10-20 JST，经用户明确授权后已上线该节内容、MACD/K 线共享时间轴与盘中事件上移。详见 `docs/reviews/intraday-deployment-20261020.md`；历史事件健康告警及首次真实窗口验收边界仍按该记录保留。

# Urus 当前领域上下文

本文是 Urus 当前运行架构的术语和边界基线。代码、接口、页面和后续设计应优先使用这里的名称；历史迁移文件只承担数据库升级职责，不定义当前运行行为。

## 产品边界

Urus 负责 Universe、市场数据、日线证据、期权结构、确定性策略、可视化投影和远程决策输入的事实与版本管理。外部数据源只通过适配器进入系统，所有可展示结果都必须带来源、采集时间和质量状态。

系统不下单、不管理真实仓位、不推送信息，也不把缺失数据填成旧值或模型推测。用户主动查看时获得截至证据时点的机会判断与条件化研究计划；查看不等于成交，不假设用户响应延迟或实际盈亏。远程 AI 是用户主动触发的决策辅助能力，不能修改事实层或扩大输入范围。

## 术语

**Instrument Universe**：版本化的标的配置集合。每个标的包含资产类型、主题、启用状态、采集意图、角色和相对强弱基准；保存新配置会生成新版本。

**Observation Group**：由 Universe 自动投影或用户维护的版本化标的集合，包含成员、基准、标签和版本。观察组是组级技术证据的输入，历史运行始终引用当时的组版本。

**Observation Run**：以一个交易日和截止时间为边界的一次确定性收市观察。它冻结 Universe/观察组版本，补齐需要的日线，计算指标、图表和策略，串行采集期权快照，并保存组快照与质量结果。

**Daily Decision Dataset**：对一个 `Decision Scope` 在最后完整日 K 截止时形成的不可变证据包，包含 canonical daily bars、指标、质量和范围身份。相同内容重试时复用已有数据集，不原地修改历史数据集。

**Decision Chart Projection**：从 Daily Decision Dataset 和策略结果生成的只读图形投影。它只负责把 K 线、指标、策略 overlay、状态区间和质量信息映射到页面，不在前端重新推导交易信号。

**Opportunity View**：用户主动查看某份冻结Dataset时生成的只读机会投影。日线回放维护稳定setup与首次trigger，查看层给出研究区间、失效条件、参考价、知识截止和查看时点。旧图表缺少逐bar观测时间时明确标记历史重建与未知来源资格；没有合格盘中报价不能声称当前可参与。读取接口不补采、不写回历史，未来价格标签仅供离线质量研究使用。

**Decision Scope**：一次决策冻结的对象范围，类型为 `instrument`、`group` 或 `observation_run`，并包含对应标的、组版本、交易日和基准。决策执行期间不能扩大范围。

**Strategy Decision**：由确定性策略根据冻结证据产生的结构化结果，包含 stance、action、horizon、原因、风险、确认条件、失效条件和证据引用。它不是原始指标，也不是模型观点。

**Symbol Setup Projection**：从 Strategy Decision v2 的机器可读 `setup_plan` 按标的投影 primary setup。它按方向、阶段、确认距离、策略分数和策略名确定稳定顺序，并直接保留触发价、当前距离与失效位；不能从展示文案或 `visual_anchors` 反推。

**Deterministic Synthesis**：对同一数据集内策略结果进行规则化汇总的只读结果。数据集级结果保留横截面总览，同时必须提供按标的独立计算的 `symbol_summaries`；个股和观察组只能消费自身范围内的合成结果，不能把其他标的的相反方向当作本标的策略冲突。它不改变任何单一策略结果，也不声称概率或收益保证。

**Group Decision Phase**：Observation Group 的当前强弱状态与边际变化组合。`state` 表达当前 level，`impulse` 表达相对前一交易日的 `improving`、`deteriorating` 或 `stable`；强势但恶化、弱势但改善时，动作降为 `watch`，避免把滞后的绝对状态直接当作新增优先级。

**Sample Support**：组级结论的样本支持事实，包含有效成员数、请求成员数、覆盖率、证据强度和排名资格。单标的组以及少于 4 个有效成员的组为低证据，动作最高为 `watch`，不得成为领先主题。

**Cross-sectional Group Ranking**：在同一 Observation Run 内，对具备 Sample Support 排名资格的组进行波动调整和截面 mid-rank 标准化。排名以风险调整后的相对收益、MA20/MA50 广度和领涨分散度为因子；缺失因子不填零，少于 5 个合格组时排名不可用。

**Discovery Projection**：从一份冻结 Observation Run 及其已有的可比前序快照生成的只读变化列表。它复用横截面和报告事实，按证据身份去重并保留全部观察组归属；不采集数据、不修改策略结果，也不把缺少前序证据解释为没有变化。第一阶段只提供明确的指标状态变化，策略与组阶段事件在输入契约成熟前标为 deferred。

**Focus List**：个人重点关注的 symbol 集合。它与 Instrument Universe 的采集角色、观察组成员和运行资格分离；加入或移出 Focus List 不改变市场数据采集范围。

**Option Evidence**：在同一次 Observation Run 中按配置标的和到期日采集的期权快照及链路计算结果，包含 Option Spot、IV、Expected Move、DEX、GEX、Gamma Wall、Gamma Profile 和 Max Pain。

**Regular-Close Alignment**：用交易所官方 regular close 与期权结构价位进行的确定性邻近比较。`near_max_pain` 和 `near_dex_wall` 只表示邻近候选；`dex_influence_candidate` 表示结构性影响候选，不表示 DEX 已经造成收盘。

**History Collection State**：按 provider 和 symbol 保存的日线采集状态。`access_state` 描述额度准入和采集进度，`quality_state` 描述数据是否达到日线证据要求；二者独立。

**Capacity Plan**：由当前 Universe、canonical daily bars 和 OpenD 实时额度形成的短期容量计划，区分 cache hit、admitted 和 pending quota。计划用于解释和优先级排序，真正请求前仍须通过权威准入检查。

**Data State**：页面和接口使用的事实状态，包括 `live`、`mock`、`placeholder`、`partial`、`unavailable` 和 `failed`。状态必须与质量说明一起展示。

**Remote Decision Run**：用户确认后，Anomalo 对一份精确 Workflow Binding 和冻结输入执行的远程运行记录。Urus 保存输入指纹、远端运行身份、事件、结果 envelope 和校验状态；重跑创建新记录。

**Workflow Binding**：把一个远程决策意图绑定到已发布的精确 Workflow Ref、definition hash、compiled hash、能力清单 hash 及输入输出 Schema。运行时只允许使用 active binding，不能按页面点击动态替换流程。

**Decision Harness**：围绕数据冻结、确定性策略、期权分析、页面投影和远程决策接缝的整体模块边界。Urus 拥有事实和校验；远端服务只执行被绑定且已冻结的决策输入。

## 关系

```text
Instrument Universe
        │
        ├── Observation Group version
        │          │
        │          └── Observation Run
        │                 ├── canonical daily_bars
        │                 ├── Daily Decision Dataset
        │                 ├── Group Daily Snapshot
        │                 ├── Option Evidence + Regular-Close Alignment
        │                 └── Observation Report
        │
        └── instrument Decision Scope ──┐
                                        ├── Chart Projection
                                        ├── Strategy Decision
                                        └── Deterministic Synthesis

Frozen evidence + active Workflow Binding ──> Remote Decision Run
```

- 一个 Observation Run 可以包含多个观察组，但每个组都引用不可变的组版本。
- 日线证据只从 `daily_bars` 构建；OpenD 返回的 `history_snapshots` 只负责写入 canonical 存储。
- 期权属于 Observation Run 的收市后证据，不创建独立的事实运行链；前端在个股工作区内展示它。
- Regular-Close Alignment 只有在官方收盘价可用时才生成正式标记。Option Spot fallback 只能作为参考位置。
- 远程 AI 只能读取冻结的 Dataset、Chart Projection、Group Snapshot 或 Observation Run 投影，不能读取未声明范围的数据源。
- Observation Report 中同一个 Strategy Decision 即使属于多个重叠观察组，也只能进入一次机会/风险泳道；报告用 `group_ids` 保留其全部组归属。`bearish` 只进入风险泳道，不能被 `confirmed` 状态误归入机会泳道。
- 数据源不可用时保留失败原因和质量状态；不要用历史快照、缓存或模型输出补齐当前事实。

## 当前代码边界

| 边界 | 主要实现 |
| --- | --- |
| 配置与 Universe | `backend/app/core/config.py`、`backend/app/repositories/universe.py`、`backend/app/api/universe.py` |
| OpenD 与额度 | `backend/app/integrations/moomoo.py`、`backend/app/integrations/moomoo_options.py`、`backend/app/services/history_quota.py`、`backend/app/services/market_data_collection.py` |
| 日线证据 | `backend/app/decision_harness/market_evidence.py`、`backend/app/repositories/daily_evidence.py` |
| 期权分析 | `backend/app/services/options_collection.py`、`backend/app/analytics/options.py`、`backend/app/analytics/options_volatility.py` |
| 收市观察 | `backend/app/services/observation.py`、`backend/app/decision_harness/group_observation.py`、`backend/app/decision_harness/observation_report.py` |
| Setup、组阶段与排名 | `backend/app/decision_harness/setup_projection.py`、`backend/app/decision_harness/group_phase.py`、`backend/app/decision_harness/group_ranking.py` |
| 远程决策 | `backend/app/decision_harness/remote_workflow.py`、`backend/app/services/remote_decision_supervisor.py`、`backend/app/integrations/anomalo_workflow.py` |
| 页面 | `frontend/src/views/InstrumentDecisionView.vue`、`frontend/src/views/GroupObservationView.vue`、`frontend/src/views/ObservationRunView.vue` |

## 变更规则

1. 新数据类型先定义来源、时间、质量和不可变身份，再接入页面。
2. 新采集能力必须加入 Observation Run 的单一入口，并复用主机级锁和限频器。
3. 新页面优先消费后端冻结投影；前端只做格式化、筛选和交互，不重新计算事实。
4. 任何远程决策入口都必须经过 source locator、输入 hash、binding 和 artifact 校验。
5. 数据库迁移目录可以保留历史版本，但运行代码、配置示例和开发文档只描述当前模型与接口。

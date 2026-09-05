# Daily Decision Harness 开发设计

本文描述当前 Urus 日线与收市观察产品的实现边界。它是开发约束，不是历史版本迁移记录；数据库迁移历史另行保留在 `backend/alembic/versions/`。

## 1. 目标

Daily Decision Harness 把一个已确定的标的范围转换为可审计的日线证据、指标、图表、确定性策略和可选远程决策输入。它需要回答：

1. 本次观察使用了哪个 Universe、观察组和交易日？
2. 每个标的的日线是否完整、来源是否明确、是否存在质量缺口？
3. 期权结构如何与官方收盘价对齐，是否接近 Max Pain 或 DEX Wall？
4. 页面展示的策略结果是否来自同一份冻结证据？
5. 用户主动请求远程 AI 时，发送的输入是否精确、可复现且受校验保护？

非目标：下单、自动交易、持仓管理、实时期权订阅、前端独立计算指标，以及由模型改写原始市场事实。

## 2. 总体结构

```text
Universe / Observation Group
          │
          ▼
ObservationRunService
  ├─ HistoryCapacityService + HistoryAdmission
  ├─ DailyMarketEvidenceService
  │    ├─ canonical daily_bars
  │    ├─ indicators / Chart Projection
  │    └─ deterministic strategies
  ├─ OptionsCollectionService
  │    ├─ option snapshots / chains
  │    └─ DEX/GEX/Max Pain/Expected Move
  ├─ Regular-Close Alignment
  └─ Group Daily Snapshot + Observation Report
          │
          ├─ Instrument / Group / Run pages
          └─ RemoteDecisionCompiler → Remote Decision Supervisor
```

模块通过窄 Interface 连接：数据适配器返回 JSON-compatible payload，证据服务负责冻结与 hash，页面只消费 API 投影。

## 3. 核心生命周期

### 3.1 Universe 和观察组

`InstrumentUniverseRepository` 保存版本化标的配置。`ObservationGroupSyncService` 把启用的 equity watchlist 投影为“指标推荐”，并把启用且采集日线的主题成员投影为版本化 Observation Group；两者范围相互独立。如果配置了远端 Universe 来源，会先校验来源 URL、revision 和 content hash，再保存本地审计记录。

观察组保存成员和基准的版本。保存新成员不会覆盖历史版本；Observation Run 必须引用一个 active 版本，不能在运行中重新解析当前列表。

### 3.2 Observation Run

`POST /api/observation/runs` 是唯一的完整收市观察入口。请求可以指定 group、交易日和 cutoff；调度器使用 `trigger_mode=scheduled`，接口也保留 `manual` 触发以便诊断和补采。

运行步骤：

1. 根据交易所日历确认交易日已经完成官方收盘。
2. 固定 Universe revision、观察组版本、group ids 和幂等键。
3. 通过容量计划和请求前准入，决定哪些历史标的可以读取。
4. 读取或补齐 canonical `daily_bars`，生成 Daily Decision Dataset、Chart Projection 和确定性策略。
5. 在同一主机级采集锁内串行读取配置期权标的的期权元数据、链路和合约快照。
6. 用官方 regular close 构建收市后邻近结果。
7. 为每个观察组保存 Group Daily Snapshot，并保存汇总报告、期权结果和质量状态。

重复请求使用稳定的 idempotency key 返回已有结果；输入的 Universe、组版本或交易日不同，才会形成新的运行记录。

### 3.3 调度器

`backend/scripts/schedule_market_data_collection.py` 只维护一个 `post_close_observation` slot。它负责：

- 按 `exchange-calendars` 排除周末、假日并处理提前收市；
- 启动或连接后端；
- 先同步观察组，再提交一个 Observation Run；
- 记录完成状态并在受控窗口内重试失败 slot。

调度器不执行 AI、不直接调用 Moomoo、不拆分股票和期权任务，也不创建第二个采集生命周期。

## 4. 日线证据合同

### 4.1 canonical 存储

OpenD 的历史返回通过 `history_snapshots` 进入 `DailyEvidenceRepository.persist_collected_history()`，规范化为 `daily_bars`。Dataset 构建只读取 canonical 表，不读取适配器临时对象或页面缓存。

每个 Dataset 至少记录：

- `dataset_id`、schema version、feature version、交易日和 cutoff；
- `Decision Scope`、Universe/观察组版本和 benchmark；
- 每个 symbol 的 bar manifest、输入 hash、样本数和最新日期；
- 指标快照 id、Chart Projection、策略结果和总体质量；
- `content_sha256`，且不包含可变运行时间和数据库生成 id。

### 4.2 完整性和质量

目标交易日必须是交易所正式 session，最后一根日 K 必须不晚于 cutoff 且属于该交易日。样本不足、缺失、冲突和来源不可用分别进入质量字段；不能用旧值静默补齐。

`quality.status` 描述证据可用程度；`History Collection State` 额外描述额度访问状态。历史数据已经获取但样本不足，仍可能是 `acquired + partial`，不能误报为 quota 问题。

## 5. 期权证据与收市对照

期权是 Observation Run 的子结果，前端在个股工作区的股票证据旁显示，不建立独立的事实页面或旧运行链。配置标的由 `Settings.options_collection_symbol_list` 得到，采集服务按 symbol 和 expiration 生成结构结果。

完整链路用于计算：

- Max Pain；
- Call/Put/Net DEX Wall；
- calls、puts 和 net 的 DEX/GEX totals；
- strike GEX sign changes、Gamma Zones、Spot Gamma Profile；
- ATM IV、Expected Move 以及 IV/HV context。

页面可裁剪远离现价的执行价行，但 totals、walls 和邻近判断必须来自完整链路。期权调用使用独立的 metadata、chain 和 snapshot rate class，但与日线共享 `MoomooCollectionCoordinator` 的文件锁；重启不能绕过已保存的 pacing timestamp。

### 5.1 Regular-Close Alignment

`build_post_close_option_alignment()` 只接受 Dataset 中目标交易日的官方 close。它比较 close 与 Max Pain、Call DEX Wall、Put DEX Wall 和 Net DEX Wall，并记录距离、阈值和 flags。

- `near_max_pain`：close 落在 Max Pain 邻近阈值内；
- `near_dex_wall`：close 落在任一 DEX Wall 邻近阈值内；
- `dex_influence_candidate`：结构性邻近候选，不是因果结论；
- 没有官方 close 时：alignment 为 unavailable，Option Spot 只能作为参考位置。

默认阈值来自当前选项分析配置（目前为 `0.60%`），必须在结果中保留使用的阈值和 source phase，方便页面解释。

## 6. 容量与限频

### 6.1 日线额度

`HistoryCapacityService` 根据实时 OpenD quota、保护性 reserve、当前 canonical bars 和 Universe 版本生成 Capacity Plan。每个 symbol 只有以下几类决定：

- `cache_hit`：已满足日线要求，不消费新历史额度；
- `admitted`：通过当前额度和优先级准入；
- `pending_quota`：额度不足或 quota 不可读，等待下次运行。

`HistoryAdmission` 在每个实际历史请求前重新读取/确认容量，并在成功写入和校验 `daily_bars` 后推进状态。Quota 不可读时 fail-closed，不发起新增历史请求。

### 6.2 主机级协调器

`MoomooCollectionCoordinator` 用文件锁确保同一台机器只有一条 OpenD 采集路径。锁文件只保存各 rate class 的下次允许时间：

| rate class | 用途 |
| --- | --- |
| `snapshot` | 股票/ETF 批量报价 |
| `history` | 完整日线 |
| `option_metadata` | 期权标的和到期日元数据 |
| `option_chain` | 期权链路 |
| `option_snapshot` | 合约快照 |

每个适配器调用 OpenD 前都要申请相应 slot。适配器内部缓存只去重同一次运行的重复读取，不替代持久化状态，也不把失败伪装成成功。

## 7. 远程决策接缝

远程 AI 不是采集步骤。用户从个股、组或交叉分析页面发起请求后：

1. `RemoteDecisionCompiler` 根据 immutable source locator 读取 Dataset、Chart Projection、Group Snapshot 或 Observation Run；
2. 校验 scope、数据集 hash、输入大小、binding、Workflow Ref 和 Schema；
3. 编译带 `input_sha256` 的冻结 input envelope；
4. `RemoteDecisionSupervisor` 把运行放入有界队列，跟踪远端事件和状态；
5. 返回后校验 artifact 的 scope、输入 hash、Schema、完整性和 evidence refs；
6. 只保存通过校验的结果，失败保留安全错误和事件轨迹。

页面刷新不能自动重新提交；重跑必须显式创建新的 Remote Decision Run。远端结果不能修改 Dataset、Chart Projection、策略或期权事实。

Deterministic Synthesis 先在每个 symbol 内部合成，再保留数据集级横截面总览。个股页面读取对应的 `symbol_summaries[symbol]`；Group Daily Snapshot 只投影组内 symbol summaries。不同标的之间的一多一空属于横截面分化，不得显示成某个个股自身的策略冲突。

Observation Report 的机会与风险泳道按 `decision_id` 去重，并通过 `group_ids` 保留重叠观察组归属。bearish Strategy Decision 只能进入风险泳道；confirmed 只有在非 bearish 时才进入机会泳道。

### 7.1 P1 决策阶段、Setup 与排名

- Strategy Decision v2 增加机器可读 `setup_plan`，冻结方向、阶段、触发操作符/价格、观察价、剩余价格/百分比/ATR，以及失效操作符/价格/距离。
- Symbol Setup Projection 按 `decision_id` 去重，为每个标的稳定选择 primary setup；前端只展示该投影，不重新计算价位。
- Group Daily Snapshot v4 用 Group Decision Phase 合并 `level × impulse × setup`，输出 `leader`、`deteriorating`、`reversal_watch` 等 category。
- Sample Support 对单标的和少于 4 个有效成员的组标记低证据、取消排名资格，并把动作上限限制为 `watch`。
- Observation Report v2 输出领先主题、恶化主题和潜在反转主题；旧 `group_rankings`、`improving_groups`、`deteriorating_groups` 暂时保留兼容。
- Cross-sectional Group Ranking 仅在至少 5 个合格组时可用。20 日超额收益先除以预期 20 日 tracking move，再与 MA20/MA50 广度、`1 - leader_concentration` 一起做 mid-rank 标准化到 `[-1, 1]`。缺失因子不填零。

## 8. 前端信息架构

- `/instruments/:symbol` 是个股工作区，包含价格、技术证据、策略结果和期权结构；
- `/groups/:groupId` 是观察组工作区，包含组相对强弱、广度、轮动、热力图和确定性组结果；
- `/observation-runs` 展示一次收市观察的组结果、质量、期权采集状态和运行历史；
- `/indicators`、`/strategies` 展示跨标的确定性投影；
- `/decision-runs/:localRunId` 展示远程决策生命周期；
- `/settings/universe` 维护版本化 Universe。

期权没有独立导航入口；`/options` 仅把已有书签导向对应个股工作区。所有页面从 API 读取结构化结果，组件只负责格式化、筛选、标记和交互。

## 9. 失败和幂等

| 场景 | 当前行为 |
| --- | --- |
| Universe 来源不可用 | 默认 fail-closed；显式允许 stale 时使用最近成功 revision 并标记 `stale` |
| OpenD quota 不可读 | 停止新增历史请求，保留 warning 和 `pending_quota` |
| 日线部分成功 | 保存可用 bars 和质量状态，Dataset/运行标记 `partial` |
| 期权采集失败 | 保留 `failed`/`unavailable` 及错误，股票观察仍可完成但运行状态为 `mixed` |
| 官方 close 缺失 | 不生成正式 alignment，只展示 Option Spot reference |
| 同一运行重复提交 | 由 idempotency key 返回原记录 |
| 页面或服务重启 | 使用数据库运行记录、锁文件 pacing 和远程事件序号恢复 |

## 10. 当前实现索引

| 能力 | 实现 |
| --- | --- |
| 收市观察编排 | `backend/app/services/observation.py` |
| 日线冻结 | `backend/app/decision_harness/market_evidence.py` |
| 组快照 | `backend/app/decision_harness/group_observation.py` |
| Setup、组阶段与排名 | `backend/app/decision_harness/setup_projection.py`、`backend/app/decision_harness/group_phase.py`、`backend/app/decision_harness/group_ranking.py` |
| 期权采集 | `backend/app/services/options_collection.py`、`backend/app/integrations/moomoo_options.py` |
| 期权计算 | `backend/app/analytics/options.py`、`backend/app/analytics/options_volatility.py` |
| 历史额度 | `backend/app/services/history_quota.py`、`backend/app/services/market_data_collection.py` |
| 远程决策 | `backend/app/decision_harness/remote_workflow.py`、`backend/app/services/remote_decision_supervisor.py` |
| 个股页面 | `frontend/src/views/InstrumentDecisionView.vue` |
| 组/运行页面 | `frontend/src/views/GroupObservationView.vue`、`frontend/src/views/ObservationRunView.vue` |

修改上述边界时，必须同步更新 API schema、冻结 hash、前端类型、对应测试和 `CONTEXT.md`。

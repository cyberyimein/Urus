# Phase D AI 决策流程与按钮设计

> 状态：Phase D 开发交接基线（D0 发布与鉴权已完成，D4 真实联调中）
> 日期：2026-08-27
> 前置条件：Urus Phase C 的确定性日 K、Strategy Decision、Observation Run 和横截面投影已经完成；Anomalo 已提供已发布 Workflow 的运行、事件、状态和停止 Interface。
> 上位设计：[日 K Decision Harness 开发设计](daily-k-decision-harness-development-design.md)
> 领域词汇：[Urus Research Context](../CONTEXT.md)
>
> 开发硬门槛：Anomalo 已完成通用 Workflow Runtime；目标实例已发布本文四个 Urus 决策 Workflow，并配置了仅限四个 Ref 的 Urus 运行客户端。生产 AI 按钮仍须通过 D4 的真实成功、失败、恢复和越界 fixture 后才能默认启用。

## 1. 结论

Phase D 的 AI 不是另一个技术指标计算器，也不是自由聊天入口。它是冻结证据之上的**策略仲裁与解释层**：

```text
用户看到确定性事实与 Strategy Decision
  → 主动表达一个明确的评估意图
  → Urus 冻结本次输入并创建 Remote Decision Run
  → 后台 Worker 调用一个已发布的 Anomalo Workflow Ref
  → Anomalo 执行模型、节点、重试、超时和事件记录
  → Urus 校验 Workflow hash、输入范围和结构化输出
  → 页面并列展示确定性结论与 AI 结果
  → 用户可以重跑，但旧结果永不覆盖
```

V1 提供四个明确入口：

1. 个股页：**AI 策略仲裁**；
2. 组页：**AI 评估整个组**；
3. 指标横截面页：**AI 寻找指标异常**；
4. 策略横截面页：**AI 寻找策略关注项**。

四个入口共享一个深的 Remote Workflow Module，并各自绑定不同的 Workflow Ref；它们使用同一窄外层 Input/Artifact Schema，由 `intent_type` 和受限字段承载不同语义。它们不会跳转到聊天页，不会让 AI 查询“最新行情”，不会让 AI 新增 symbol，也不会覆盖 Deterministic Synthesis、Strategy Decision 或横截面排名。Observation Run 只负责数据采集、证据冻结和确定性报告，不提供 AI 复核入口。

## 2. 产品目标与意义

AI 层要解决的不是“把已有数字再总结一次”，而是四类确定性程序不擅长的问题：

- 多个完整策略冲突时，当前证据更支持采信谁；
- 组内或跨组结构是否一致，还是被少数极端 symbol 误导；
- 一个指标或策略在不同组中的异常是否值得继续下钻；
- 在信息不足、策略都不适用或风险不对称时，明确输出 `no_action`。

它的业务意义是：

- **决策更聚焦**：把几十条策略输出压缩为有限的关注、等待和回避项；
- **冲突可解释**：保存 AI 采信、否决和放弃的结构化依据；
- **结果可评价**：后续可以分别做 Strategy Evaluation 与 Arbitration Evaluation；
- **重复可比较**：同一冻结输入可以用不同 Workflow 或模型版本重跑；
- **失败可隔离**：Anomalo 失败时，确定性页面仍然完整可用。

V1 不证明 AI 比确定性基线更好；V1 先建立可审计、可回放、可比较的运行链路，为 Phase E 的评价提供真实样本。

## 3. 两类按钮不能混淆

页面上存在两类看似相近、实际含义完全不同的按钮。

| 按钮类型 | 例子 | 是否创建或更新确定性证据 | 是否创建 Remote Decision Run |
| --- | --- | --- | --- |
| 确定性运行 | 执行收市观察、执行全部观察组 | 是 | 否 |
| AI 评估 | AI 策略仲裁、AI 评估整个组、AI 寻找指标异常、AI 寻找策略关注项 | 否，只引用已冻结证据 | 是 |

因此：

- 组页现有“执行收市观察”继续只负责冻结数据、计算指标和运行全部策略；
- Observation Run 页现有“执行全部观察组”继续只产生 deterministic-only 报告；
- Observation Run 完成后为指标和策略横截面提供冻结输入，但自身不触发 AI；
- AI 按钮必须单独出现，并且只有绑定到一份已完成的冻结证据后才能启用；
- 页面刷新、切换 Tab、切换 symbol 或选择历史 run 都不能隐式启动 AI。

## 4. 按钮总表

| 页面 / 按钮 | 用户问题 | 冻结范围 | 拟发布 Workflow Ref | 期待结果 | 意义 |
| --- | --- | --- | --- | --- | --- |
| 个股页 / AI 策略仲裁 | 这个 symbol 当前应采信哪个策略，还是不行动？ | `instrument` | `urus-instrument-arbitration@2` | 一条 Strategy Arbitration Decision | 保留策略冲突并给出有界选择 |
| 组页 / AI 评估整个组 | 这个组是真的广泛走强，还是少数个股驱动？ | `group` + group version | `urus-group-arbitration@2` | 组级判断、组内优先项、风险项 | 检查组内一致性并缩小下钻范围 |
| 指标页 / AI 寻找指标异常 | 当前指标卡片中哪些数值、变化或组内分歧异常并值得关注？ | `observation_run` + indicator lens | `urus-indicator-review@2` | 按重要度排序的异常卡片和下钻对象 | 从卡片堆中筛选值得看的值，不产生交易结论 |
| 策略页 / AI 寻找策略关注项 | 当前策略卡片中哪些 setup、异常或状态突变值得关注？ | `observation_run` + strategy lens | `urus-strategy-review@2` | 按重要度排序的策略卡片和下钻对象 | 从卡片堆中筛选研究对象，不重算策略 |

### 4.1 与 Phase C 页面颗粒度的对齐结论

Phase D 不能只按路由名称接按钮，AI 的一次运行对象必须与 Phase C 页面当前冻结的 read model 完全一致：

| Phase C 页面 | 当前页面实际颗粒度 | Phase D 一次 AI 运行颗粒度 | 对齐状态 | 接入前缺口 |
| --- | --- | --- | --- | --- |
| 个股页 | 一个 symbol + 一份 Daily Decision Dataset + 全部 Strategy Decision | `dataset_id + symbol`，读取全部策略 | 基本对齐 | LOCAL DEMO 必须禁用 AI；当前选中的策略图层不能缩小正式输入；两个视觉按钮共享一个 action |
| 组页 | 一个 group 的 `latest_snapshot` | `observation_run_id + snapshot_id + group_version_id` | 已对齐 | 页面通过 `/observation/runs/:runId/groups/:groupId` 绑定精确历史 snapshot，不能提交时再取 latest |
| 指标横截面 | 一个 Observation Run + 一个已选 indicator lens + 该 lens 下全部组/symbol 卡片 | `run_id + indicator lens identity + projection hash`，一次扫描整页卡片 | 高度对齐 | 增加确定性 attention features，避免让模型从裸数值自行发明异常阈值 |
| 策略横截面 | 一个 Observation Run + 一个已选 strategy lens/implementation + 该 lens 下全部组/symbol 卡片 | `run_id + strategy lens identity + projection hash`，一次扫描整页卡片 | 基本对齐 | 增加确定性 attention features；当前 projection 没有跨策略冲突证据，因此 V1 不做跨策略冲突判断 |
| Observation Run | 一次收市采集、共享 dataset、全部组快照和 deterministic-only 报告 | 不运行 AI | 对齐 | 保持采集与冻结职责，不增加 AI 状态 |

横截面的“整页卡片”只指当前已选择的一个 lens。一次 AI 调用扫描该 lens 下所有可见业务卡片：

```text
一个 Observation Run + 一个 indicator/strategy lens
  → 多个 group card
  → 每个 group 下多个 symbol card
  → 一次 AI 异常筛选
```

V1 不采用以下两种颗粒度：

- **每张卡一次 AI**：调用数随 symbol 数膨胀，无法做横截面对比；
- **一次扫描所有 indicator/strategy Tab**：输入范围超过 Phase C 当前单 lens read model，结果也无法稳定回到当前页面。

如果未来需要“全指标总扫描”或“全策略总扫描”，应先新增一个确定性的 multi-lens projection，再设计新的入口；不能让 AI 绕过页面 read model 自己批量读取所有 lens。

## 5. 所有 AI 按钮共享的点击流程

### 5.1 点击前 Gate

按钮只有在以下硬条件全部满足时才启用：

- 已绑定唯一的 Daily Decision Dataset 或 Observation Run；
- 冻结输入具有 `content_sha256`，Strategy Decision 具有版本和 hash；
- scope、group version、lens version 唯一且可读；
- 对应 Workflow Binding 处于 `active`，并保存预期 `workflow_ref` 与 `compiled_hash`；
- 当前身份有调用该 Workflow Ref 的权限；
- 没有相同 `request_intent_id` 正在提交。

以下属于软警告，不必一律禁用：

- 部分 symbol 质量不足；
- 某些策略为 `not_applicable`；
- Observation Run 为 `mixed`，但仍有完整可评估组。

软警告必须在确认框列出受影响范围，并进入 AI 输入的 `quality` 字段。AI 必须能够输出 `insufficient_evidence`，不能把缺失值当成中性值。

### 5.2 第一次点击：只打开确认框

第一次点击不创建数据库记录，也不调用 Anomalo。确认框固定显示：

- 用户意图和按钮类型；
- trading date、cutoff time、dataset/run ID 与短 hash；
- scope、组版本、symbol 数或 lens；
- Strategy Decision 数量、错误数和不适用数；
- Workflow Ref、Workflow 显示版本和预期模型/Skill 标签；
- 数据质量警告；
- “AI 结果不会覆盖确定性结果”的提示。

V1 不在确认框承诺精确费用；若 Anomalo 以后返回可用预算信息，可增加预算上限和预估区间。

### 5.3 确认：先记本地账，再调用远端

用户确认后按以下顺序执行：

```text
生成一次 request_intent_id
  → 编译不可变 Remote Decision Input
  → 计算 input_sha256
  → 创建本地 Remote Decision Run(status=queued)
  → 提交后台工作项
  → 立即向前端返回 202 + local_run_id
```

前端请求不能等待整个 Workflow 完成。Anomalo 的非流式运行 Interface 会收集到 terminal 才返回，因此 Urus 的后台 Worker 应使用流式运行 Interface，或在独立工作进程中执行非流式调用；不能让浏览器 HTTP 请求长时间挂起。

### 5.4 后台执行

Worker 通过运行期 service token 调用：

```text
POST /api/workflows/:name/versions/:version/runs/stream
GET  /api/runs/:runId
GET  /api/runs/:runId/events?after_sequence=N
POST /api/runs/:runId/stop
```

启动请求只包含：

```json
{
  "input": {},
  "idempotency_key": "sha256:...",
  "metadata": {
    "source": "urus",
    "local_run_id": "uuid",
    "trigger_source": "instrument_page"
  }
}
```

相同调用身份、Workflow Ref 和 `idempotency_key` 必须得到同一个 Anomalo run。网络重试复用原 key；用户明确点击“用同一证据重新运行”时创建新的 `request_intent_id` 和新 key。

### 5.5 运行中页面

页面的 AI 区域原地更新，不遮挡确定性内容：

```text
尚未运行
  → 排队中
  → 提交中
  → 运行中（当前节点 / 已用时）
  → 成功 | 失败 | 已停止 | 超时
```

Anomalo 顶层状态只有 `queued/running/succeeded/failed/stopping/stopped`。Urus 可以把带有明确 timeout error code 的失败投影为 `timed_out`，但不得伪造 Anomalo 不存在的 `partial` 运行状态。部分证据或部分结论应记录在成功 artifact 的 `completeness=partial` 中。

运行中可用按钮：

- **查看执行详情**：打开本地 `/decision-runs/:runId`；
- **停止**：二次确认后请求远端 stop；
- 页面离开或浏览器关闭：不自动停止。

### 5.6 成功后的动作

- **查看证据引用**：聚焦到确定性页面对应位置；
- **查看执行详情**：展示节点事件、usage、hash 和错误，但不默认展示 Provider-returned Reasoning；
- **用同一证据重新运行**：创建新 Remote Decision Run，不覆盖旧 artifact；
- **选择历史结果**：同一 dataset/run 的多个结果按时间和 Workflow Ref 切换。

## 6. 统一输入契约

四个 Workflow 共享一个窄的外层 Interface：

```json
{
  "schema_version": "urus.remote_decision_input.v1",
  "intent": {
    "type": "instrument_arbitration",
    "request_intent_id": "uuid",
    "trigger_mode": "user",
    "trigger_source": "instrument_page"
  },
  "scope": {},
  "dataset": {
    "dataset_id": "uuid",
    "schema_version": "urus.daily_decision_dataset.v1",
    "content_sha256": "...",
    "trading_date": "2026-08-25",
    "cutoff_time": "..."
  },
  "evidence": {},
  "strategy_decisions": [],
  "deterministic_synthesis": {},
  "quality": {},
  "constraints": {
    "allowed_symbols": [],
    "allow_latest_data_lookup": false,
    "allow_symbol_expansion": false
  },
  "rows": [],
  "evidence_refs": [],
  "input_sha256": "<computed from the canonical payload above>"
}
```

外层保持一致，`evidence` 根据意图使用有界子 Schema。不要把整份前端 read model、任意 prompt 字符串或数据库对象直接传给 Anomalo。

输入必须满足：

- 每个事实有 Evidence Reference；
- 每个 Strategy Decision 保存其 ID、策略版本、实现 hash、horizon 和完整状态；
- 输入数组使用稳定排序后计算 `input_sha256`；
- 输入只来自本次冻结 scope；
- 用户在 UI 中的排序、筛选和折叠状态不改变正式输入，除非它本身就是 lens 参数。

## 7. 四条具体决策流程

### 7.1 个股：AI 策略仲裁

**入口**：`/instruments/:symbol`。页面顶部和 AI 卡片目前有两个入口，Phase D 应共享同一个 action 和同一个状态；不能因点击不同位置创建两类 run。

**输入**：

- instrument scope 与唯一 symbol；
- Daily Decision Dataset 摘要、市场/benchmark 上下文和质量；
- 该 symbol 的全部 Strategy Decision，包括错误和未适用项；
- Deterministic Synthesis；
- 可选的只读 group membership 摘要，但不得扩大决策 symbol；
- Phase E 前不启用 Case Card 检索。

页面的 `activeStrategy` 只是图层和详情筛选，不进入正式输入；AI 始终读取该 symbol 在本次 dataset 中的全部 Strategy Decision。页面处于 `LOCAL DEMO` 回退时不得启用 AI。

**AI Task**：判断采信一个策略、在兼容时组合多个策略，或全部否决并输出 `no_action`。V1 建议产品先允许“采信一个或全部放弃”；只有 Schema 和评价逻辑支持后再开放组合。

**期待输出**：`urus.remote_decision_artifact.v1`，其中 `intent_type=instrument_arbitration`。

```text
decision = select_one | no_action | insufficient_evidence
selected_strategy_ids[]
rejected_strategy_ids[] + rejection_reason_code
action / horizon / confidence
conflicts[] / risks[]
confirmation_conditions[] / invalidation_conditions[]
decision_rationale[] + evidence_refs[]
```

**意义**：直接回答“策略冲突时听谁”，并为后续 Arbitration Evaluation 保存被选和未选策略。

### 7.2 组：AI 评估整个组

**入口**：`/groups/:groupId`。新增 AI 按钮；现有“执行收市观察”保持确定性语义。

**输入**：

- group scope、冻结 group version、benchmark 与成员清单；
- group features、group decision、group Strategy Decision；
- 全部组内个股的 Strategy Decision 摘要；
- leaders、laggards、breadth、dispersion、relative strength、状态变化；
- symbol 与 group 质量统计。

提交身份必须包含 `observation_run_id + snapshot_id + group_version_id + dataset_id + content_sha256`。Phase C 当前 `GET /observation/groups/:groupId` 只返回 latest snapshot，Phase D 必须先提供按 run/snapshot 读取的 Interface，并让 URL 或页面状态明确显示所选 run；不能只提交 `group_id` 后由后端临时选择最新快照。

**AI Task**：判断组级信号是广泛一致、少数领涨、内部冲突还是证据不足；给出有限的下钻优先项和风险项。它不为每只股票重新做完整个股仲裁。

**期待输出**：`urus.remote_decision_artifact.v1`，其中 `intent_type=group_arbitration`。

```text
group_verdict / action / confidence
supporting_group_strategy_ids[] / rejected_group_strategy_ids[]
internal_consistency / leadership_concentration
priority_symbols[] / risk_symbols[]
conflicts[] / risks[] / evidence_refs[]
```

**意义**：防止把“少数大涨股票带动的组均值”误判为整个主题走强，并把后续研究集中到少量 symbol。

### 7.3 指标横截面：AI 寻找指标异常

**入口**：`/indicators/:indicatorId?run=:runId`。把当前标记为旧 “Phase E” 的 disabled 占位替换为 Phase D 真实按钮。

**输入**：

- observation_run scope；
- indicator lens 的 ID、版本、单位、阈值与本次 projection hash；
- 各组分布、当前值、前值、转换、异常和质量；
- 当前页面选择的 group 只作为展示焦点，不缩小正式横截面输入，除非用户明确选择“仅评估此组”。V1 不提供该变体。

正式 AI 输入直接引用当前 `CrossSectionService.indicator_projection` 的完整不可变 projection，而不是由前端把 DOM 卡片重新拼成 JSON。每个 symbol card 已有稳定 `row.id`、snapshot/dataset identity 和 Evidence Reference；AI 输出的 `card_id` 必须等于该 `row.id`。

Phase D 在 projection 中补充确定性 `attention_features`，至少包含 `within_group_percentile`、`change_percentile`、`threshold_distance`、`is_transition` 和 `quality_flag`。模型负责排序和解释这些候选，不负责创造正式异常阈值。

**AI Task**：像异常筛选器一样审阅当前页面全部指标卡片，找出异常绝对值、异常变化、状态突变、组内分歧和数据质量可疑项，并按值得关注的程度排序。结果必须回指具体 `card_id/group_id/symbol`；不得只输出泛泛的指标解释，也不得产生 Strategy Arbitration Decision。

**期待输出**：`urus.remote_decision_artifact.v1`，其中 `intent_type=indicator_attention`、`lens_type=indicator`。

```text
scan_summary / confidence
notable_cards[]:
  rank / card_id / group_id / symbol
  finding_type(extreme_value|abrupt_change|state_transition|internal_divergence|quality_anomaly)
  observed_value / comparison_value / severity
  why_notable / suggested_drilldown / evidence_refs
dismissed_extremes[] / quality_warnings[]
```

**意义**：用户不必逐张阅读所有卡片；AI 负责从当前指标横截面挑出最值得看的卡片，并区分真实结构性异常与仅仅数值靠前，但不把单一指标冒充策略。

### 7.4 策略横截面：AI 寻找策略关注项

**入口**：`/strategies/:strategyId?run=:runId`。

**输入**：

- observation_run scope；
- strategy lens、策略版本、实现 hash 和 projection hash；
- 每个组与 symbol 的 Strategy Decision、setup stage、score、确认距离和失效条件；
- 组间差异、状态转换、错误和不适用项。

正式输入直接引用当前 `CrossSectionService.strategy_projection` 的完整不可变 projection。每个策略卡片已有稳定 `row.id`、`decision_id`、strategy version、implementation hash 和 Evidence Reference；AI 输出必须回指这些身份。

Phase D 补充确定性 `attention_features`，至少包含 `score_percentile`、`score_change_percentile`、`confirmation_distance_rank`、`is_stage_transition`、`is_new_invalidation` 和 `quality_flag`。当前单策略 projection 没有其他策略的完整输出或确定性冲突标记，因此 V1 不要求 AI 判断跨策略冲突；该职责留在个股/组 Strategy Arbitration Decision。

**AI Task**：审阅当前页面全部策略卡片，筛出 setup 突然进入或离开关键阶段、score 异常、确认距离快速收窄、组内不一致或数据质量可疑的卡片。结果按研究优先级排序并回指具体卡片。AI 不修改策略 score、stage 或阈值。

**期待输出**：`urus.remote_decision_artifact.v1`，其中 `intent_type=strategy_attention`、`lens_type=strategy`。

```text
scan_summary / confidence
notable_cards[]:
  rank / card_id / group_id / symbol / strategy_decision_id
  finding_type(stage_transition|score_outlier|near_confirmation|new_invalidation|cross_group_divergence|quality_anomaly)
  current_stage / previous_stage / severity
  why_notable / suggested_drilldown / evidence_refs
coverage_gaps[] / quality_warnings[]
```

**意义**：用户不必逐张阅读所有策略卡片；AI 负责找出值得立即下钻的 setup、异常和状态突变，为策略研究提供候选，而不是让 AI 改写策略。

### 7.5 Observation Run 明确不运行 AI

Observation Run 的主要职责是收市后采集数据、冻结共享 Daily Decision Dataset、计算指标、运行全部 Strategy Adapter，并生成 deterministic-only 报告。它为两个横截面 AI 入口提供不可变证据，但自身不创建 Remote Decision Run、不生成 AI-enhanced 报告，也没有自动 AI 计划任务。

## 8. 输出验收与展示规则

所有结果使用共同 envelope：

V1 的 `output_schema_version` 统一为 `urus.remote_decision_artifact.v1`；第 7 节各入口的
`decision` 字段和 `notable_cards` 字段承载意图专属语义，后续如需要独立结果 Schema 再以
新的 Workflow 版本发布。

```json
{
  "schema_version": "urus.remote_decision_artifact.v1",
  "intent_type": "instrument_arbitration",
  "scope": {},
  "dataset_id": "uuid",
  "input_sha256": "...",
  "completeness": "complete",
  "decision": {},
  "warnings": [],
  "evidence_refs": []
}
```

Urus 接受 artifact 前必须依序校验：

1. Anomalo `run_id` 与本地 Remote Decision Run 匹配；
2. `runtime_kind=workflow` 且 `target_ref` 等于绑定的 Workflow Ref；
3. `target_hash` 等于绑定时保存的 `compiled_hash`；
4. 本地 `input_sha256` 未改变；
5. 输出符合该按钮声明的结果 Schema；
6. scope、dataset、group version、lens 和 symbol 范围没有扩大；
7. Strategy Decision ID 都来自本次输入；
8. Evidence Reference 只能指向本次冻结证据；
9. 验收后保存不可变 artifact hash。

模型自由文本不得绕过 Schema 成为正式结果。Provider-returned Reasoning 可以随 trace 保存为未校验审计材料，默认折叠且不能被报告引用为证据。

## 9. Anomalo Interface 的实际适配

现有上位设计曾假设“每次点击把 Workflow Definition 发给 Anomalo 做 alignment，再执行同一 JSON”。Anomalo 已实现的实际 Interface 是**管理期发布 + 运行期按精确 Workflow Ref 调用**，Phase D 以实际 Interface 为准：

```text
开发 / 发布期
Urus 仓库中的 Definition
  → GET capability manifest
  → validate
  → import draft
  → publish exact name@integer-version
  → Urus 保存 definition_hash / compiled_hash / capability_manifest_hash

日常运行期
active Workflow Binding
  → POST exact /api/workflows/:name/versions/:version/runs[/stream]
  → GET run / events
  → validate target_ref + target_hash
```

关键决策：

- Urus 仓库仍是业务流程设计的权威来源；
- Anomalo registry 是已发布可执行版本的权威来源；
- 发布不是用户点击按钮时发生，而是受控部署步骤；
- 日常运行只使用 `workflow:run` / `workflow:read` service token，不把管理 token 放进 Urus Web 运行时；
- `Workflow Binding` 取代旧设计中的逐次 Alignment Receipt；
- Workflow Ref 使用 Anomalo 的 `name@integer-version`，业务语义版本通过不可变新整数版本和 Definition labels 记录。

Workflow Binding 最小字段：

```text
intent_type / scope_types
workflow_ref / status(active|disabled|retired)
definition_hash / compiled_hash / capability_manifest_hash
input_schema_version / output_schema_version
published_at / verified_at
```

### 9.1 Anomalo 正式合同核对

本设计以 Anomalo 的 `docs/integrations/urus-workflow.md` 为运行合同，不能从旧 Urus 草案反推远端能力：

- Definition `api_version` 必须是 `anomaloharis.dev/workflow/v1`；
- Capability Manifest 必须是 `anomaloharis.dev/workflow-capabilities/v1`，并且是节点、Preset Model、Plugin Operation 与限制的唯一能力来源；
- Definition 必须恰好有一个 `input` 和一个 `output` 节点，所有节点形成可达 DAG；
- V1 不支持 loop、wait、approval 或 subworkflow；不能在 Urus 设计里假设这些能力；
- Preset Model 和 Plugin Operation 必须使用 Manifest 中的精确 Ref/版本，不能由运行请求覆盖 provider、prompt、tool、credential 或 policy；
- validate 没有副作用；import 只产生 draft；publish 后 `name@integer-version` 不可变；
- 四个表格中的 Workflow Ref 是拟发布名称，只有完成 validate/import/publish、记录 `definition_hash/compiled_hash` 并加入 Urus service client allowlist 后，Workflow Binding 才能进入 `active`；
- 日常运行请求只能发送 `{input, idempotency_key, metadata}`，不能携带 Definition；
- Urus service token 只授予 `workflow:read/workflow:run`，并将 allowlist 限制为四个 active Ref；Urus Web 运行时不持有 admin token。

四个 Workflow Definition 的具体节点和边必须在取得目标实例最新 Manifest 后设计。尤其不能假设结构化 projection 可以直接连入 `preset_model`：若 Preset Model 的输入端口只接受 message，必须使用 Manifest 中真实存在且被授权的 Plugin Operation 做确定性序列化，或选择输入 Schema 兼容的已发布 Preset Model；不得虚构转换节点。

### 9.2 运行与恢复的精确语义

Urus 后台 Worker 优先使用 `/runs/stream` 摄取 NDJSON 事件，并按 `run_id + sequence` 幂等保存。连接中断后使用：

```text
GET /api/runs/:runId
GET /api/runs/:runId/events?after_sequence=:lastSequence
```

同一调用身份、Workflow Ref 和 `idempotency_key` 下，`input` 或 `metadata` 任一变化都会触发 `idempotency_key_reused`。因此首次提交后必须冻结并保存实际发送的 input 和 metadata；网络重试不能重新生成时间戳或改变 metadata。

Urus 验收 `run.target_ref` 和 `run.target_hash`，其中 `target_hash` 必须等于 Workflow Binding 保存的 `compiled_hash`。终态只包括 `succeeded/failed/stopped`；timeout 是停止原因或错误语义，不是 Anomalo 新增的第四种终态。

## 10. 深化后的 Module 与 Seam

### 10.1 Remote Workflow Module

统一 Interface：

```text
prepare(intent, frozen_evidence) -> Remote Decision Input
submit(local_run_id) -> background work item
ingest(event | terminal run) -> normalized local state
accept(run, artifact) -> immutable accepted artifact
stop(local_run_id) -> stop receipt
```

Implementation 隐藏输入编译、稳定 hash、幂等、HTTP 流、事件游标、状态映射、结果验收和持久化。个股、组、指标横截面与策略横截面页面只需要理解 `intent + local_run_id`，因此 Interface 有较高 Depth、调用方获得 Leverage、远端变化保持 Locality。

删除该 Module 后，四个页面都会重复实现 token、URL、hash、状态、重试和验收逻辑，因此它通过 deletion test。

### 10.2 Workflow Adapter Seam

这是一个真实 Seam，至少有两个 Adapter：

- `AnomaloWorkflowAdapter`：调用已发布 Workflow Ref；
- `FakeWorkflowAdapter`：用于离线契约测试、前端状态和失败恢复测试。

不要把现有 `AnomaloAdapter.summarize/investigate` 扩展成一个巨大 message。聊天/事件调用与 Workflow Run 的 Interface、权限、状态和错误模式不同，应共享底层 HTTP 配置，但保持两个独立 Module。

### 10.3 Decision Result Projection Module

统一 Interface：

```text
project(intent_type, accepted_artifact) -> page-specific read model
```

Implementation 负责把三种结果 Schema 投影到四种页面视图，前端不自行推断“AI 选择了哪个策略”或“哪张卡片异常”。该 Module 保持展示语义和 Schema 演进的 Locality。

## 11. Urus 后端 Interface

```text
POST /api/remote-decisions/preflight
  输入 intent_type + source locator
  输出 enabled/blockers/warnings、确认摘要、binding、input_sha256 和 preflight_fingerprint

POST /api/remote-decisions
  输入相同 locator + preflight_fingerprint + request_intent_id
  输出 202 + local Remote Decision Run

GET /api/remote-decisions/:localRunId
  输出状态、结果投影、错误和最新事件 sequence

GET /api/remote-decisions?scope_type=&scope_id=&dataset_id=
  输出同一冻结证据的历史运行

GET /api/remote-decisions/:localRunId/events?after_sequence=
  输出已清洗 Decision Trace 事件

POST /api/remote-decisions/:localRunId/stop
  仅允许 queued/submitting/running/stopping

POST /api/remote-decisions/:localRunId/rerun
  复用冻结输入引用，创建新的 request_intent_id
```

V1 没有用户认证 Module，因此不设计伪安全的一次性 confirmation token。`preflight_fingerprint` 是确定性 hash，绑定：

```text
intent_type
规范化 source locator
冻结 evidence identity/content hash
Workflow Ref/compiled hash
Remote Decision Input hash
```

正式提交时后端必须重新从 source locator 编译输入并比较 fingerprint。任一冻结证据或 Binding 变化时返回 `preflight_stale`，要求重新确认，不能静默换成 latest。`request_intent_id` 由前端在用户确认时生成一次并在网络重试中复用。

## 12. 持久化

建议新增四个逻辑实体：

### `decision_workflow_bindings`

保存第 9 节的绑定字段。运行记录必须复制当时的 ref 和 hash，不能只外键到一条可变 active binding。

### `remote_decision_runs`

```text
local_run_id / anomalo_run_id
intent_type / request_intent_id / idempotency_key
scope_type / scope_id / scope_version / dataset_id
lens_type / lens_id / lens_version
workflow_ref / definition_hash / compiled_hash
input_schema_version / input_sha256 / input_json / metadata_json
trigger_mode / trigger_source
status / remote_status / latest_event_sequence
validation_status(pending|accepted|rejected)
error_code / safe_error_message
created_at / submitted_at / started_at / completed_at
```

### `remote_decision_events`

```text
local_run_id / sequence / event_type / event_timestamp
node_id / attempt / child_run_id
safe_data_json / created_at
```

### `remote_decision_artifacts`

```text
local_run_id / output_schema_version
completeness / artifact_json / artifact_sha256
evidence_refs_json / usage_json / trace_ref
accepted_at
```

V1 直接保存 Anomalo 实际收到的 `input_json` 和 `metadata_json`；不引入尚未由已发布 Workflow 支持的 URI manifest 模式。事件单独保存，并按 `(local_run_id, sequence)` 幂等写入；前端需要的 Decision Trace 是这些已清洗事件的 read model。

## 13. 错误与恢复

| 场景 | 页面结果 | 是否可重试 |
| --- | --- | --- |
| Workflow Binding 缺失或 hash 未验证 | 按钮禁用，显示配置问题 | 修复部署后重新 prepare |
| 输入在确认后改变 | `preflight_stale` | 重新确认 |
| 相同 key、相同输入 | 返回已有 run | 不创建新 run |
| 相同 key、不同输入 | `idempotency_conflict` | 生成新 intent，不自动改 key |
| Anomalo 不可达 | 本地 run `failed` 或保留 queued 等待受控重试 | 仅 retryable 网络错误 |
| Workflow 输入 Schema 不匹配 | `failed`，标记集成缺陷 | 修代码/Definition 后新版本重跑 |
| 模型节点失败 | 展示远端稳定 error code | 依远端 retryability |
| 输出 Schema 不匹配 | `rejected_result`，不展示为 AI 结论 | 修 Workflow 后重跑 |
| 结果 symbol 越界或 hash 不符 | `rejected_result` + 安全告警 | 不自动接受 |
| 浏览器断线或服务重启 | Worker 通过本地记录和远端 run 查询恢复 | 是 |

远端 HTTP 错误必须按稳定 `error_code` 分支，至少覆盖：

- `invalid_workflow_run_request`：集成输入或 Schema 缺陷，不自动重试；
- `workflow_ref_forbidden`：service client allowlist/权限配置缺陷；
- `workflow_not_found`：Binding 指向未发布、已退役或错误版本；
- `idempotency_key_reused`：同 key 下 input/metadata 漂移；
- `workflow_unavailable`：受控退避并告警；
- `workflow_runtime_error`：保存 request/run 信息并停止无界重试。

日志和页面只显示安全错误；service token、管理 token、原始 Authorization header、带凭据 URL 不得落库或返回前端。

## 14. 开发顺序

### D0：契约和 Workflow 发布

- 冻结四个 Remote Decision Input 子 Schema 和三个结果 Schema；
- 通过受控发布流程获取并保存目标 Anomalo 实例最新 Capability Manifest 及 `manifest_hash`；
- 只使用 Manifest 中真实存在的 node type、Preset Model 和 Plugin Operation，在 Urus 仓库保存四份 Anomalo Workflow Definition；
- 用管理面 validate、import draft，经管理员确认后 publish；
- 保存 Workflow Binding 和固定 hash fixture；
- 为 Urus service client 配置 `workflow:read/workflow:run` 与四个精确 Workflow Ref allowlist；
- 为每个 Workflow 准备一个成功、证据不足、模型失败和输出越界 fixture。

### D1：Remote Workflow Module

- 新增独立 `AnomaloWorkflowAdapter`；
- 创建 run、Worker、事件摄取、状态恢复、stop 和 artifact 验收；
- 实现 Fake/production Adapter contract tests；
- 不改动现有聊天 `AnomaloAdapter` 的 Interface。

### D2：个股入口

- 先贯通个股 `instrument` scope；
- 两个视觉入口共享一个 action；
- 完成确认框、运行状态、结果卡、历史结果和重跑；
- 以此验证完整 vertical slice。

### D3：组与横截面入口

- 组页增加独立 AI 按钮；
- 增加按 Observation Run/snapshot 读取组页面的 Interface，禁止 AI 提交时解析 latest snapshot；
- 启用指标/策略页现有占位按钮；
- 为两类横截面 projection 增加确定性 `attention_features`；
- 复用公共确认、状态和运行详情 Module；
- 验证 lens 不扩大 Decision Scope。

### D4：恢复、真实联调与交接

- 完成服务重启、流断开、幂等重试、stop 和 rejected artifact 恢复测试；
- 在真实 Anomalo 环境依次验证四个已发布 Workflow；
- 确认 Observation Run 不出现 AI 调用；
- Decision Case、相似案例、Strategy Evaluation、Arbitration Evaluation 和 Skill 演化全部留到 Phase E。

## 15. 验收标准

1. 四个按钮各自只表达一种清晰意图，确定性按钮与 AI 按钮不混用；
2. 页面加载、刷新、切换筛选不会创建 Remote Decision Run；
3. 第一次点击只显示确认，确认后才创建 run；
4. 前端在 202 后立即恢复交互，不等待 Anomalo terminal；
5. 相同网络重试幂等，明确重跑创建新记录且不覆盖旧 artifact；
6. 四条流程都只读取冻结输入，不能扩大 symbol 或查询 latest；
7. 个股输出是 Strategy Arbitration Decision，指标输出不是交易建议；
8. `target_ref`、`target_hash`、`input_sha256` 和 Evidence Reference 全部通过验收；
9. 远端失败、停止或超时不影响确定性页面；
10. Observation Run 只完成采集、冻结和确定性报告，不创建手动或自动 Remote Decision Run；
11. 运行详情能显示节点、usage、错误和证据血缘，默认不展示 Provider-returned Reasoning；
12. 日常 Urus 运行时不持有 Anomalo 管理 token；
13. Fake Adapter 与 Anomalo Adapter 通过同一 Interface 的契约测试；
14. 所有被选和未选策略都能进入后续独立评价；
15. Definition 符合目标 Manifest，四个 Ref 已发布并进入 Urus service client allowlist；
16. 断线后通过相同 run ID 和事件 sequence 恢复，不创建第二个 Run。

## 16. 当前明确不做

- 自由聊天式“问 AI”；
- AI 自动选择新 symbol 或修改 Observation Group；
- AI 重算指标、策略 score、group ranking 或 Deterministic Synthesis；
- AI 输出订单、仓位或自动交易；
- 页面加载自动调用个股、组或横截面 Workflow；
- Observation Run AI 收市复核或自动 AI 计划任务；
- 每次点击动态 import/publish Workflow；
- 把管理 token 暴露给日常 Urus 运行时；
- 把 Provider-returned Reasoning 当成正式 Decision Rationale；
- Phase D 内动态修改生产 Skill；
- 样本不足时宣称 AI 已优于确定性基线。

## 17. 开发启动条件与外部发布物

### 17.1 当前就绪度

| 能力 | 当前状态 | Phase D 处理 |
| --- | --- | --- |
| Phase C 个股、组、指标/策略横截面 read model | 已完成 | 作为冻结输入权威来源 |
| Anomalo Workflow Runtime 管理面与运行面 | 已完成 | 按正式合同接入 |
| Urus Workflow Adapter、运行账本、事件恢复 | 已实现本地骨架 | D4 真实环境联调 |
| 四个 Urus 业务 Workflow Definition | 已绑定 `urus-arbitration@4` / `urus-attention@5`，四个 `@2` Ref 已发布 | D0 继续做真实运行联调 |
| 决策专用 Preset Model / workflow-callable serializer | 已配置两个 Preset Model Ref：仲裁 `@3`、关注 `@4` | D0 保存 Manifest hash；若输入端口不接受 message，再按 Manifest 增加 serializer |
| 四个 published Workflow Ref / compiled hash | 已发布并写入 `backend/workflow-bindings.json` | D4 用相同 Ref/hash 做真实运行联调 |
| Urus workflow service token 与 Ref allowlist | 已配置 `workflow:read` / `workflow:run`，仅允许四个 Ref；已通过 401/400/403 auth smoke | D4 继续做真实运行联调；secret 仅留在部署环境与本地 `.env` |

“Anomalo Workflow Runtime 已完成”不等于“Urus 四条 AI 决策已经可运行”。开发 agent 不得把现有
`scheduled-event-investigator@1` 或 `daily-event-review@1` 当作决策 Workflow 复用；它们的 Task、输入和输出都不同。

### 17.2 D0 必须交付给 Urus 的发布包

```text
anomaloharis-workflow-capabilities.json
urus-instrument-arbitration-v1.json
urus-group-arbitration-v1.json
urus-indicator-review-v1.json
urus-strategy-review-v1.json
workflow-bindings.json（不含 token）
```

`workflow-bindings.json` 每项至少包含：

```json
{
  "intent_type": "instrument_arbitration",
  "workflow_ref": "urus-instrument-arbitration@2",
  "definition_hash": "sha256:...",
  "compiled_hash": "sha256:...",
  "capability_manifest_hash": "sha256:...",
  "input_schema_version": "urus.remote_decision_input.v1",
  "output_schema_version": "urus.remote_decision_artifact.v1",
  "published_at": "..."
}
```

未取得完整发布包时，Fake Adapter 测试可以运行，但 `decision_workflow_bindings.status` 不能设为 `active`，所有生产 AI 按钮返回 blocker `workflow_binding_unavailable`。

### 17.3 当前部署鉴权配置

目标 Anomalo 实例的部署环境已写入 canonical `ANOMALOHARIS_SERVICE_TOKENS` 与
`ANOMALOHARIS_WORKFLOW_ALLOWED_REFS`。现有 `default` client 保留旧的
`compute:models` / `compute:invoke` / `compute:read` 能力；新增 `urus-decision` client
只拥有 `workflow:read`、`workflow:run`，client 与 Host 两层 allowlist 都只包含本节四个
`@2` Ref。Urus 的专用运行 token 只写入本地 `.env` 和远端部署环境，不进入仓库、Binding
或日志。服务重启后已通过合法 token 的请求校验（400）、错误 token（401）和越权 Ref（403）
探测；这些探测不会创建 Run。

## 18. Source Locator、Preflight 与提交合同

### 18.1 Source Locator

前端只提交不可变对象的 locator，不提交 evidence JSON：

```json
{"intent_type":"instrument_arbitration","source":{"dataset_id":"uuid","symbol":"NVDA"}}
{"intent_type":"group_arbitration","source":{"observation_run_id":"uuid","snapshot_id":"uuid"}}
{"intent_type":"indicator_attention","source":{"observation_run_id":"uuid","lens_id":"rsi14"}}
{"intent_type":"strategy_attention","source":{"observation_run_id":"uuid","lens_id":"trend_momentum_v1"}}
```

后端必须从 Repository/Decision Harness Module 读取权威冻结证据并校验：

- instrument：dataset 的 scope 必须包含且只决策 locator symbol；
- group：snapshot 必须存在于 locator 指定 Observation Run 的 `group_snapshots`，并解析出精确 group version、dataset 和 content hash；
- indicator/strategy：Observation Run 必须为 `succeeded|mixed`，lens 必须存在且 projection hash 可重建；
- locator 不接受 `latest`、日期模糊查询、前端 rows、任意 prompt 或 symbol 列表覆盖。

### 18.2 Preflight Response

```json
{
  "enabled": true,
  "blockers": [],
  "warnings": [],
  "intent_type": "indicator_attention",
  "source": {},
  "source_summary": {
    "trading_date": "2026-08-25",
    "symbol_count": 42,
    "group_count": 6,
    "quality_status": "partial"
  },
  "binding": {
    "workflow_ref": "urus-indicator-review@2",
    "compiled_hash": "sha256:..."
  },
  "input_sha256": "<64-lowercase-hex>",
  "preflight_fingerprint": "<64-lowercase-hex>"
}
```

稳定 blocker code：

```text
workflow_disabled
workflow_binding_unavailable
source_not_found
source_not_frozen
source_scope_mismatch
source_version_conflict
local_demo_forbidden
input_too_large
no_valid_evidence
```

### 18.3 Submit Request 与幂等键

```json
{
  "intent_type": "indicator_attention",
  "source": {"observation_run_id":"uuid","lens_id":"rsi14"},
  "preflight_fingerprint": "...",
  "request_intent_id": "client-generated-uuid"
}
```

本地幂等键固定计算为：

```text
sha256("urus-remote-decision-v1\0" + request_intent_id + "\0" + workflow_ref + "\0" + input_sha256)
```

发送给 Anomalo 的 `metadata` 在首次提交时冻结为：

```json
{
  "source": "urus",
  "local_run_id": "uuid",
  "intent_type": "indicator_attention",
  "trigger_source": "indicator_cross_section"
}
```

重试不得增加当前时间或改变 metadata。相同 `request_intent_id` 与相同 fingerprint 返回已有本地 run；相同 intent ID 对应不同 fingerprint 返回 `request_intent_conflict`。

## 19. 本地数据契约与限制

### 19.1 Intent 和结果 Schema

Pydantic 与 TypeScript 使用同一组稳定枚举：

```text
intent_type:
  instrument_arbitration
  group_arbitration
  indicator_attention
  strategy_attention

artifact schema:
  urus.remote_decision_artifact.v1

`decision` 和 `notable_cards` 按 `intent_type` 承载个股、组或横截面语义；需要拆分为独立
结果 Schema 时发布新的 Workflow/Schema 版本。
```

`backend/app/schemas/remote_decision.py` 中 strict Pydantic model 是输入和 artifact 合同的唯一源；使用 `model_json_schema()` 导出并提交 JSON Schema fixture，四个 Anomalo Workflow Definition 必须嵌入对应的同内容 Schema。Urus contract test 比较规范化 Schema hash，防止远端 Workflow 与本地验收模型漂移。

共同 artifact envelope 使用第 8 节结构，并满足：

- `completeness = complete|partial|insufficient_evidence`；
- `confidence` 为 `0..1` 数字，不显示为胜率；
- `summary/why_notable/suggested_drilldown` 单字段最多 1,000 字符；
- `notable_cards` 最多 20 项，`warnings` 最多 50 项；
- `rank` 从 1 连续递增，`card_id` 在 artifact 内唯一；
- indicator/strategy artifact 的每个 card ID 必须存在于输入 projection；
- instrument/group 的 strategy ID 必须存在于输入 Strategy Decision；
- 输出额外字段一律拒绝，不能宽松丢弃后接受。

### 19.2 Attention Features

`CrossSectionService` 在 content hash 计算前为每个 row 增加 `attention_features`：

```text
indicator:
  global_percentile
  within_group_percentile
  change_percentile
  threshold_distance
  is_transition
  quality_flag

strategy:
  score_percentile
  score_change_percentile
  confirmation_distance_rank
  is_stage_transition
  is_new_invalidation
  quality_flag
```

百分位采用有效数值的稳定 mid-rank；少于 4 个有效样本时为 `null`。缺失/错误值不进入分母，保留 `quality_flag`。AI 输入仍包含全部 row，features 只提供确定性比较坐标，不提前删除卡片或形成交易判断。

### 19.3 V1 资源 Gate

默认配置：

```text
remote_decision_max_input_bytes = 500000
remote_decision_max_cross_section_rows = 1000
remote_decision_max_notable_cards = 20
remote_decision_event_poll_limit = 500
# 0.25s poll interval × 4800 ≈ 20 minutes, matching the Workflow read window
remote_decision_max_polls = 4800
anomalo_workflow_enabled = false
anomalo_workflow_token = secret-or-null
anomalo_workflow_connect_timeout_seconds = 10
anomalo_workflow_read_timeout_seconds = 1200
```

canonical input 超限时 preflight 返回 `input_too_large`；V1 不静默截断、不拆成多个互不关联的 Run。真正需要更大范围时，先设计新的确定性 projection 或静态并行 Workflow 版本。

Workflow token 只从环境/Secret Store 注入，不进入 Runtime Settings 数据库或前端响应。`anomalo_base_url` 可以与现有 chat Adapter 共享；enabled、token、timeout 和错误状态保持独立。

## 20. 持久化、Supervisor 与状态恢复

### 20.1 迁移和表

下一迁移使用 `0024_remote_decision_workflows.py`，新增：

```text
decision_workflow_bindings
remote_decision_runs
remote_decision_events
remote_decision_artifacts
```

约束：

- Binding：`intent_type + workflow_ref` 唯一；同 intent 最多一个 active；
- Run：`request_intent_id` 唯一，保存实际发送的 input 和 metadata；
- Event：`local_run_id + sequence` 唯一；
- Artifact：一个 local run 最多一个 accepted artifact；
- Run 保存 nullable `source_dataset_id/source_snapshot_id/source_observation_run_id` 外键和完整 source locator；删除被引用的 Dataset、Group Snapshot 或 Observation Run 时 RESTRICT；
- artifact 和已提交 run 不可更新业务内容，只能追加事件和推进合法状态。

### 20.2 状态转换

```text
queued → submitting → running
queued|submitting → failed
queued → stopped
queued|submitting|running → stopping
running → succeeded|failed|stopped
stopping → succeeded|stopped|failed
succeeded → accepted|rejected_result
```

`accepted/rejected_result` 是 Urus 验收状态，不是 Anomalo Run 状态。状态转换使用 compare-and-set，重复事件必须幂等。

### 20.3 生命周期 Supervisor

新增 lifespan 管理的 `RemoteDecisionSupervisor`：

- 使用有界 `asyncio.Queue` 与 `httpx.AsyncClient`，不阻塞 FastAPI 请求；
- `POST /remote-decisions` 先提交数据库事务，再 enqueue；
- 新 Run 使用 `/runs/stream`，先持久化流中出现的 `run_id`，因此提交期间也能响应 stop；
- 启动时扫描 `queued/submitting/running/stopping/succeeded`；其中 `succeeded` 表示远端已成功但 Urus 可能尚未完成 Artifact 验收，必须继续恢复；
- 没有 `anomalo_run_id` 的记录用原 idempotency key 重提；
- 已有 remote ID 的记录先 GET Run，再从 `latest_event_sequence` 继续读事件；
- 默认受控轮询窗口为 4800 次 × 0.25 秒，覆盖 1200 秒 Workflow read timeout；
- 应用关闭只停止本地消费，不自动 stop 远端 Run；
- Fake Adapter 使用同一 Supervisor Interface，测试不得依赖真实网络。

## 21. 代码改动地图

后端：

```text
backend/alembic/versions/0024_remote_decision_workflows.py
backend/app/models/remote_decision.py
backend/app/repositories/remote_decision.py
backend/app/schemas/remote_decision.py
backend/app/decision_harness/remote_workflow.py
backend/app/decision_harness/workflow_definitions/*.json
backend/app/decision_harness/remote_schemas/*.json
backend/app/integrations/anomalo_workflow.py
backend/app/services/remote_decision_supervisor.py
backend/app/api/remote_decisions.py
backend/app/api/router.py
backend/app/core/config.py
backend/app/main.py
backend/app/decision_harness/cross_section.py
backend/app/api/observation.py
backend/scripts/register_decision_workflows.py
backend/scripts/load_workflow_bindings.py
```

前端：

```text
frontend/src/types/remoteDecision.ts
frontend/src/api/client.ts
frontend/src/components/decision/RemoteDecisionPanel.vue
frontend/src/components/decision/RemoteDecisionConfirmDialog.vue
frontend/src/composables/useRemoteDecision.ts
frontend/src/views/RemoteDecisionRunView.vue
frontend/src/views/InstrumentDecisionView.vue
frontend/src/views/GroupObservationView.vue
frontend/src/views/CrossSectionView.vue
frontend/src/router/index.ts
```

Module 规则：

- evidence 编译、preflight、hash、验收和状态推进集中在 `remote_workflow.py`；
- HTTP、Bearer auth、NDJSON、Run/events/stop 集中在 `anomalo_workflow.py`；
- 页面只使用 composable 和公共面板，不各自实现轮询、错误映射或结果校验；
- 现有 `backend/app/integrations/anomalo.py` 保持不变；
- Observation Run View 不增加 AI Panel。

## 22. 测试矩阵与交接完成条件

### 22.0 当前实现状态（2026-08-27）

本分支已落地 Phase D 的 Urus 侧运行骨架：四类 source locator/preflight/指纹、`0024` 持久化、严格 Artifact 验收、Anomalo Workflow HTTP/Fake Adapter、受限队列 Supervisor/重启恢复、横截面 attention features、组快照精确查询，以及阶段 C 页面和统一 Remote Decision Run 面板。Observation Run 仍是 deterministic-only。

Anomalo 生产联调仍受 D4 fixture gate 约束：四个 Urus Workflow Ref（`urus-instrument-arbitration@2`、`urus-group-arbitration@2`、`urus-indicator-review@2`、`urus-strategy-review@2`）已通过远端管理面发布，并绑定到带 Artifact 输出契约的 `urus-arbitration@4` / `urus-attention@5`；compiled hash 与 Capability Manifest hash 已保存到 `backend/workflow-bindings.json` 并加载进本地数据库。远端已配置独立 `urus-decision` service client，仅授予 `workflow:read` / `workflow:run`，并同时受四个 Ref 的 client/Host allowlist 限制；不合法 token 和未允许 Ref 的 smoke 已分别得到 401 与 403，合法 token 在不创建 Run 的无效请求探测中得到 400。当前剩余门槛是逐条真实运行 fixture；在这些确认前，生产部署仍应由显式 `anomalo_workflow_enabled` 开关控制，本地 `.env` 已开启以支持联调。`register_decision_workflows.py` 只负责管理期 validate/import/publish 和生成不含 token 的 Binding 审计文件；日常 Urus 运行时仍不持有管理 token。

### 22.1 后端自动测试

```text
test_remote_decision_contracts.py
  四类 locator / input / preflight compiler / artifact strict validation / evidence identity / event redaction

test_anomalo_workflow_adapter.py
  auth / non-stream / streaming NDJSON / split chunks / event sequence / HTTP errors / stop

test_remote_decision_api.py
  preview-submit / 202 / idempotency / stale / history / rerun / stop

test_remote_decision_recovery.py
  crash before remote ID / crash after remote ID / streaming stop / duplicate event / terminal recovery / succeeded Artifact recovery

existing cross-section tests
  attention feature mid-rank / null sample / hash stability / version conflict

existing observation tests
  exact group snapshot lookup / no latest drift / Observation Run never calls AI
```

### 22.2 前端自动测试

- 两个个股按钮共享同一 action 和 run；
- LOCAL DEMO、无 Binding、无冻结证据时按钮禁用并显示 blocker；
- 组页 URL 绑定 exact run/snapshot，刷新不漂移；
- indicator/strategy 一次提交使用当前 run + 当前 lens，切 Tab 不自动运行；
- notable result 能定位 `row.id` 卡片；
- running/failed/stopped/rejected_result/accepted 展示；
- Observation Run 页面没有 AI 按钮或隐式请求。

### 22.3 真实联调

每个 published Ref 至少验证：成功、证据不足、模型失败、断线恢复、stop、重复幂等、越界 artifact 拒绝。联调记录保存 Workflow Ref、definition/compiled/manifest hash、Urus local run ID、Anomalo run ID 和最终 artifact hash，不保存 token。

### 22.4 可交给开发 agent 的完成定义

开发完成必须同时满足：

1. D0 发布包齐全，或明确以 Fake-only 状态交付且生产按钮保持禁用；
2. 四个入口复用同一 Remote Workflow Module 和 Panel；
3. exact source locator、preflight fingerprint、幂等与恢复测试通过；
4. 组页不再依赖 latest snapshot 发起 AI；
5. 横截面严格是一 run、一 lens、整页卡片一次调用；
6. Observation Run 没有 AI 调用；
7. 全部后端、前端和 migration 测试通过；
8. `git diff --check`、前端 build 和真实契约 smoke test 通过；
9. 文档中的未决 D0 外部依赖没有被 mock 成“生产已完成”。

### 22.5 建议工作包顺序

```text
A. contracts + migration + repositories
  → B. exact evidence compiler + attention features + preflight
  → C. Fake Adapter + API + shared frontend Panel（完成离线 vertical slice）
  → D. Anomalo Adapter + Supervisor + recovery
  → E. D0 Manifest/Definition/Preset Model 发布
  → F. 四入口真实联调与生产启用
```

A–D 不得等待 E 才开始，但 F 必须以 E 的真实发布包为硬前置。每个工作包完成后运行其直接测试；最终再运行完整 backend/frontend suite。

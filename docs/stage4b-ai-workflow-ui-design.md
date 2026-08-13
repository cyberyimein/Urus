# Stage 4B AI 工作流与报告前端设计

> **前端设计修订说明（2026-08-04）**：研究报告页面才是 Urus 的主要用户界面；现有
> Dashboard、Runs 和 Run Detail 属于采集与开发控制台。报告路由、三 Tab 信息架构、图表化技术
> 报告、真实 DAG 复盘，以及 provider reasoning-like 内容的按需查看，以
> [Stage 4B 研究报告前端重构方案](stage4b-report-frontend-redesign.md) 为准。

## 1. 结论与开发前提

Stage 4B 可以进入下一步开发。OpenRouter 已经分别通过股票与 QQQ 期权真实冒烟测试，两个
Skill 的严格 JSON 输出、本地业务校验和只读工具循环都可用。

本轮已完成第一版后端工作流与正式报告前端；仍需通过真实冻结数据联合验证和成本验证后再扩大候选数量。已落地的边界如下：

1. Step 4 现在由 `DecisionCoordinator` 执行大盘 → 并发题材 → 股票综合；期权链由程序整理为股票入场过滤上下文，不再独立调用期权 Agent。
2. `FrontendReadModel.decision` 支持真实结构化结果，同时保留禁用/失败时的占位分支。
3. `Decision Session`、`Workflow Run`、`Agent Invocation`、Trace Node 和 Model Turn 已关联并写入 SQLite。
4. 只读报告接口提供技术报告、AI 决策报告、Trace 图、节点详情和显式原始返回端点。
5. 模型调用前会保存 `running` 记录，模型 HTTP 调用不持有数据库事务；上下文、工具调用和原始返回均有预算。
6. 前端已提供技术整理、AI 决策、AI 决策复盘三个 Tab；原始模型返回默认收起，用户手动展开后仅作为未校验的复盘材料。
7. Evidence Reference、工具参数、单次/累计工具结果字节数、累计工具调用数和上下文均在本地执行前校验；期权结构上下文由程序生成，缺失或阻断性质量问题会明确标记而不会伪造结论。
8. 报告主端点只返回元数据和资源地址，三个 Tab 按需加载；报告版本、Tab、证据跳转均可写入 URL。
9. QQQ 股票真实测试累计约 150,641 prompt tokens。全量运行前仍必须限制工具结果、事件摘要和总上下文预算，否则成本、延迟和上下文溢出风险会迅速放大。
10. 在线采集保留盘前、尾盘、收盘后三阶段，但 Step 4 AI 只在盘前和收盘后运行。尾盘只冻结
    Observation 和 Technical Report，不生成 AI 预测。每次运行只把当前阶段视为当前事实，通过
    不可变的 `prior_reports` 和 `parent_session_id` 继承历史判断；缺少早期阶段时显式降级，绝不
    用当前 payload 冒充缺少的观察。详细迁移要求见
    [CTA 分支 AI 决策与 IV/HV 需求](cta-ai-decision-requirements.md)。

本设计覆盖两个连续阶段：先完成 AI 调用工作流，再实现三个 Tab 的正式报告前端。

## 2. 产品结构

每个阶段的冻结 Decision Dataset 先生成一份 Technical Report，并产生一个不可变的 Research
Report/Decision Session 版本。报告页面固定为三个一级 Tab：

1. **技术整理报告**：程序根据冻结数据生成。
2. **AI 决策报告**：展示已通过 Schema 和业务校验的 AI 输出，由程序组织版面。
3. **AI 决策复盘**：以节点图展示可审计的 Decision Trace；模型原始返回默认折叠，但用户可以
   主动展开查看。

三者必须使用同一个 `dataset_id`、`cutoff_time` 和阶段来源身份。任何 Tab 都不能偷偷读取“最新数据”
替换原始报告。

## 3. 深化后的 Module

### 3.1 Decision Session Module

这是 Step 4 对外的主要 Module。它的 Interface 保持为一个高层调用：

```text
execute(dataset_id, cutoff_time, frozen_evidence, policy) -> DecisionSessionResult
```

Implementation 隐藏股票调用、候选筛选、期权调用、失败隔离、报告组装和 Trace 写入。这个
Module 的 Depth 让 `RunService` 不需要理解 Agent 工具循环和多阶段协调，给调用者更高
Leverage，也把 Stage 4B 变化集中在一个位置以获得 Locality。

现有 `DecisionAdapter` 是模型调用 Seam；`OpenRouterProvider` 和 `FakeLLMProvider` 是两个真实
Adapter。Decision Session Module 不直接拼 OpenRouter HTTP 请求。

### 3.2 Technical Report Module

Interface：

```text
build(frozen_evidence) -> TechnicalReportV1
```

Implementation 只执行确定性投影、分组、字段归一化和质量说明。删除这个 Module 会迫使三个
前端 Tab、导出脚本和测试各自理解原始 snapshot，因此它应保持为一个 Deep Module。

### 3.3 Decision Trace Module

Interface：

```text
start_node(...)
finish_node(...)
fail_node(...)
build_graph(session_id) -> DecisionTraceGraphV1
```

SQLite Trace Adapter 用于正式运行，内存 Trace Adapter 用于测试。这个 Seam 允许 Runtime 和
Coordinator 只发出领域事件，不理解表结构，既提高测试 Leverage，也保持持久化知识的 Locality。

### 3.4 Research Report Read Module

Interface：

```text
list_reports(dataset_id?)
get_report(report_id)
get_technical_report(report_id)
get_ai_decision_report(report_id)
get_decision_trace(report_id)
```

Implementation 负责将 Snapshot、Decision Session、Agent Invocation 和 Trace 组装成前端契约。
前端不直接读取数据库模型或自行拼接工具调用。

## 4. 完整 AI 决策工作流

```text
1A / 1B / 2 / 3A / 3B 完成
             │
             ▼
同交易日 pre_market + 当前 pre_close 配对
             │
             ▼
冻结证据与 TechnicalReportV1
             │
             ▼
创建 Decision Session（running）
             │
             ▼
大盘 Agent Invocation
  SPY / QQQ / SMH / IGV
             │
             ▼
并发题材 Agent Invocation（有界并发）
 半导体 / 光概念 / SaaS / 大科技 / 航天与新兴
             │
             ▼
股票综合 Agent Invocation
  已校验大盘与题材输出
  + 程序生成的逐标的期权入场上下文
  不开放数据工具，不生成期权策略
             ▼
确定性 AI Decision Report 组装
             │
             ▼
Decision Session（succeeded / partial / failed）
             │
             ▼
Step 5 写入报告索引和前端 read model
```

### 4.1 大盘、题材与股票综合阶段

- 大盘调用先分析 SPY、QQQ、SMH、IGV、宏观事件和数据质量，形成可复用 Market Context。
- 每个非空题材创建独立 `equity_ranking` Agent Invocation，只能读取该题材 symbol 与允许的基准 ETF。
- 题材模型请求使用 `ThreadPoolExecutor` 有界并发；默认
  `URUS_AGENT_THEME_MAX_CONCURRENCY=6`，因此当前全部非空题材可同时执行。
- Worker 只执行模型和只读工具；SQLAlchemy Session、Repository 与主 Trace 始终由协调线程串行操作。
- 股票综合调用只接收已通过校验的大盘与题材结构化输出，不开放数据工具，生成跨题材连续排名。
- Evidence Reference 必须解析到同一冻结证据或该次调用已观察的工具路径；无法解析时调用失败。
- 同一 symbol 只归属一个确定性题材 scope；SMH 可在大盘调用中作为 ETF，同时在半导体题材中作为基准证据。
- 分阶段提示词使用 `urus.agent_task_prompts.v2`：大盘必须区分广度与少数 ETF 领涨；题材必须覆盖
  scope 中每个标的并核对相对强度、动量、布林、波动率、量价与事件；综合必须覆盖全部候选并保留
  冲突；期权不能混用不同到期日的 DEX/GEX、墙或 Gamma Flip。
- Theme 与 Synthesis 输出若遗漏 scope 中任一 symbol，会在本地业务校验阶段失败，不能进入 Gate。
- 每阶段最低证据由程序在第一次模型请求前通过同一只读 Tool Registry 确定性预取，并写入 Tool
  Trace：Market 读取两阶段市场、质量和宏观事件；Theme 逐标的读取 pre-close 技术快照和配对变化；
  Options 读取两阶段概览。模型仍能在许可 scope 内追加工具调用。
- Evidence path 必须在冻结包中真实解析，或与工具返回的 canonical `evidence.path` 完全相等；不能在
  工具父路径后自行拼接不存在的字段。

### 4.2 股票期权结构上下文

协调器为全部请求标的确定性选择最近正 DTE，提取 DEX/GEX、Gamma Flip、Call/Put 墙、Max Pain、
Expected Move，并分类为稳定正 Gamma、负 Gamma 放大、Gamma Flip 附近、Flip 下方脆弱或未知。
该上下文在 Synthesis 前一次性生成，不调用模型；缺失期权数据只标记 unknown，不能自动降低股票评级。

### 4.3 股票综合阶段的期权约束

- Synthesis 只能把期权结构用于股票的入场时机、波动风险和目标区确认。
- Max Pain 不能独立充当方向预测；正 Gamma 也不能覆盖弱趋势或负面事件证据。
- 负 Gamma、接近 Flip 或临近到期可收紧 `if_cash.entry_condition`，在边际机会中支持 wait/avoid。
- 禁止输出价差、蝶式、铁鹰、日历、合约腿或权利金建议。
- 报告保留 `candidate_gate=[]` 与 `option_decisions=[]` 兼容旧客户端，并新增 `equity_option_context`。

### 4.4 报告组装

由确定性代码生成 `urus.ai_decision_report.v2`：

```json
{
  "schema_version": "urus.ai_decision_report.v2",
  "report_id": "uuid",
  "session_id": "uuid",
  "dataset_id": "uuid",
  "source_run_ids": ["pre-market-run", "pre-close-run"],
  "cutoff_time": "ISO-8601",
  "status": "succeeded",
  "equity_decision_run_id": "uuid",
  "market_analysis": {},
  "theme_analyses": [],
  "market_regime": {},
  "rankings": [],
  "candidate_gate": [],
  "option_decisions": [],
  "portfolio_warnings": [],
  "quality": {},
  "execution_ready": false,
  "generated_at": "ISO-8601"
}
```

股票综合是显式、可审计的 Agent Invocation，只能整合已校验的大盘与题材输出。其后的报告组装不得
再次调用模型；程序可以排序、分组、增加链接和汇总状态，但不能创造新的投资观点。

## 5. 上下文与成本控制

全量工作流启用前必须完成：

1. `get_events` 默认最多返回 10 条、上限 20 条，只返回事件摘要；详细结果由
   `get_event_result` 单独读取。
2. 市场工具按 symbol 去重，避免 primary quote 与 cross-asset quote 重复。
3. 工具结果同时限制单次字节数、累计字节数和累计工具调用数。
4. 增加 `URUS_AGENT_MAX_CONTEXT_BYTES`、`URUS_AGENT_MAX_TOTAL_TOOL_RESULT_BYTES` 和
   `URUS_AGENT_MAX_TOTAL_TOOL_CALLS`。
5. 每轮模型调用记录独立 token usage；报告显示总 prompt/completion tokens。
   可通过 `URUS_AGENT_INPUT_COST_PER_MILLION` 与 `URUS_AGENT_OUTPUT_COST_PER_MILLION` 记录估算成本；默认值为 0，不凭空估价。
6. 达到预算时进入结构化 `insufficient_data` 或 `failed`，不能静默截断后继续假装完整。
7. 全量验证先使用 `ranked + max_option_symbols=1`，稳定后再逐步增加。

## 6. 持久化设计

### 6.1 新表 `ai_decision_sessions`

核心字段：

- `id`
- `workflow_run_id`
- `dataset_id`
- `dataset_key`
- `cutoff_time`
- `status`: `pending | running | succeeded | partial | failed | timed_out`
- `policy_json`
- `technical_report_schema_version`
- `technical_report_json`
- `decision_report_schema_version`
- `decision_report_json`
- `equity_decision_run_id`
- `error_code`
- `error_message`
- `started_at`
- `completed_at`
- `created_at`

同一 Decision Dataset 可以重跑并生成多个 Decision Session；读取默认报告时选择最近一次成功或
partial 的记录，不能覆盖旧记录。

### 6.2 扩展 `ai_decision_runs`

增加：

- `decision_session_id`
- `workflow_run_id`
- `parent_decision_run_id`
- `stage`: 新工作流使用 `market | theme | synthesis`；保留 `options | equity` 读取旧审计记录
- `sequence`

`source_run_ids` 必须实际写入配对的盘前与收盘前 Workflow Run ID。题材 Agent Invocation 指向大盘
调用。`equity_decision_run_id` 保存股票综合调用 ID；其他调用
通过 `decision_session_id + stage` 查询，不增加冗余 session 列。

### 6.3 新表 `ai_trace_nodes`

核心字段：

- `id`
- `decision_session_id`
- `decision_run_id`
- `parent_node_id`
- `depends_on_node_ids_json`
- `sequence`
- `lane`
- `node_type`
- `label`
- `status`
- `input_summary_json`
- `output_summary_json`
- `evidence_refs_json`
- `metrics_json`
- `error_code`
- `error_message`
- `started_at`
- `completed_at`

`node_type` 固定为：

- `evidence`
- `skill`
- `model`
- `tool`
- `validation`
- `gate`
- `assembly`

图的 edge 由 `parent_node_id` 与 `depends_on_node_ids_json` 确定性生成，第一版不需要单独的
edge 表。现有 `ai_tool_calls` 保留完整工具审计，Trace Node 只保存适合图形展示的摘要和引用。

### 6.4 新表 `ai_model_turns`

每次 provider 返回都单独保存，不能只保留最终通过校验的 JSON：

- `id`
- `decision_run_id`
- `trace_node_id`
- `sequence`
- `response_message_json`
- `raw_provider_response_json`
- `raw_response_bytes`
- `raw_response_truncated`
- `prompt_tokens`
- `completion_tokens`
- `created_at`

`raw_provider_response_json` 保存 provider 实际返回的内容，包括正常文本、工具调用、格式错误文本，
以及 provider 明确返回的 `reasoning`、`reasoning_details` 或类似字段。它不包含请求 header、API Key
或本地环境变量。超过配置上限时允许截断，但必须保存原始字节数和截断标志。

原始返回是审计材料，不是已经验证的决策事实。只有通过最终 Schema、业务规则和 Evidence
Reference 校验的 `parsed_output` 才能进入 AI Decision Report。

### 6.5 事务规则

1. 创建 Decision Session 并提交 `running`。
2. 创建 Agent Invocation 并提交 `running`。
3. 关闭事务后调用模型和工具。
4. 用短事务保存每批 Trace Node 或最终调用结果。
5. 最后用独立事务组装报告并结束 Decision Session。

数据库锁、进程退出或 provider timeout 都必须留下可解释的 `running/failed/timed_out` 记录。

## 7. 前端读取 Interface

建议新增只读端点：

```text
GET /api/research/reports
GET /api/research-reports/{report_id}
GET /api/research-reports/{report_id}/technical
GET /api/research-reports/{report_id}/decision
GET /api/research-reports/{report_id}/trace
GET /api/research-reports/{report_id}/trace/nodes/{node_id}
GET /api/research-reports/{report_id}/trace/nodes/{node_id}/raw-response
```

首个端点可以按 `dataset_id` 查询全部重跑版本。页面默认选择最近一次成功或 partial 的报告，
用户可以切换历史版本比较。

主报告端点只返回三个 Tab 的状态、摘要和资源地址，三个大 payload 按 Tab 懒加载，避免首次打开
就发送全部技术与 Trace 数据。

## 8. 三个 Tab 的界面设计

正式页面建议使用路由：

```text
/research/reports/:reportId?tab=technical
/research/reports/:reportId?tab=decision
/research/reports/:reportId?tab=review
```

Tab 状态写入 URL，刷新和分享链接后仍能回到相同报告与相同 Tab。

### 8.1 公共页头

三个 Tab 共用：

- Run 类型与截止时间；
- 报告状态和数据质量；
- provider、model、Skill hash；
- 运行耗时、tool calls、token usage；
- 报告版本选择；
- “研究用途、未下单”固定提示。

### 8.2 Tab 1：技术整理报告

定位：事实底稿，不展示 AI 观点。

页面顺序：

1. 大盘与四个 ETF 摘要；
2. 宏观环境和事件；
3. 按 ETF、半导体、光概念、SaaS、大科技、航天与新兴分区的个股技术矩阵；
4. MACD、布林带、波动率、成交量 effort/result、相对强弱；
5. 期权 DEX/GEX、Gamma Flip、Gamma 区间、墙、Max Pain、Expected Move；
6. 数据质量、缺失字段和来源。

布局原则：

- 先摘要、再按题材子 Tab、最后展开单 symbol；
- 不再用一张超宽表承载所有字段；
- Evidence Reference 对应的区域具有稳定 anchor；
- AI 报告点击证据后切换到本 Tab 并高亮对应字段。

### 8.3 Tab 2：AI 决策报告

定位：模型观点的结构化展示，不直接展示原始 JSON。

页面顺序：

1. 市场环境分类、置信度和主要证据；
2. 股票/ETF 排名和 action；
3. 每个候选的 thesis、风险、缺失字段和失效条件；
4. 每个标的的期权入场过滤摘要、目标墙和结构风险；
5. 组合级 warnings、数据限制和免责声明。

每个证据条目都应是可点击链接。`execution_ready=false` 时使用显眼但不刺眼的状态标签，并隐藏
精确收益区，避免用户误认为可以直接下单。

### 8.4 Tab 3：AI 决策复盘

定位：类似 n8n 的只读执行图，不是聊天记录。页面不会主动铺开模型推理文本，但 provider 实际
返回的原始内容会保留，并允许用户在模型节点中主动查看。

桌面布局：

```text
┌────────────────────────────────────────────────────────────┐
│ 状态 / 总耗时 / 模型 / token / 工具次数 / 错误             │
├───────────────────────────────────┬────────────────────────┤
│                                   │ 节点详情               │
│  证据 → 股票模型 → 校验 → Gate    │ 时间、状态、输入摘要   │
│                     ├→ QQQ 期权   │ 工具参数、结果摘要     │
│                     ├→ INTC 期权  │ Evidence Reference     │
│                     └→ 跳过节点   │ 输出、错误、token      │
│                         ↓         │                        │
│                     报告组装      │                        │
└───────────────────────────────────┴────────────────────────┘
```

交互：

- 支持平移、缩放、适配画布和节点选择；
- lane 固定为 `Preparation / Equity / Gate / Options / Assembly`；
- 成功、运行、跳过、失败使用一致颜色；
- 点击工具节点显示参数、经过截断的结果和来源；
- 点击模型节点首先显示 model、temperature、输入摘要、token、结构化输出摘要；
- 模型节点提供默认折叠的“原始模型返回”调试区，用户主动展开后可以查看额外文本、格式失败内容
  和 provider 返回的 reasoning-like 字段；
- 点击校验节点显示 Schema 与业务规则通过/失败情况；
- 点击 Evidence Reference 可跳到技术报告；
- 原始模型返回必须标记为“未校验，不构成决策证据”，不得渲染其中的 HTML 或执行任何指令；
- 不显示 API Key、请求 header 或系统环境变量。

移动端不强行缩小整张图，改为按 lane 的纵向节点列表，节点详情使用抽屉。

## 9. 前端 Module 划分

建议新增：

```text
frontend/src/views/ResearchReportView.vue
frontend/src/components/research/ReportHeader.vue
frontend/src/components/research/TechnicalReportTab.vue
frontend/src/components/research/AIDecisionReportTab.vue
frontend/src/components/research/DecisionTraceTab.vue
frontend/src/components/research/TraceCanvas.vue
frontend/src/components/research/TraceNodeInspector.vue
frontend/src/stores/researchReport.ts
frontend/src/types/research.ts
```

`ResearchReportView` 只负责路由、报告版本和 Tab；三个报告 Module 各自拥有自己的 Interface 和
Implementation。Trace Canvas 不理解股票字段，AI Decision Report Tab 不理解图布局，从而保持
Locality。

## 10. 状态与失败呈现

- `succeeded`：股票与所有选中期权调用成功；
- `partial`：股票成功，但部分期权失败或不可用；
- `failed`：股票阶段失败，无法形成有效报告；
- `timed_out`：provider 超时；
- `disabled`：配置关闭，技术报告仍可读取，AI 两个 Tab 显示未运行原因。

失败的 Decision Session 不覆盖上次成功报告。页面默认打开最近成功/partial 版本，并在顶部提示
存在更新但失败的尝试。

## 11. 开发顺序

### Phase 1：完成调用 AI 的工作流（已完成实现，待真实联合回放）

1. 实现上下文预算和工具结果压缩；
2. 新增 Decision Session、Trace Node 模型与迁移；
3. Runtime 发出模型、工具、校验 Trace 事件；
4. 实现大盘 → 并发题材 → 携带确定性期权入场上下文的股票综合 Coordinator；
5. 实现 TechnicalReportV1 和 AIDecisionReportV2；
6. 替换 Step 4 占位契约并把 report/session/run ID 串起来；
7. 增加只读报告端点；
8. 使用 Fake Provider 做全流程集成测试；
9. 使用冻结数据执行股票综合与 QQQ 期权入场上下文真实联合验证。

### Phase 2：正式报告前端（已完成实现，待真实联合回放）

1. 新增 Research Report route、store 和类型；
2. 完成技术整理报告 Tab；
3. 完成 AI 决策报告 Tab；
4. 完成只读 Trace Canvas 和节点详情；
5. 增加 waiting_for_pair、technical_ready、running、失败、partial、timed_out 和移动端状态；
6. 运行前端单元测试、构建和浏览器视觉检查。

### Phase 3：全量与成本验证

1. 记录三次重复运行的合规率、工具数、延迟和 token；
2. 比较移除独立期权 Agent 前后的耗时和失败率；
3. 验证期权上下文确实影响边际股票的 buy/wait/avoid，而不覆盖强方向证据；
4. 保留每次报告版本用于复盘，不覆盖旧版本。

## 12. 验收标准

1. 一份冻结 Decision Dataset 能生成一份 Technical Report；一次成功 Decision Session 能生成
   一份 AI Decision Report 和一张 Decision Trace 图。
2. 大盘调用完成后，所有非空题材能以配置上限并发；股票综合收到所有请求标的的确定性
   `equity_option_context`。
3. 工作流不创建独立 Options Agent、Candidate Gate 或期权策略节点。
4. Step 4 返回并持久化 `session_id`、`report_id` 和所有 Agent Invocation ID。
5. 每个 Agent Invocation 在调用模型前已有 `running` 记录。
6. 模型 HTTP 调用不占用数据库事务。
7. 所有 AI Evidence Reference 能解析并跳转到技术报告字段。
8. 前端三个 Tab 读取同一 dataset、cutoff 和报告版本。
9. 复盘图默认展示可观察节点和工具结果；模型原始返回默认折叠、允许主动查看，并与正式决策
   证据明确区分。
10. `execution_ready=false` 的期权模板不会显示为可直接交易。
11. 重跑生成新版本，不覆盖历史报告。
12. Fake Provider 全流程、SQLite、前端构建和浏览器视觉检查全部通过。

# Stage 4B 研究报告前端重构方案

## 1. 本文结论

Stage 4B 的主要用户界面是 **Research Report Workspace（研究报告工作区）**，不是当前用于触发
1A、1B、2、3A、3B 和查看原始 read model 的开发页面。

当前 Dashboard、Runs 和 Run Detail 保留，但重新定位为 **Operations Console（采集与开发控制台）**。
它用于开发、采集验证、错误诊断和查看原始数据，不承担日常研究决策界面的职责。

研究报告工作区固定包含三个一级 Tab：

1. **技术整理报告**：确定性程序生成，以图、表和结构化指标为主，不包含 AI 观点。
2. **AI 决策报告**：展示通过 Schema、业务规则和 Evidence Reference 校验的 AI 结果，由程序排版。
3. **AI 决策复盘**：展示真实 Decision Trace、工具调用、结构化决策依据，以及用户主动查看的模型原始返回。

本文件是 Stage 4B 前端、路由和复盘交互的权威设计。若与
`stage4b-ai-workflow-ui-design.md` 的旧前端描述冲突，以本文为准。

当前实现状态（2026-08-04）：主入口、报告索引、数据集索引、Operations Console
路由、技术图表/矩阵/事件时间轴，以及带真实 SVG 依赖边的复盘 inspector 已落地。
Decision Dataset 的双 Run 持久化和独立报告生成仍按后续迁移阶段推进；过渡期旧 Run
报告链接仍可访问，但会明确显示数据来自单个 workflow evidence。

## 2. 两个产品面必须分离

### 2.1 Research Report Workspace：主要产品界面

面向日常研究使用，回答三个问题：

- 市场和关注标的现在处于什么技术状态；
- Urus Agent 得出了什么决策以及依据是什么；
- Agent 是如何读取证据、调用工具、通过校验并形成报告的。

主导航中的第一入口必须是“研究报告”，应用根路由也应进入此界面。

### 2.2 Operations Console：开发与采集界面

现有 Dashboard、Runs、Run Detail 属于该区域，负责：

- 手动触发盘前、收盘前和复盘采集；
- 查看 1A、1B、2、3A、3B、4、5 的执行状态；
- 检查 Moomoo、Anomalo、SQLite 和数据质量；
- 查看原始 snapshot、step payload 和开发期 JSON；
- 对失败步骤进行诊断。

它可以保留大量原始字段和 JSON，但必须明确标识为“开发工具”，不能再作为 Urus 的默认首页。

```text
Urus
├── 研究报告                  ← 默认入口、日常使用
│   ├── 最新报告
│   ├── 历史报告
│   └── 单份报告三个 Tab
├── 数据集                    ← 配对数据与质量审计
└── 开发工具                  ← Operations Console
    ├── 采集控制台
    ├── Workflow Runs
    └── Run Detail / Raw JSON
```

## 3. 报告不能继续从属于单个 Workflow Run

一份 Stage 4B 报告的事实来源是一个冻结的 **Decision Dataset**，不是一个当前 Workflow Run。

一个配对数据集至少包含：

- 当日 `pre_market` Workflow Run 和 Snapshot；
- 当日 `pre_close` Workflow Run 和 Snapshot；
- 两个阶段各自的 1A、1B、2、3A、3B 数据；
- 截止时间以内的事件记录和事件结果；
- 程序计算的 `paired_changes`；
- 数据质量、缺失项、来源和 `content_sha256`。

一个 Decision Dataset 可以产生多个 Decision Session，用于重跑不同模型、温度、Skill 版本或候选策略。
因此正确关系是：

```text
pre_market Run ─┐
                ├─ Decision Dataset ─┬─ Technical Report
pre_close Run ──┘                    ├─ Decision Session A ─ AI Report A + Trace A
                                     └─ Decision Session B ─ AI Report B + Trace B
```

报告页面必须以 `report_id` 为主身份；`run_id` 只是来源元数据，不能再作为报告的永久路由主键。

`Research Report` 是不可变的展示版本：

- Decision Dataset 冻结后创建一个 `technical_ready` 报告，`decision_session_id=null`；
- 每次 AI 重跑创建新的 `report_id`，引用同一 `dataset_id` 和新的 `decision_session_id`；
- 历史版本按 `dataset_id` 归组，但不会覆盖；
- AI 未运行时不创建 `session disabled` 之类的伪 Decision Session。

## 4. 路由和导航设计

### 4.1 正式路由

```text
/
  → 重定向到 /research

/research
  最新可用报告首页；没有报告时展示数据准备状态

/research/reports
  历史报告列表，按交易日、状态、模型和数据集筛选

/research/reports/:reportId
  单份报告的 canonical URL

/research/datasets
  冻结配对数据集列表与质量状态

/research/datasets/:datasetId
  数据集来源、两个采集阶段、缺失项和可重放状态

/operations
  采集与验证控制台

/operations/runs
  Workflow Run 列表

/operations/runs/:runId
  Workflow Run 详情与原始 read model
```

### 4.2 报告 URL 状态

报告 Tab 和当前查看对象写入 URL，以便刷新、收藏和分享：

```text
/research/reports/:reportId?tab=technical
/research/reports/:reportId?tab=technical&section=options&symbol=QQQ&expiration=2026-08-07
/research/reports/:reportId?tab=technical&theme=semiconductor&symbol=NVDA
/research/reports/:reportId?tab=technical&symbol=QQQ&expiration=2026-08-07
/research/reports/:reportId?tab=decision&symbol=QQQ
/research/reports/:reportId?tab=review&node=model-turn-id
```

Evidence Reference 使用 hash 定位技术报告字段：

```text
/research/reports/:reportId?tab=technical&symbol=QQQ#options-qqq-2026-08-07-gamma-flip
```

### 4.3 兼容旧链接

迁移期间保留旧路由，但只做跳转：

```text
/runs                         → /operations/runs
/runs/:runId                  → /operations/runs/:runId
/runs/:runId/report           → 查找该 Run 对应的最新 report_id 后跳转
/research-reports/:reportId   → /research/reports/:reportId
```

无法解析 `run_id → report_id` 时，旧报告链接跳到对应数据集或 Run Detail，并说明报告尚未生成。

## 5. 研究报告整体页面构造

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Urus   研究报告   历史报告   数据集                     开发工具     │
├──────────────────────────────────────────────────────────────────────┤
│ 2026-08-03 研究报告        succeeded / quality ok                    │
│ 盘前 21:30 → 收盘前 04:00   dataset / cutoff / provider / model      │
│ [报告版本] [重新运行 AI] [数据来源] [研究用途，不执行交易]           │
├──────────────────────────────────────────────────────────────────────┤
│ 技术整理报告             AI 决策报告             AI 决策复盘         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│                         当前 Tab 内容                                │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

公共页头只显示用户判断有用的信息：

- 交易日和报告版本；
- 盘前、收盘前观察时间；
- 数据质量和缺失项数量；
- Decision Session 状态；
- provider、model、Skill 版本；
- token、成本和耗时放入“运行信息”折叠区；
- 固定显示“研究用途，不构成投资建议，不执行交易”。

`session_id`、hash 和内部 schema 默认不占据主视觉；它们进入运行信息或数据来源抽屉。

## 6. Tab 1：技术整理报告

### 6.1 设计原则

- 主要表达形式是图、表、热力图、状态标签和小型趋势图。
- 普通页面不直接打印大块 JSON。
- 文本只用于结论标签、数据质量说明和图表无法表达的限制。
- 原始字段保留在每个模块的“数据详情”折叠区，供开发核对。
- 此 Tab 不出现 AI thesis、action 或交易观点。

### 6.2 页面结构

技术整理报告再分为四个二级 Tab，避免市场、个股、期权和事件内容在一页连续纵向堆叠：

| 二级 Tab | 内容 |
|---|---|
| 总览 | 数据质量、市场总览、盘前到收盘前的配对变化和缺口 |
| 个股技术 | 按题材切换的技术矩阵与单标的详情 |
| 期权结构 | DEX/GEX、Gamma Profile、Gamma Flip、墙和行权价明细 |
| 事件时间轴 | 预期事件、已发生事件和结果状态 |

二级 Tab 使用 `section=overview|instruments|options|events` 写入 URL；页面只渲染当前分区，原始字段仍保留在当前分区的折叠详情中。

#### A. 数据质量摘要

使用一行状态卡展示：

| 指标 | 展示 |
|---|---|
| 配对状态 | 盘前和收盘前是否齐全 |
| 数据来源 | Moomoo snapshot / history、Anomalo event |
| 标的覆盖 | 已采集 / 计划数量 |
| 期权覆盖 | 有效 symbol / 到期日数量 |
| 事件状态 | 日历完整、结果待补、已完成 |
| 缺失项 | 按严重程度分组 |

存在 blocking error 时使用页面级提示，不能只把错误藏进 JSON。

#### B. 市场总览与盘中变化

- SPY、QQQ、SMH、IGV 四个核心 ETF 使用并排小卡。
- 每张卡显示盘前价、收盘前价、阶段涨跌、成交量变化、技术状态和相对强弱。
- 使用斜率线或 dumbbell chart 表示 `pre_market → pre_close`，避免用两段 JSON。
- VIX、利率、美元、黄金、原油等跨资产使用紧凑表格或热力格。
- 明确标识 pre-market volume 与 cumulative regular-session volume 不可直接同比。

#### C. 个股技术矩阵

一级题材 Tab 固定为：

```text
ETF / 半导体 / 光概念 / SaaS / 大科技 / 航天与新兴
```

每个题材内使用可排序表：

| Symbol | 盘前→收盘前 | 相对 QQQ | MA 状态 | Bollinger | MACD | 量价 E/R | RV20 | 质量 |
|---|---:|---:|---|---|---|---|---:|---|

点击一行打开 symbol 详情抽屉：

- 价格与成交量小图；
- MA20/50/200；
- Bollinger 1σ/2σ/3σ；
- MACD DIF、DEA、柱体与交叉；
- Effort/Result 完整情形；
- 相对强弱；
- 盘前与收盘前变化；
- 数据质量与来源。

#### D. 期权结构

先选 symbol，再选 expiration。单个到期日页面包含：

- Spot、Expected Move、Max Pain、Call Wall、Put Wall；
- DEX/GEX 按行权价横向图；
- Spot Gamma Profile；
- Gamma Flip；
- 正 Gamma、负 Gamma 区间背景；
- 关键行权价表，只高亮 Spot、墙、Max Pain、变号点；
- 盘前与收盘前墙、Flip、DEX/GEX 的变化表。

原始 gamma zones 和 profile points 只在“数据详情”中展开。

#### E. 事件时间轴

按时间展示宏观事件和个股事件：

```text
时间 → 事件 → 预期值 → 实际值 → 状态 → 关联标的 → 来源
```

未来事件和已发生事件分组，缺少结果时显示“待结果采集”，不输出完整事件 JSON。

## 7. Tab 2：AI 决策报告

此 Tab 只消费通过校验的结构化 AI 输出，程序负责排版，不允许模型生成 HTML 或整页 Markdown。

页面顺序：

1. 市场环境：classification、confidence、主要 Evidence Reference；
2. 股票/ETF 排名表：rank、action、score、confidence、SEPA 完整度；
3. 候选详情：thesis、风险、缺失字段、失效条件；
4. Candidate Gate：每个 symbol 的 selected/skipped 和确定性原因；
5. 期权决策：gamma regime、horizon、结构模板、情景锚点和不确定性；
6. 组合级警告、数据限制和免责声明。

每条证据可以点击并跳到技术报告对应图表或表格行。`execution_ready=false` 时不显示看似精确的
收益数字，也不出现“下单”“买入”等操作按钮。

## 8. Tab 3：AI 决策复盘

### 8.1 复盘不是聊天记录

主体是一个真实只读 DAG：

```text
Evidence Bundle
      ↓
Equity Skill → Model Turn → Tool Calls → Validation
                                      ↓
                               Candidate Gate
                           ┌──────────┼──────────┐
                           ↓          ↓          ↓
                        selected   selected    skipped
                           ↓          ↓
                      Option AI   Option AI
                           └──────┬───┘
                                  ↓
                           Report Assembly
```

节点必须使用实际 edge 绘制 SVG 连线和箭头，不能把 edge 作为文字列表放在画布底部。

### 8.2 节点详情面板

选择节点后显示：

1. 状态、开始/结束时间和耗时；
2. 节点输入范围，不显示整份巨大数据包；
3. 使用的 Skill、model、temperature 和 prompt/schema 版本；
4. 工具调用时间线：参数摘要、结果摘要、Evidence Reference、错误；
5. Schema、业务规则和 Evidence Reference 校验；
6. 结构化输出摘要；
7. token 和成本；
8. 用户主动打开的原始模型返回。

### 8.3 “AI 思考过程”分成两层

#### 第一层：可审计决策依据

这是正式产品能力，始终可以查看。输出 Schema 应要求模型返回简短、结构化、可验证的依据，例如：

```json
{
  "decision_rationale": [
    {
      "step": 1,
      "claim": "QQQ remains above MA200 but is below MA50",
      "evidence": ["observations.pre_close.instruments[QQQ].technical.moving_average"],
      "effect": "trend is constructive but not fully confirmed"
    }
  ]
}
```

它不是要求模型泄露私有 Chain of Thought，而是要求模型给出对人类可读、对证据可校验的决策依据。

#### 第二层：Provider 实际返回的 reasoning-like 内容

有些模型或 Harness 会在响应中实际返回 `reasoning`、`analysis`、`thinking`、额外文本或格式失败内容。
Urus 必须原样保存这些内容，但不主动展开。

模型节点提供按钮：

```text
查看模型原始返回
```

展开后分区展示：

- Structured final content；
- Tool calls；
- Provider-returned reasoning / analysis；
- Complete raw response。

如果响应中没有 reasoning-like 字段，显示“本次供应商未返回可见推理字段”，不能由 Urus 猜测或补写。

原始内容必须：

- 默认折叠并按需请求；
- 标记为“未校验，不构成决策证据”；
- 使用纯文本或 JSON 渲染，不执行 HTML；
- 不保存或显示 API Key、Authorization header 和环境变量；
- 显示原始字节数与截断状态。

## 9. 前端 Module 边界

建议结构：

```text
frontend/src/views/
  ResearchHomeView.vue
  ResearchReportsView.vue
  ResearchReportView.vue
  ResearchDatasetView.vue
  OperationsDashboardView.vue
  OperationsRunsView.vue
  OperationsRunDetailView.vue

frontend/src/components/research/
  ReportHeader.vue
  TechnicalReportTab.vue
  MarketPairOverview.vue
  InstrumentThemeMatrix.vue
  InstrumentDetailDrawer.vue
  OptionsStructurePanel.vue
  EventTimeline.vue
  DecisionReportTab.vue
  CandidateGatePanel.vue
  OptionDecisionCard.vue
  DecisionTraceTab.vue
  TraceCanvas.vue
  TraceNode.vue
  TraceEdgeLayer.vue
  TraceNodeInspector.vue
  RawModelResponsePanel.vue
```

边界要求：

- View 只负责路由、报告版本和 Tab 状态；
- 技术组件只读取 Technical Report DTO；
- AI 决策组件只读取已校验 Decision Report DTO；
- Trace Canvas 不理解股票或期权业务字段；
- Inspector 通过节点类型选择专用展示器；
- RawModelResponsePanel 是唯一允许展示 provider 原始响应的组件。

## 10. Read API 调整

建议正式接口：

```text
GET  /api/research/reports
GET  /api/research/reports/{report_id}
GET  /api/research/reports/{report_id}/technical
GET  /api/research/reports/{report_id}/decision
GET  /api/research/reports/{report_id}/trace
GET  /api/research/reports/{report_id}/trace/nodes/{node_id}
GET  /api/research/reports/{report_id}/trace/nodes/{node_id}/raw-response

GET  /api/research/datasets
GET  /api/research/datasets/{dataset_id}
POST /api/research/datasets/{dataset_id}/decision-sessions
```

主报告元数据至少包含：

```text
report_id
dataset_id
decision_session_id | null
trading_date
pre_market_run_id
pre_close_run_id
pre_market_observed_at
pre_close_observed_at
cutoff_time
technical_status
decision_status
quality
provider / model / skill hashes
```

Tab payload 继续懒加载；原始模型响应必须再使用单独的显式端点。

## 11. 页面状态

报告页面必须分别处理：

- `waiting_for_pair`：只有盘前数据，等待收盘前观察；
- `technical_ready`：配对数据完整，技术报告可读，尚未运行 AI；
- `decision_running`：AI 后台运行中，技术报告仍可读；
- `succeeded`：全部完成；
- `partial`：股票成功，部分期权失败；
- `failed`：本次 Decision Session 失败，旧报告版本仍可选；
- `timed_out`：单独展示超时，不合并成普通 failed；
- `blocked`：数据质量阻止模型运行。

`URUS_AGENT_ENABLED=false` 不应生成虚假的 `session disabled`。它表示 Technical Report 已存在、
Decision Session 尚未运行；页面应显示“技术报告可用，AI 决策未运行”。

## 12. 分阶段迁移

### Phase A：纠正信息架构和路由

1. 新增 `/research` 和 `/research/reports/:reportId`；
2. 将当前 Dashboard/Runs 移到 `/operations`；
3. 保留旧路由重定向；
4. 修改主导航，研究报告成为默认入口；
5. 报告路由从 `run_id` 切换到 `report_id`。

### Phase B：重做技术报告

1. 去掉默认大块 JSON；
2. 实装市场配对图和核心 ETF 卡；
3. 实装题材技术矩阵和 symbol 抽屉；
4. 实装期权图表与到期日选择；
5. 实装事件时间轴；
6. 原始数据全部下沉到折叠详情。

### Phase C：重做 AI 复盘

1. 增加结构化 `decision_rationale`；
2. 拆分 Trace Canvas、Edge Layer 和 Inspector；
3. 使用真实 SVG edge；
4. 展示 selected/skipped/failed/timed_out 分支；
5. 增加按需 RawModelResponsePanel；
6. 单独显示 provider-returned reasoning-like 字段。

### Phase D：与真实配对数据联合验收

1. 使用 2026-08-03 已保存的盘前/收盘前数据；
2. 验证两个阶段没有被复制或混用；
3. 使用同一 Dataset 重跑至少两个 Decision Session；
4. 检查 URL 刷新后 Tab、symbol、expiration 和 node 状态保持；
5. 桌面与移动端进行视觉验收；
6. 确认普通报告请求不会自动加载原始模型响应。

## 13. 验收标准

1. 应用首页是研究报告，不是采集控制台。
2. 开发页面与研究页面在导航、路由和视觉语义上明确分开。
3. 报告 canonical URL 使用 `report_id`，不依赖一个 Workflow Run。
4. 技术报告主要由图和表组成，默认页面没有大块 JSON。
5. 盘前与收盘前来自两个真实来源，配对变化可以追溯。
6. AI 决策报告只展示通过校验的结构化内容。
7. 复盘页使用真实节点和 edge 展示执行路径。
8. 模型节点始终可查看结构化决策依据。
9. provider 实际返回 reasoning-like 内容时，用户可以主动查看；没有返回时不伪造。
10. 原始模型响应默认不加载、不展示、不执行 HTML。
11. 同一 Dataset 的多次 AI 重跑形成多个独立报告版本。
12. Operations Console 保留采集、步骤诊断和原始 JSON 能力，但不再承担主前端职责。

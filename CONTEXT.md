# Urus Research Context

本文统一 Urus 数据采集、AI 决策、报告展示和复盘中使用的领域词汇。

## Language

**Workflow Run**:
一次具有固定 `run_id` 和 `cutoff_time` 的数据采集执行；盘前和收盘前是两个不同的 Workflow Run。
_Avoid_: Job, task, batch

**Technical Report**:
由确定性程序根据冻结的 **Decision Dataset** 数据生成的事实整理报告，主要使用图、表和结构化指标。
_Avoid_: AI summary, analysis dump

**Decision Dataset**:
由同一交易日盘前和收盘前两个 **Workflow Run** 组成的不可变 Stage 4B 证据包；一个数据集可以产生
多个 **Decision Session**。
_Avoid_: Latest run, current frontend snapshot

**Decision Session**:
基于一个冻结 **Decision Dataset** 的完整 Stage 4B AI 执行，包含股票决策、候选分流、期权决策
和报告组装；同一数据集可以重跑多个 Decision Session。
_Avoid_: Chat session, agent conversation

**Research Report**:
面向人类研究的不可变展示版本，引用一个 **Decision Dataset** 和零或一个 **Decision Session**。
数据集冻结后先产生 technical-only 报告；每次 AI 重跑产生新的报告版本，不伪造 Decision Session。
_Avoid_: Run detail, latest mutable dashboard

**Agent Invocation**:
一个 **Decision Session** 中具有单一 Task、Skill、证据范围和输出 Schema 的 Urus Agent 调用。
_Avoid_: Agent, prompt, turn

**Market Context**:
一次大盘 **Agent Invocation** 对 SPY、QQQ、SMH、SOXX、IGV、宏观事件和质量状态形成的结构化市场判断；
它是题材分析的已校验上游输入。
_Avoid_: Market chat, global raw dump

**Order-Size Capital Flow**:
Moomoo 按成交订单金额分档提供的日度主动净流量。Urus 只补最近已完整收盘且尚未缓存的一个交易日，
数据库长期保留观测，以最近 30 个交易日计算规则信号，只向盘前 **Pre-Market Composite Decision**
投影最近 5 日。大额单连续流出后转正且中小额单仍流出只能称为吸收候选；中小额单流入但大额单
流出只能称为派发风险。订单金额档位不等同于机构、散户或账户身份。
_Avoid_: Institutional flow, retail identity, confirmed smart money

**Theme Decision**:
一次题材 **Agent Invocation** 对明确 symbol 范围进行的局部排名；多个题材调用可以并发执行，但只能读取
各自 symbol 与题材基准。
_Avoid_: Sector summary text, unrestricted watchlist scan

**Equity Synthesis**:
在全部 **Theme Decision** 完成后，对 **Market Context** 和已校验题材输出进行跨题材排序的
**Agent Invocation**；不再读取原始数据工具。
_Avoid_: Report rewrite, second fact investigation

**Pre-Market Composite Decision**:
由确定性程序先把市场、题材、标的、事件、期权入场上下文和有效 **Forecast Experience** 编译成紧凑
只读投影，再由单次无工具 **Agent Invocation** 同时形成大盘预测、题材判断和全标的可评分预测；前五名是
详细关注清单，其余标的保持简洁但必须保留可评分字段。旧的 Market Context → Theme Decision → Equity
Synthesis 多模型链路仅作为历史实现，不再用于正式盘前运行。
_Avoid_: Multi-agent debate, per-theme model fan-out

**Forecast Evaluation**:
由确定性程序把正式盘前预测与同日官方收盘事实逐项对照形成的可审计评分；至少区分市场方向、
主题领先/落后、标的方向、预期区间、相对表现和置信度校准。模型只能解释评分，不能修改 verdict、
score、实际收益或 Brier 值。
_Avoid_: AI self-score, hindsight opinion

**Post-Close Review**:
在 **Forecast Evaluation** 完成后，由单次无工具 **Agent Invocation** 解释当日关键结果、预测偏差和
可验证经验的正式复盘。它不重新运行 **Theme Decision**、不产生全市场排名，也不提出同日交易。
_Avoid_: Closing stock recap, second Equity Synthesis

**Forecast Experience**:
由 **Post-Close Review** 提出的可证伪经验假设，保存稳定 pattern key、适用市场标签、证据、支持与
反例次数及状态。盘前只继承数量受控且仍有效的经验，不继承整份历史复盘作为长期记忆。
_Avoid_: Free-form lesson, prompt memory

**AI Decision Report**:
由确定性代码合并一个 **Decision Session** 中已通过校验的股票和期权输出形成的只读报告。
_Avoid_: AI summary, rewritten report

**Decision Trace**:
一个 **Decision Session** 中证据准备、模型原始返回、工具调用、校验、分流和组装的可观察执行轨迹。
_Avoid_: Chain of thought, chat history

**Decision Rationale**:
模型按输出 Schema 返回的简短、结构化、带 **Evidence Reference** 的决策依据。它可以主动展示并接受
本地校验，不等同于模型不可见的私有 Chain of Thought。
_Avoid_: Hidden reasoning, raw model thoughts

**Provider-returned Reasoning**:
供应商响应中实际存在的 `reasoning`、`analysis` 或类似原始字段。属于未校验的审计材料，默认不加载、
不展示，但允许用户在模型节点中主动查看。
_Avoid_: Reconstructed thought, validated evidence

**Research Report Workspace**:
Urus 面向日常研究的主要前端，包含技术整理、AI 决策和 AI 决策复盘三个 Tab。
_Avoid_: Dashboard, operations console

**Operations Console**:
用于采集触发、步骤验证、错误诊断和原始 JSON 检查的开发界面。
_Avoid_: Main research UI

**Evidence Reference**:
指向冻结证据中 dataset、run、snapshot、phase、path 和时间信息的稳定字段引用。
_Avoid_: Citation text, source note

## Relationships

- 两个配对的 **Workflow Run** 形成一个 **Decision Dataset**，并产生一个 **Technical Report**。
- 一个 **Research Report** 始终引用一个 **Decision Dataset**；AI 尚未运行时其 Decision Session 可以为空。
- 一个 **Decision Dataset** 可以产生多个重跑版本的 **Decision Session**。
- 盘前 **Decision Session** 先生成一个确定性证据投影，再运行一个无工具的 **Pre-Market Composite
  Decision**；评分所需的全标的预测与前五名详细关注清单来自同一次 **Agent Invocation**。
- 盘后 **Decision Session** 先生成 **Forecast Evaluation**，再运行一个无工具的 **Post-Close Review**；
  它可以产生零到多个候选 **Forecast Experience**。
- 一个 **Decision Session** 产生一个 **AI Decision Report** 和一个 **Decision Trace**。
- 一个 **AI Decision Report** 包含多个 **Evidence Reference**，每个引用定位到同一 **Decision Dataset** 的 **Technical Report**。

## Example dialogue

> **Dev:** “重新运行模型时，要更新原来的 **AI Decision Report** 吗？”
> **Domain expert:** “不要。为同一个 **Decision Dataset** 创建新的 **Decision Session**，旧报告和 **Decision Trace** 都必须保留。”

## Flagged ambiguities

- “run” 曾同时指 **Workflow Run** 和单次模型调用；后者统一称为 **Agent Invocation**。
- “复盘”包括可校验的 **Decision Rationale**；provider 实际返回的额外文本或 reasoning-like 字段属于
  **Provider-returned Reasoning**，默认折叠但允许用户主动查看。
- “Dashboard”曾被同时用于研究首页和开发控制台；正式研究入口统一称为
  **Research Report Workspace**，采集页面统一称为 **Operations Console**。

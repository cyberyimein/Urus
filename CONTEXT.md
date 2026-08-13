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
一次大盘 **Agent Invocation** 对 SPY、QQQ、SMH、IGV、宏观事件和质量状态形成的结构化市场判断；
它是题材分析的已校验上游输入。
_Avoid_: Market chat, global raw dump

**Theme Decision**:
一次题材 **Agent Invocation** 对明确 symbol 范围进行的局部排名；多个题材调用可以并发执行，但只能读取
各自 symbol 与题材基准。
_Avoid_: Sector summary text, unrestricted watchlist scan

**Equity Synthesis**:
在全部 **Theme Decision** 完成后，对 **Market Context** 和已校验题材输出进行跨题材排序的
**Agent Invocation**；不再读取原始数据工具。
_Avoid_: Report rewrite, second fact investigation

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
- 一个 **Decision Session** 包含一个大盘 **Agent Invocation**、零到多个并发题材 **Agent Invocation**、
  一个股票综合 **Agent Invocation** 和零到多个期权 **Agent Invocation**。
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

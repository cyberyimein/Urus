# 三层决策 AI：策略仲裁与双向再学习路径

> 状态：提案
> 详细版备份：[decision-ai-learning-loop-design.v1-detailed.md](archive/decision-ai-learning-loop-design.v1-detailed.md)
> 开发规格：[日 K Decision Harness 开发设计](daily-k-decision-harness-development-design.md)

## 1. 核心想法

Urus 不先做一个直接预测大盘涨跌的 AI，而先做一个“策略仲裁 AI”：

> 数据层提供事实，策略层独立产生决策，AI 层判断采信哪个策略，反馈层用回溯和实际运营结果持续优化两者。

完整链路是：

~~~text
数据采集
  → 多策略独立决策
  → AI 策略仲裁
  → 回溯 / 模拟实战 / 实际运营
  → 优化策略算法
  → 优化 AI 的信任和偏见
~~~

第三层 AI 不是“再做一次技术分析”，而是回答：

- 当前哪些策略值得信任？
- 策略冲突时采信谁？
- 是否所有策略都不值得采信？
- AI 自己是否对某种策略存在系统性偏见？

## 2. 系统是一个 Decision Harness

三层不是三个互相独立的脚本，而是由一个 Decision Harness 统一编排：

- 决策前冻结当前数据和质量状态；
- 运行全部适用的策略；
- 检索相似的历史 Decision Case；
- 组装有限的 AI 输入；
- 调用 AI 并校验输出；
- 保存 Decision Session、Decision Trace 和当前 Decision Case；
- 到达 horizon 后补入实际结果并触发评价、复盘和学习。

Harness 是逻辑上的整体控制面，但运行时分成两个平面：Urus 负责事实、策略、流程设计、触发、结果投影和
决策账本；Anomalo 负责对齐并执行 Urus 提交的 Decision Workflow Definition。AI 只能处理 Workflow JSON
声明并由冻结 evidence manifest 提供的证据，不能自行绕过数据、策略、案例和评价流程。

Urus 不复制 Anomalo 的 Workflow 执行器、模型调度或 Skill 运行能力。Urus 保存版本化 Workflow JSON，
Anomalo 返回对齐回执、远端运行状态和结构化结果；双方用 definition/input hash 保证执行的是同一流程和输入。

### 三层架构

| 层 | 主要职责 | 输出 | 不负责 |
| --- | --- | --- | --- |
| 数据层 | 采集个股、大盘、技术指标、新闻 RSS、事件、期权和质量信息 | 带时间、来源和质量的 Data Snapshot | 不做最终交易判断 |
| 策略层 | 运行多种完整的算法策略 | 每个策略独立的 Strategy Decision | 不调用 LLM，不受 AI 临时改规则 |
| AI 层 | 比较策略结果，选择、否决或放弃 | Strategy Arbitration Decision | 不补数据，不重算指标，不改策略算法 |
| 反馈层 | 评价每个策略和 AI 的选择，产生新版本 | Evaluation、Experience、版本晋级/回滚 | 不把一次成功直接变成规则 |

## 3. 第一层：数据采集

第一版采集：

- 个股行情、日线、技术指标、成交量；
- SPY、QQQ、行业 ETF、跨资产和波动率；
- 当日新闻 RSS、宏观事件、财报和公司事件；
- 期权结构、资金流等可选上下文。

RSS 不直接以长文本喂给 AI。先保存原文和来源，再规范为 News Event，至少有：

~~~text
published_at / source / symbols_or_themes
event_type / relevance / quality
~~~

数据层要保证每个事实有 cutoff_time、来源、采集时间和质量状态，防止回溯时偷看未来。

## 4. 第二层：策略库

策略层是一个 Strategy Library。每个策略都是独立的、完整的、算法驱动的 Module：

~~~text
Strategy Input → Strategy Algorithm → Strategy Decision
~~~

策略可以使用数据层的技术、市场和事件字段，但不调用 LLM，也不读取其他策略的自然语言结论。

第一版至少需要两种风格不同的策略，否则无法验证 AI 是否真的会选择：

- Trend / Momentum：趋势、突破、相对强弱、成交量确认；
- Mean Reversion：超跌、偏离、波动回归、反转确认。

每个策略必须输出统一结构：

~~~text
strategy_name / version / hash
symbol / action / horizon
score_or_probability / confidence_type
reasons / risks
confirmation_conditions / invalidation_conditions
evidence_refs
~~~

策略输出已经是完整决策，例如：

~~~text
trend_momentum_v1 → watch
mean_reversion_v1 → avoid
~~~

策略可以独立回放、独立评分和独立升级。策略之间的冲突必须保留，不能提前平均掉。

## 5. 第三层：AI 策略仲裁

AI 的输入是：

- 当前 Market Context、News Event 和数据质量；
- 所有策略的结构化 Strategy Decision；
- 各策略在相似市场状态、主题和 horizon 下的历史表现；
- 已验证或正在 shadow 的经验。

### 相似案例检索

以 RSI14 策略为例，策略先输出当前的 RSI14 信号；Harness 再根据当前状态检索相似的已结案 Decision Case。

检索条件可以包括：

- 策略名称和版本；
- RSI14 信号类型与数值区间；
- 市场 regime、行业主题和预测 horizon；
- 价格相对均线、相对强弱、成交量和波动率；
- 事件或新闻风险状态；
- 数据质量。

V1 先使用结构化条件和加权相似度检索 Top-K，后续再考虑向量检索。返回给 AI 的是有限的 Case Card，而不是完整历史原文：

~~~text
case_id / similarity
当时的 Strategy Decision
当时的 AI Arbitration Decision
实际结果 / error_tags
适用条件 / 反例
~~~

案例必须同时包含成功、失败和未决策案例，不能只检索“看起来成功”的案例。检索只能使用当前 cutoff_time 之前已经结案的案例，并记录 retrieval_version、query_fingerprint 和返回的 case_id，防止未来数据泄漏并保证可复现。

AI 的第一版输出只允许：

1. 采信一个策略；
2. 在明确规则下采信多个策略；
3. 不采信任何策略，输出 no_action。

每次仲裁都要记录选中了谁、否决了谁、冲突是什么、为什么选择以及失效条件。第一版建议先只允许“选一个或全部放弃”，组合加权以后再做。

AI 不得：

- 新增策略没有提供的 symbol；
- 补齐缺失数据或重算指标；
- 因为某个策略昨天成功就永久信任它；
- 因为某个策略连续失败就永久禁用它；
- 修改策略算法、阈值或客观评分；
- 输出自动交易指令。

## 6. 反馈层：两条学习线

### A. 优化策略算法

回答：

> 这条策略的规则、参数、输入或适用市场状态是否需要改变？

流程：

~~~text
策略版本
  → 历史回放
  → walk-forward
  → shadow
  → 新旧版本对照
  → active 或 rollback
~~~

评价的是策略自己的结果：方向、收益、回撤、换手、成本、滑点和不同市场状态下的稳定性。

### B. 优化 AI 对策略的看法

回答：

> AI 是否在某些市场状态中过度信任或过度怀疑某种策略？

评价维度包括：

- AI 选中的策略实际结果；
- AI 没选中的策略如果执行会怎样；
- AI 是否错过了当日更好的策略；
- AI 是否过度追逐近期表现；
- AI 是否受新闻情绪、单一指标或某个主题影响过大。

这条线优化的是策略选择、信任权重和偏见，不是直接修改策略算法。

### 必须保存所有策略的结果

即使 AI 没有采信某个策略，也要保存它的 Strategy Decision，并在 horizon 到达后评分。否则只能知道 AI 选中的结果，不知道 AI 是否错过了更好的选择。

策略评价和 AI 仲裁评价必须分开，不能混成一个总分。

## 7. 回溯、模拟实战、实际运营

三种状态都进入同一条反馈链，但权限不同：

| 状态 | 用途 | 是否影响正式决策 |
| --- | --- | --- |
| 历史回放 | 验证策略和仲裁逻辑 | 否 |
| 模拟实战 / shadow | 用当前数据观察新版本 | 否 |
| 实际运营 | 使用 active 版本并记录真实后续结果 | 是 |

实际运营的每次 Decision Session 都要保存：

- 当时的 Data Snapshot；
- 所有策略输出；
- AI 仲裁结果；
- 检索到的 case_id、检索版本和当前案例 ID；
- 之后的实际结果；
- 当时使用的策略、Skill 和模型版本。

生产环境可以更新表现统计，但不能让统计结果直接改写 active 规则。新版本必须经过回放、shadow 和人工批准，再晋级 active；退化时按 hash 回滚。

### Decision Case 生命周期

一个案例在决策时创建，而不是等复盘完成后才创建：

~~~text
open
  → resolved（horizon 到达，实际结果已写入）
  → reviewed（复盘完成）
  → experience_candidate / validated_pattern
~~~

案例至少保留：

- 当前 Data Snapshot 和 cutoff_time；
- 全部 Strategy Decision；
- AI Arbitration Decision；
- AI 实际使用的历史案例引用；
- Forecast Evaluation、Strategy Evaluation、Arbitration Evaluation；
- Post-Close Review 和后续经验候选。

原始决策不可回写；后续结果以追加方式关闭案例。这样可以区分“当时的判断”和“事后的解释”。

## 8. 最小可实现路线

### Phase 1：共同契约

先固定 Data Snapshot、News Event、Strategy Decision、Arbitration Decision、Decision Case 和 Evaluation 的结构。

### Phase 2：两种独立策略

先实现 Trend/Momentum 和 Mean Reversion。两者都能独立运行、回放和评分。

### Phase 3：非 AI 基线

先用固定规则做策略选择，例如按市场状态选择趋势或均值回归。没有基线，就无法证明 AI 仲裁带来了增益。

### Phase 4：AI 仲裁

AI 读取全部策略输出、历史表现和相似 Case Card，只决定采信谁、为什么、是否放弃。保留基线和 AI 结果做 A/B。

### Phase 5：双向再学习

分别建立 Strategy Evaluation 和 Arbitration Evaluation：

- 前者改进策略算法；
- 后者改进 AI 的策略选择、信任和偏见。

## 9. 接入现有 Urus

现有链路可以演进为：

~~~text
Workflow Run
  → Decision Dataset / Technical Report
  → Strategy Library
  → Strategy Decisions
  → Strategy Arbitration AI
  → AI Decision Report / Decision Trace
  → Forecast Evaluation + Strategy Evaluation + Arbitration Evaluation
  → Post-Close Review
  → Forecast Experience
~~~

现有的 Pre-Market Composite Decision 可以转成策略仲裁的 Agent Invocation。当前 urus-equity-decision 直接生成最终排名的部分，应逐步拆成：

- 算法策略产生 Strategy Decision；
- AI 选择和解释策略；
- 程序合并、评分和复盘。

优先加深的 Module：

1. Strategy Registry：管理策略 Adapter、版本和输出契约；
2. Strategy Evaluation：评价每个策略自己的结果；
3. Strategy Arbitration：评价 AI 是否选对策略；
4. Case Retrieval：检索相似历史案例并记录检索血缘；
5. Experience Registry：保存经验和版本晋级状态。

当前代码已有 Decision Session、Decision Run、Decision Trace 和 Forecast Experience 的审计骨架，
但还没有独立的 Decision Case 实体。不要把案例继续塞进 Forecast Experience；最小实现应新增
Decision Case Module，保存 case_id、session_id、cutoff_time、horizon、status、全部策略输出、
AI 仲裁结果、检索到的 case_id、实际结果和两套 Evaluation。案例关闭逻辑只追加 outcome，
不回写原始决策。

## 10. 成功标准

1. 每次运行都能看到数据、所有策略输出和 AI 最终选择；
2. 未被选择的策略也有实际结果，能计算 AI 是否错过更优选择；
3. AI 使用过哪些历史案例、为什么使用，都能追溯；
4. 当前 Decision Case 会在 horizon 后自动补齐实际结果；
5. 策略算法优化和 AI 仲裁优化有两套独立评价；
6. 新版本先回放、再 shadow、后 active；
7. 每个 active 版本都有 hash、适用范围和回滚点；
8. 样本不足时输出 unknown 或 no_action，不强行选择；
9. 不自动交易，不自动修改生产策略，不用单日结果宣称准确。

## 11. 现在只需要确定三件事

1. 第一批策略是否确定为 Trend/Momentum + Mean Reversion；
2. 第一版 horizon 是否固定为盘前到收盘；
3. 新闻 RSS 第一版只做事件风险标签，还是同时做新闻方向分类。

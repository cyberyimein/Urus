# Stage 4B AI 决策设计

Urus Agent 的完整开发范围、工具契约、持久化和验收标准见
[Urus Agent 详细设计与开发需求](urus-agent-design-requirements.md)。

股票到期权的完整协调、三份报告契约和三个前端 Tab 的后续设计见
[Stage 4B AI 工作流与报告前端设计](stage4b-ai-workflow-ui-design.md)。

研究报告作为主要前端、开发控制台的重新定位、canonical 路由、图表化技术报告和 AI 复盘中
可见决策依据／provider reasoning-like 内容的展示规则，见
[Stage 4B 研究报告前端重构方案](stage4b-report-frontend-redesign.md)。

## 当前落地范围

Stage 4B 先建立可复现的决策输入和两种分析能力，不直接连接交易执行：

1. `stage4b_strategy_pair.v1` 保存盘前与盘前收盘的完整原始快照。
2. `build_stage4b_decision_packet.py` 将原始文件压缩为 `stage4b_decision_packet.v1`。
3. `urus-equity-decision` 对市场环境和自选股进行排序。
4. `urus-options-decision` 解释 DEX/GEX、Gamma Flip、墙、Max Pain 和 Expected Move，并输出有限风险期权结构模板。

原始配对文件仍是事实备份。决策包只服务模型上下文，不替代原始数据。

## 决策包

在 `backend` 目录执行：

```bash
.venv/bin/python scripts/build_stage4b_decision_packet.py \
  --input data/strategy_research/stage4b-premarket-preclose-2026-08-03.json
```

默认在输入文件旁生成 `stage4b-premarket-preclose-2026-08-03-decision-packet.json`。输出包含来源哈希、两个观测、成对变化、事件、质量警告和执行限制。

实际模型调用使用更小的任务投影：

```bash
# 全自选股排序，不携带完整期权数据
.venv/bin/python scripts/build_stage4b_decision_packet.py \
  --input data/strategy_research/stage4b-premarket-preclose-2026-08-03.json \
  --mode equity

# 单独分析 QQQ 期权；其他 symbol 同样逐个生成
.venv/bin/python scripts/build_stage4b_decision_packet.py \
  --input data/strategy_research/stage4b-premarket-preclose-2026-08-03.json \
  --mode options --symbols QQQ

# 不联网的严格 JSON 冒烟测试
.venv/bin/python scripts/run_urus_agent.py \
  --input data/strategy_research/stage4b-premarket-preclose-2026-08-03-decision-packet-equity.json \
  --provider fake
```

为控制 token 和防止模型把噪声当作证据，输出不包含原始历史 K 线、每个期权的逐行权价曝光数组、Spot Gamma Profile 的全部采样点，以及工作流步骤中重复保存的 payload。墙、Gamma 区间、行权价 GEX 变号、Spot Gamma Flip、Max Pain、Expected Move 和 DEX/GEX 汇总会保留。

## Skill 与期权数据的职责

`urus-equity-decision` 参考 SEPA 的趋势、相对强度、市场环境和风险收益思想，但不会伪装成完整 SEPA。当前 Urus 缺少 MA150 和基本面增长字段，所以严格 SEPA 完整度必须标为 `partial` 或 `not_evaluable`。

主工作流不再调用独立的 `urus-options-decision`。程序从冻结期权链提取 DEX/GEX、Gamma Flip、
Call/Put 墙、Max Pain、Expected Move 和最近正 DTE，生成每个标的的
`equity_option_context`。它只用于股票入场时机、目标区与波动风险过滤；不生成价差、蝶式、铁鹰、
日历或合约腿。Max Pain 仅作描述，期权数据缺失也不能单独成为看空股票的理由。若未来能识别真实
组合订单流，可再把集中蝶式行权价作为目标区证据；模型自行建议的蝶式中心不是独立市场证据。

## 模型供应商边界

决策数据、Skill 和输出 Schema 不绑定供应商。后续协调器应只依赖类似以下接口：

```text
decide(system_instructions, decision_packet, response_schema) -> decision_json
```

建议的职责划分：

- OpenRouter：处理已经采集和整理好的 Stage 4B 决策包。它适合反复回放历史数据和模型对比。
- Anomalo：继续处理需要联网搜索的事件日历、事件结果和缺失事实调查。

在供应商二选一之前，使用同一份冻结决策包对两边做离线 A/B 冒烟测试，比较 JSON 合规率、字段引用准确率、延迟、成本和重复运行稳定性。不要让决策模型自行联网补齐缺失值；缺失数据应回到采集或调查工作流。

当前 Urus Agent 已实现 OpenRouter 适配、Fake Provider、只读数据工具、数学工具、SQLite
审计表，以及大盘 → 并发题材 → 携带确定性期权入场上下文的股票综合 Decision Session。前端报告提供技术整理、
AI 决策和逐节点复盘三个 Tab。默认 `URUS_AGENT_ENABLED=false`，因此未启用 Key 时仍保留决策占位，
不会改变既有采集工作流行为。启用真实调用前设置：

```text
URUS_AGENT_ENABLED=true
OPENROUTER_API_KEY=...
URUS_AGENT_MODEL=deepseek/deepseek-v4-flash-0731
```

AI 决策运行和工具调用可通过只读接口查看：

```text
GET /api/ai/decisions
GET /api/ai/decisions/{decision_id}

# Stage 4B 报告
GET /api/runs/{run_id}/research-reports
GET /api/research-reports/{report_id}
GET /api/research-reports/{report_id}/technical
GET /api/research-reports/{report_id}/decision
GET /api/research-reports/{report_id}/trace
GET /api/research-reports/{report_id}/trace/nodes/{node_id}/raw-response
```

## 每日三阶段采集、两阶段 Agent 工作流

> 2026-08-13 决策更新：目标架构改为盘前和收盘后两次 AI；尾盘仅采集并冻结数据。当前代码及
> 部分旧冻结报告仍包含尾盘 Agent，迁移与兼容要求见
> [CTA 分支 AI 决策与 IV/HV 需求](cta-ai-decision-requirements.md)。

在线采集仍以美东交易日为边界保留三个 run type；只有两个阶段运行 Agent。Profile 存放在
`backend/app/urus_agent/prompts/daily_agents.yaml`，身份、目标、时间范围和阶段约束不再硬编码在
Python 中：

| run type | Agent 策略 | 当前数据 | 继承数据 | 产物 |
| --- | --- | --- | --- | --- |
| `pre_market` | `urus-premarket-strategist` | 开盘前一小时快照 | 最近一个交易日的收盘复盘 | 当日常规交易时段预测 |
| `pre_close` | `collection_only`，不调用 AI | 收盘前一小时快照 | 当日盘前快照 | 冻结 Observation、技术报告和质量审计 |
| `post_close_review` | `urus-postclose-reviewer` | 收盘后完整快照 | 当日盘前预测和尾盘冻结数据 | 当日总结、盘前预测评分和次日基线 |

每次执行均生成独立的冻结 `stage4b_decision_packet.v1`，其中新增 `decision_context`、可用的
一至三个 `observations`、`prior_reports`、`stage_changes` 和明确的缺失血缘。缺少前一阶段时允许
低置信度降级运行，但不能伪造上一阶段结果。`ai_decision_sessions` 使用 `decision_phase`、
`trading_date` 和 `parent_session_id` 保存链路；同日查询额外受当前 `cutoff_time` 限制，避免回放时
读取未来生成的报告。

股票输出升级为 `urus.equity_decision.v3`：盘前除大盘 `forecast` 外，每个标的必须明确
给出方向、概率、预期收益区间和相对强弱，并分别回答两个互不替代的问题：当前为空仓时是买入、
等待还是避开；已经持有时是加仓、持有、止盈、减仓、止损还是退出。确定性期权上下文会参与
`if_cash` 的入场过滤和目标区判断，但不会产生独立期权交易建议。收盘复盘不再让模型给
自己的预测打分；模型只解释偏差，程序使用冻结的阶段价格计算实际收益、实际方向、方向命中、
收益区间命中、相对基准收益和 Brier 校准分。每日周期报告因此升级为
`urus.ai_decision_report.v5`。

客观复盘使用程序评分：盘前预测以 `premarket_price` 为起点，以官方收盘 `regular_price` 为终点。
程序同时校验 `market.session`；若 `post_close_review` 实际仍是 `premarket`，该阶段直接标为
`unscorable`，不能计为横盘、miss 或纳入命中率。尾盘快照只作为路径和结构变化的复盘证据，
不再存在需要评分的尾盘预测。旧报告中的 `pre_close_evaluation` 只做可空兼容。

Theme 节点只输出本题材标的判断，顶层 `forecast` 与 `review` 固定为 `null`，防止五个题材重复
生成整份大盘报告。期权到期日由程序从冻结数据中选择最近正 DTE，压缩为股票综合节点的上下文，
不再创建 Options Agent 节点。Urus 默认不发送 `max_tokens`，不主动截断大型 Synthesis
响应；只有显式设置 `URUS_AGENT_MAX_COMPLETION_TOKENS`（或设为正整数）时才限制 completion。
工具结果仍保留独立的字节保护，避免工具把无界数据灌入上下文。总超时仍保持 1,200 秒，供复杂
全流程调用使用。收盘复盘阶段的期权数据只作为当天市场结构的复盘证据。

分阶段业务提示词已升级为 `urus.agent_task_prompts.v2`，分别约束 Market、Theme 和 Synthesis
的分析维度、标的范围、证据引用、冲突处理，并禁止 Synthesis 生成期权交易结构。下一步验证顺序是：

1. 用一份已配对的冻结数据执行单次 OpenRouter 联合测试；
2. 检查每个阶段的 JSON 合规、scope 完整性、证据路径和 Trace；
3. 对同一数据重复三次，记录排名稳定性、工具数、延迟、token 和成本；
4. 稳定后再扩大期权候选数；若要生成可执行收益图，另行采集 bid/ask、multiplier 和腿权利金。

2026-08-04 的 Market 节点真实验证使用 2026-08-03 配对数据和
`deepseek/deepseek-v4-flash-0731`：当前 Stage 4B 的默认模型；真实收盘复盘验证已完成综合节点，
但部分题材节点受任务耗时和空响应影响而标记为 partial。
（两阶段市场、质量、宏观事件以及 SPY/QQQ/SMH/IGV 各自技术快照），使用 25,004 prompt
tokens 和 3,555 completion tokens。严格 JSON、四 ETF scope 覆盖及 canonical evidence path
校验均通过。
模型文本仍可能出现数值比较措辞错误，因此展示层应优先展示程序计算的图表/数值；后续可将均线
位置等关系进一步下沉为确定性派生字段，而不是依赖模型自行做自然语言算术。

同一份冻结配对数据随后完成了三个真实节点的联调：

- 半导体 Theme（SMH/AMD/INTC/NVDA）在 58.0 秒内成功，确定性预取 8 个工具结果，使用
  36,340 prompt tokens 和 3,055 completion tokens；四个标的均有排名、动作、风险和
  canonical evidence path。
- QQQ Options 在 40.1 秒内成功，确定性预取 5 个工具结果，使用 26,982 prompt tokens 和
  3,889 completion tokens；模型选取最近的 7 DTE 到期日并返回 `no_trade`，同时通过了
  DEX/GEX、Gamma Flip、Call Wall、Max Pain 和 Expected Move 的结构字段校验。
- 将上面保存的 Market 与 Theme 结果作为受控元数据传给 Synthesis，在 103.8 秒内成功，未
  额外调用工具，使用 28,089 prompt tokens 和 8,005 completion tokens；四个半导体标的的
  综合排名和证据路径均通过校验。这是单题材、四标的的真实 Synthesis 冒烟测试，不代表完整
  自选股宇宙已经完成全量回放。

为保证后续复现，运行脚本支持 `--save-output` 保存完整 Provider 结果，以及
`--metadata-file` 将已校验的上游阶段结果注入 Synthesis；临时验证输出不写入生产 SQLite。

### 全流程回放结果

2026-08-04 使用准备好的 `stage4b-premarket-preclose-2026-08-03.json` 做了一次真实
`DecisionCoordinator` 回放。原始历史 packet 直接回放时得到 `partial`：半导体题材因旧包缺少
`paired_changes.instruments[NVDA].technical_confirmation` 而被业务校验拒绝，SPY 期权又暴露出
证据路径解析器不能按日期选择 `expirations[2026-08-10]` 的问题。现在完整流程必须先通过
`build_stage4b_decision_packet.py` 重新生成规范 packet；证据路径解析器也已支持 expiration、
strike 和 contract_id 选择器。

使用规范化 packet 的全流程结果为 `succeeded`：19 个标的、1 个 Market、5 个并行 Theme、1 个
19 标的 Synthesis、Candidate Gate 选出 MSFT/AMZN/GOOG 三个期权候选、3 个 Options，以及最终
`urus.ai_decision_report.v2` 组装全部成功。该结果属于三阶段改造前的旧配对回放基线。单次回放耗时约 9 分 21 秒；Market 85.0 秒，题材并发
批次 137.1 秒，Synthesis 253.1 秒，三个期权节点分别 28.8/28.0/28.8 秒；SQLite 中记录 10
个 Agent runs、100 个 Trace nodes 和 55 个工具调用。回放数据库与报告只写入 `/tmp`，没有修改生产
数据库。

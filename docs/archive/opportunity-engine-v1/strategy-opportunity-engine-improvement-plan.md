# 历史归档：v1.0 已被 v2.0 取代，不得用于新实施

# Urus 指标、策略与机会引擎改善方案

状态：Proposed — 等待用户审查，不代表批准实盘或扩大数据采集范围。  
版本：1.0  
日期：2026-09-05  
审查基线HEAD：`786f7b32178530acd9470c295949ae5142589e4d`；实施前需重新核对工作区与当前HEAD。  
配套交付：[实施任务与验收清单](strategy-opportunity-engine-implementation-handoff.md)

## 1. 设计结论与目标

保留 Urus 的版本化 Universe、canonical 日线、冻结 Dataset、来源与质量记录、Observation Run 和远程 Workflow Binding。把策略层从“技术指标加分与投票”改为“有独立身份、明确触发、价格约束、风险约束和结果记录的机会引擎”。

目标不是每天产生建议，而是回答：

1. 当前哪些市场环境适合研究哪类机会？
2. 哪个具体机会刚出现了新增证据？
3. 哪个机会仍处于可接受价格，哪个已经错过或失效？
4. 在成本、风险、相关暴露和占用时间相当时，先研究哪个？
5. 上述筛选是否比简单基线更有效，哪些额外信息没有贡献？

本方案定义的是研究和决策辅助系统，不增加券商下单、真实仓位管理或自动加仓。允许新增人工维护的“分析用暴露快照”和虚拟交易账本；不得把它们伪装为真实账户。

所有新策略初始为 `research_only`。本文件中的数值是可复现实验的初始参数，不是已经验证的最优参数或收益承诺。实施 agent 不得通过反复试参数来把测试改成赚钱。

### 1.1 成功与非目标

工程成功：相同输入与版本可重放；触发/失效与机器条件一致；无未来数据；同一机会不重复计数；未触发、错过和数据缺失也可追踪。

研究成功：相对于事先冻结的基线，在相当风险暴露下，样本外成本后结果改善；改善不完全来自单个标的、少数日期或市场 Beta；新增复杂度有独立增益证据。

非目标：捕获每个最低点、预测所有市场、把多指标一致解释成概率、用 LLM 或 Monte Carlo 创造数据中不存在的信息优势。

### 1.2 优先级

| 顺序 | 交付 | 原因 |
|---|---|---|
| P0 | 修复现有规则与状态语义 | 防止错误确认、错误合成继续影响判断 |
| P1 | 时间契约、机会账本与结果回放 | 先建立能证伪策略的基础 |
| P2 | 两类日级机会、事件风险过滤、研究排序 | 形成最小可评价产品 |
| P3 | 时间外验证、成本模型、增量实验 | 判断是否值得保留 |
| P4 | 候选标的盘中触发 | 比较其是否改善入场及错失成本 |
| P5 | 事件后策略、期权增强、组合排序 | 在基础策略可评价后扩展 |

P0 可以独立交付；P1–P3 是第一版闭环。P4/P5 不应成为基础账本和回放的依赖。

## 2. 当前事实与需要修复的问题

以下根据当前仓库代码审查；没有据此宣称实际胜率或收益。`strategy-discussion.md` 的旧目标不能视为已实现能力。

| ID | 当前问题与证据位置 | 必须达到的行为 |
|---|---|---|
| F01 | `strategies.py::BreakoutVolumeStrategy` 前20日高点包含昨天，失败突破却要求昨天收盘高于该高点 | 以昨天突破前的窗口判断昨天是否突破，或用机会建立时冻结的突破位；合法 OHLC 必须能够触发失败路径 |
| F02 | 突破初始分数为0时放量默认向多头加分 | 未越过方向性价格条件，量、收盘位置和振幅不能创造方向 |
| F03 | `MeanReversionStrategy` 由分数设 confirmed，与价格确认/失效条件不一致 | 机器条件决定状态；invalidated 优先；确认、失效不能同时成立却输出优先机会 |
| F04 | `_decision_consensus` 两个 bullish/watch 可升级 prioritize | 不得把弱信号数量升级为入场资格；分别汇总环境、触发和风险 |
| F05 | `setup_projection.py::_setup_order` 多头方向排序先于阶段 | 入场队列和风险队列独立；forming 多头不能遮蔽 confirmed 空头风险；invalidated 不进入入场主机会 |
| F06 | `_setup_progress` 多数调用固定 bars_in_stage=1 | 没有历史账本时返回生命周期 unknown/null；有账本后使用真实转换记录 |
| F07 | 所有策略共用5日 horizon | 各策略有独立等待期限、持有期限、事件退出与评价期限 |
| F08 | 日线典型价格承接全部日成交量，命名为成交密集支撑；部分放量下跌命名 absorption | 标记为日线价格密度代理；“未符合派发”不能加分为已确认承接 |
| F09 | `group_phase.py::_impulse` 使用滚动收益日差与零阈值投票 | 区分当日新增收益与窗口退出效应，设置独立变化指标；旧排名仅保留研究描述用途 |
| F10 | `StrategyAdapter.implementation_sha256` 只收集 base/adapter 源码 | 参数、特征实现、辅助函数、时间/执行规则的变化必须改变完整规则包身份 |

P0 具体兼容规则：

- 旧算法保存为 v1 baseline；新增修正版 adapter/version，不原地重写旧 decisions 或 syntheses。
- 失败突破的短期补丁：昨日高点阈值来自 `bars[-22:-2]`，昨日低点同理；昨日收盘越过该阈值且今日收盘返回内侧才判失败。窗口不足不评价；60日前置极值至少需要61根含当前K线。
- 旧均值回归的修正版仅作为实验对照：多头要求昨日 close <= 昨日 lower band、今日 close > 今日 lower band、RSI 今日 > 昨日；触发价是今日 close，失效价为最近5根（含今日）low 最小值减0.25 ATR，须低于触发价；空头镜像。禁止继续用布林中轨同时充当入场确认和回归目标。新主产品以第7节两类策略为准。
- P0 合成最多保留原有可用策略中已经明确成立的行动级别；有双向矛盾返回 watch，不作收益推断。P2 后由机会队列替代动作投票。
- RSI/MACD 等指标仍可展示，不因退出决策核心而删除历史字段。

## 3. 目标领域模型与模块边界

```text
Universe / Observation Group / calendar / provider revisions
                    |
       Observation 收市入口 + 既有盘中采集器
                    |
   Daily Dataset + Intraday/Event source manifests
                    |
          Decision Evidence Bundle（冻结）
                    |
       Features -> Market Context -> Setup Detectors
                    |
        Opportunity Evaluator（纯函数）
                    |
     append-only evaluations / transitions / projections
                    |
    Research Queue + Risk Queue + optional AI explanation

Frozen opportunities + future observations（隔离的访问路径）
                    |
       Outcome Labeler / Paper Execution / Experiment
                    |
     out-of-sample evaluation -> research promotion record
```

### 3.1 新术语

- **Decision Evidence Bundle**：同一 knowledge cutoff 下的冻结输入；引用日线 Dataset、可用分钟修订、事件修订与可选期权快照。与现有 Daily Dataset 并存，不重命名或覆盖它。
- **Market Context**：市场/行业趋势、波动与事件风险的条件描述，不输出可直接执行的买卖动作。
- **Opportunity**：一个标的、策略版本、方向和冻结结构锚共同定义的一次机会。跨多个观察组仍是同一个机会。
- **Opportunity Evaluation**：该机会在一个冻结证据包上的评价；包含条件、状态、价格计划、可研究性、数据质量。
- **Opportunity Transition**：状态变更的不可变记录。
- **Research Queue**：供用户先看什么的顺序，未验证时不声称盈利排序。
- **Risk Queue**：失效、事件变化、过度延伸和已有分析暴露风险；不会被多头偏好隐藏。
- **Outcome**：后续价格路径的标签，不假定发生交易。
- **Paper Trade**：使用冻结执行规则模拟出的入场与退出；与机会状态严格分离。
- **Experiment**：冻结策略、参数、样本范围、切分、成本和评价口径的一次研究运行。

### 3.2 推荐实现位置

| 模块 | 职责 |
|---|---|
| `backend/app/opportunity_engine/contracts.py` | Pydantic 契约与枚举、纯输入输出类型 |
| `features.py` / `context.py` | 有版本的特征、背景，不访问网络或未来标签 |
| `strategies/breakout.py` / `pullback.py` | 纯策略检测与条件计算 |
| `lifecycle.py` | 状态机、优先级、冻结锚、到期 |
| `ranking.py` / `risk.py` | 两条队列、分析暴露约束 |
| `evidence.py` | 输入冻结、revision 选择、跨存储只读桥接 |
| `backend/app/research/execution.py` / `outcomes.py` | 模拟成交、未来标签；不得被在线策略模块导入 |
| `research/experiments.py` / `validation.py` | 实验注册、切分、统计、准入结论 |
| `models/opportunity.py` / `repositories/opportunities.py` | 主库中的持久化与幂等事务 |
| `schemas/opportunity.py` / `api/opportunities.py` | API 边界；引用统一契约，不复制定义 |
| `services/opportunities.py` | Observation 到机会引擎的服务编排 |

不要把新领域继续塞进现有1500行 `strategies.py`。旧文件保留兼容 adapter 和 baseline 接口。纯 evaluator 不访问数据库、行情源、当前系统时间或 LLM。

盘中期权已经使用独立 `IntradayOptionBase` 和独立存储。新方案不合并数据库、不把这些表注册到主库 metadata。通过 manifest+revision hash 只读导入冻结数据；跨库引用无外键，以内容校验和明确 unavailable 状态保证完整性。

## 4. 时间、价格与数据契约

### 4.1 三个时间不可混用

每条事实至少记录 `event_time`（行情/事件发生时间）、`received_at`（系统首次可用时间）、`revision_id`。发布型数据还记录 `published_at`；没有可靠发布时间则标 unknown。

事实可进入时间为 T 的在线决策，必须同时满足：

```text
event_time <= market_cutoff
received_at <= knowledge_cutoff
published_at <= knowledge_cutoff（适用时）
bar_end <= market_cutoff 且完整（K线）
```

当一个高影响事件尚未发生，允许纳入其已知日历安排，不能纳入之后发布的 actual 值。延迟采集使用实际 received_at；不能将 scheduled_at 当作已知道行情的时间。

- `market_cutoff` 描述要观察到哪个市场时点；`knowledge_cutoff` 描述当时实际掌握了什么。
- 回放严格模式必须满足双时间约束。后来下载的多年日线只能进入 `historical_reconstruction` 实验，并披露非真实系统可见历史；不能默认为在线 PIT 回放。
- 在 knowledge_cutoff 后到达的修订不得覆盖旧证据；新的研究修订建立新 Bundle/Experiment。
- 盘中桥接必须选 `IntradayUnderlyingMinuteBarRevisionModel` 的当时可见版本，不能从可能已更新的 latest bar 表回放历史。
- 分钟bar采用左闭右开区间，保存 source timestamp 表示开始还是结束；聚合前统一到 `bar_start/bar_end`，完整性按交易日历预期分钟核验。
- 美股使用交易所时段与 America/New_York，不硬编码 UTC 开收盘。15分钟桶从常规开盘锚定；提前收盘剩余桶不足完整周期则不触发。
- 从冻结到分析结束期间出现的新行情不得偷偷进入同一 Bundle。

### 4.2 复权、交易价格与公司行动

- 指标使用同一已声明 adjustment basis 的连续价格；交易模拟使用当时未复权可交易价格。
- 每份 Bundle 保存复权模式、因子版本与映射来源。触发/失效位转换到交易价后再与实时行情比较。
- 不允许将当前 QFQ 日线价直接与历史未复权分钟价混算。
- 缺少可审计映射时，跨公司行动窗口的交易结果为 unavailable；指标可保留 research_only。
- 拆股发生时，对冻结价位和虚拟持仓数量作可审计的单位换算，不把换算解释成移动止损。分红与退市处理进入总收益/现金流账本；缺失时不把最后价格长期前填。

### 4.3 Bundle schema：`urus.decision_evidence_bundle.v1`

| 字段 | 类型/约束 |
|---|---|
| `bundle_id`, `content_sha256` | ID与规范化内容hash |
| `mode` | `observed_pit / historical_reconstruction` |
| `market_cutoff`, `knowledge_cutoff` | 带时区时间，内部UTC |
| `universe_version_id`, `scope`, `group_version_ids` | 冻结研究资格与观察归属 |
| `daily_dataset_ids` | 精确引用，禁止自动替换 latest |
| `source_manifest[]` | source、record/revision IDs、hash、时间范围、availability |
| `calendar_version`, `adjustment_manifest` | 日历和价格映射身份 |
| `feature_bundle_version`, `rule_bundle_hash` | 完整实现与参数身份 |
| `quality` | 各维度状态、缺失原因、覆盖率，不能只用一个总分 |

source manifest 中的期权 OI 有效日期与行情 quote_time 分开。不能因 quote_time 更新就声称 OI 是盘中实时更新。

### 4.4 必需与可选证据

| 功能 | 必需 | 缺失动作 |
|---|---|---|
| 日级背景/策略 | 足够完整日线、基准、价格映射 | affected strategy unavailable |
| 日级纯技术研究回放 | 上述；事件缺失可分层 | research_only + event_coverage_unknown，不宣称事件已过滤 |
| 可执行候选展示 | 上述 + 有效事件覆盖 + 当前计划检查 | blocked/expired，不生成 actionable candidate |
| 盘中触发 | 冻结日级锚、完整盘中bar、延迟状态、交易时段 | 不触发，不用旧值补齐 |
| 期权增强 | 可比时间/期限、bid/ask质量、合约覆盖 | enhancement unavailable，不影响不依赖它的基础策略 |

实时 quote 初始最大年龄60秒、可用于盘中触发的bar接收延迟120秒；超过即不产生当时 actionable 标记。它们是工程SLA配置，不是模型参数。研究回放保留实际延迟；缺失延迟的历史数据使用单独模式，不能假装满足在线SLA。

## 5. 指标与市场背景重构

### 5.1 各特征只承担一种主要职责

| 信息族 | 核心字段 | 使用方式 |
|---|---|---|
| 趋势 | close/MA50、MA50五日斜率、MA200位置 | 策略适用背景；不重复相加为概率 |
| 相对表现 | 对大盘/行业20、60日超额表现 | 背景及研究排序；相对上涨不代表绝对上涨 |
| 风险 | ATR14、实现波动率20、延伸/ATR、gap/ATR | 价格约束、风险距离、成本压力 |
| 参与度 | 前20日量基线、收盘位置、组广度 | 结构确认；不命名真实资金意图 |
| 收缩/扩张 | 5日TR中位数/前20日TR中位数 | 识别整理，不和多个等价波动指标投票 |
| 事件 | 下次财报/宏观时间、已发布事实和修订 | 禁入窗口与独立事件策略 |
| 期权 | 跨式价格、期限匹配IV、skew、OI结构代理 | 独立增量实验 |

第一版保留当前简单平均TR的 ATR14 定义，显式命名 `atr14_sma_tr_v1`，不悄悄改成 Wilder ATR。所有位置用相同 ATR 口径。阈值输入用此前已完成bar；策略中例外会明确标注。

收益使用总收益口径时标明现金流处理；纯价格超额表现不得命名总收益 Alpha。市场残差增强初始采用此前60日OLS：`r_symbol = intercept + beta*r_benchmark + residual`，至少40个共同收益样本；每日预测残差使用当日前一时点估计系数。单独保存 residual，不与原始超额收益同时加分。数据不足返回 null。

基准必须显式冻结：市场基准、行业基准分别设置；行业不可用时仅市场比较，不能填0或换QQQ后仍标行业比较。默认配置可以迁移为用户当前实际基准，不自动扩大采集标的。

### 5.2 背景状态 v1

市场基准至少需要200根完整bar。设 `slope50 = (MA50[D] - MA50[D-5]) / ATR14[D]`：

- `trend_up`：close > MA50 且 slope50 > 0。
- `trend_down`：close < MA50 且 slope50 < 0。
- 否则 `mixed`；数据不足 `unknown`。
- MA200位置作为独立慢背景，不再加一票。
- `volatility_state`：RV20 相对此前252个可用RV20值的分位数；>=90%为 elevated，其余 normal；不足126值为 unknown。第一版只分层报告，不因 elevated 自动减半仓位。
- `event_state` 由第8节定义。

首批两类多头策略要求市场不为 trend_down；unknown 阻止 actionable，允许标为研究线索。`mixed` 与 `trend_up` 的结果分开评价。背景改变不回写历史，不自动触发虚拟持仓平仓，退出规则见第10节。

市场背景与共同流动性门在创建、armed转换、trigger评价及paper entry时用当时可见证据重查；armed锚不随背景更新移动。背景/流动性不再符合时资格blocked，但若纯价格触发已发生，仍记录triggered事实与阻断原因，不将“没交易”误记成“价格没触发”。

### 5.3 组级信息

- 保留 level 与 impulse 分离，但 impulse 改为当日超额收益、近3日累计超额收益、广度3日变化，不以20日滚动收益差充当新增买盘。
- 滚动20日变化另外保存 `window_entry_component` 与 `window_exit_component`；对数收益可精确分解为今日log return减20日前log return，再转换用于说明。
- 初始显示规则：3日超额收益方向和3日广度变化方向同正为 improving、同负为 deteriorating，其他 mixed/stable；任一关键字段缺失为 unknown。噪声阈值先用收益0.25%、广度5个百分点，并单独记录为待验证参数。
- 所有阈值采用原始数值计算，展示舍入不参与比较。
- 小样本保护保留，但“>=4”只代表展示门槛，不代表统计证据充分。
- 计算组重叠 Jaccard、共用成员、成员收益相关性。相似组不能作为多个独立支持票。
- 组名次用于导航，不参与入场资格；允许所有组都没有 actionable opportunity。

## 6. 机会身份、状态机与不变量

### 6.1 身份与去重

`opportunity_key = hash(symbol, family, parameter_hash, benchmark_mapping_hash, direction, trigger_timeframe, origin_bar_or_event_id, anchor_payload_hash, stream_id)`。

- `stream_id` 区分 observed online 与某次历史实验；回测不得推进线上状态。
- scope/group/run ID 不进入经济机会身份；同一标的同一证据、参数与基准下跨组去重，归属通过 association 保存。
- Universe 版本作为资格证据引用；未改变经济定义时不因每日组版本变化重建机会。基准或参数变化必须新身份。
- 一个 `(stream, symbol, family, parameter_hash, benchmark_mapping, direction, timeframe)` 同时最多一个非终态机会。
- watching阶段允许重新计算价格预览与结构条件，但不可延长其3日寿命。origin的预览hash仅用于身份，不是可交易价位；首次armed转换将当时完整plan写入不可变transition，之后所有评价引用这个anchor_transition_id，不再滚动替换锚以追逐价格。终态之后至少等待3个完整日bar，且结构截止bar已变化，才允许同族新机会；事件策略用新 event_id 区分。
- 同一机会不同组的评价只写一份核心结果；组级 projection 可以有多份引用。

### 6.2 状态与资格分离

状态：`watching -> armed -> triggered -> completed`；任意非终态可进入 `invalidated / expired`。允许同一个评估时点记录 watching→armed→triggered 两次转换，但二者都在该数据实际可用后发生。

资格：`eligible / research_only / blocked / unavailable`。数据缺失、事件窗口或缺少实验验证不应伪造价格失效；这些属于资格。

终态为 completed、invalidated、expired，不可复活。数据修订需生成新的 replay/assessment 分支；线上原记录保持不变。

| 状态 | 精确定义 |
|---|---|
| watching | 族级前提满足，但结构条件尚未全部满足 |
| armed | 所有结构条件满足，价格锚已冻结，尚未观测到有效触发 |
| triggered | 已发生本族机器触发条件；不等于有成交或应该买入 |
| invalidated | 结构失效条件先发生；保留原因和时间 |
| expired | 触发前等待超时、事件型机会的原事件取消、或价格已超过允许追入上限 |
| completed | 从首次trigger后到达该族观察期限；不代表盈利 |

`bars_in_state` 根据策略评估周期中已实际处理的完整bar计数，第一次进入为1；`elapsed_sessions` 另由交易日历计算。重复评估同一bar不增加计数，缺失bar不伪造已处理记录。

### 6.3 同一评估的处理顺序

1. 输入质量与时间可见性检查；未知条件保留 unknown，不当成 false。
2. 处理已有终态，不再转换；新信息只能附加评价/告警。
3. 检查结构硬失效（如观察bar low <= 冻结stop）。
4. 检查超期、事件取消、过度延伸。
5. 检查 armed/triggered 条件。
6. 生成资格与两个队列投影；再计算排名。

同一OHLC bar同时越过失效和触发而无更细路径时，采取保守规则：`invalidated` 并标 `path_ambiguous`，不能宣称先触发后成功。标签/模拟成交的歧义另按第10节记录。

triggered 后计划入场资格只在规定有效期内存在；不因状态仍是 triggered 而每日重复推荐。新的可研究时机必须来自新的结构或尚有效的下一执行bar。

触发后的一次入场被取消/未成交时，paper为not_filled、entry资格为blocked/entry_window_elapsed；机会仍保持triggered直到结构失效或观察完成，以保留这次真实触发的后续标签。expired/extended只用于触发前；不能因之后价格上涨而抹去一次已发生的trigger。已取消日历事件解除普通策略风险门，不会把普通技术机会变成expired。

### 6.4 价格不变量

多头：`stop < entry_min <= entry_max`；空头镜像。价格必须有限且>0，ATR>0，所有价位有明确 adjustment basis。

- `entry_max` 是最高可接受价格，不是预测目标。
- `target` 是明确退出策略的价位或 null，不能为凑R/R从不存在的阻力推导。
- `planned_risk = planned_entry - stop`；`R` 以实际模拟fill减初始stop计算，且固定分母不因移动止损重算。
- 跳空损失可超过1R，止损不是损失保证。
- 未校准分数不得填入 `probability` 或 `expected_return`。
- 冻结突破位在突破后保持不变，不随滚动最高价上移；风险警报与机会入口分开。

## 7. 首批策略的可实现规格

首批只研究美股股票/ETF的现金多头；空头输出为风险观察，不模拟裸空、借券或期权替代。不同资产分别报告，不能将ETF和个股样本混合后宣称普适。

共同要求：至少200根有效日线；close>=5美元；此前20日中位成交额>=2000万美元；市场背景不为 trend_down；标的市场基准可用；事件风险按第8节处理。成交额优先原始turnover，缺失用unadjusted close×volume并标proxy。对总收益/复权不一致的数据不得计算美元流动性。

### 7.1 B1：趋势背景下整理突破 `compression_breakout_v1`

研究假设：趋势和相对表现尚好、价格整理且振幅收缩后，越过冻结整理高点可能提供可控风险的延续机会。该假设必须对照“相同突破、不加收缩/量能”的基线。

以候选评价日 D 为例，结构窗口只使用 D 之前的完整bar：

```text
A = ATR14[D-1]
L = max(high[D-20:D])                       # 20根，不含D
S = min(low[D-5:D]) - 0.25*A
compression = median(TR[D-5:D]) / median(TR[D-25:D-5])
volume_ratio_D = volume[D] / mean(volume[D-20:D])
```

watching：标的 close[D-1] > MA50[D-1]、MA50[D-1] > MA50[D-6]、20日市场超额收益[D-1]>0、共同要求满足。

armed：watching 且 compression<=0.80，且 `0.5*A <= L-S <= 2.5*A`。冻结 L、S、A、结构窗身份；不要求已经突破。

日级trigger：`close[D] > L`、`close[D] <= L+0.75*A`、volume_ratio_D>=1.20、当日收盘位置 `(close-low)/(high-low)>=0.60`。当日high=low时收盘位置unknown，不触发。评价只能在D的完整bar实际可用之后；不存在在D收盘价成交的假设。

价格计划：entry_min=L，entry_max=L+0.75A，initial_stop=S。下一完整可执行bar的开盘价必须位于区间内；低于L视为触发未保持，高于上限为错过。不挂长期等待成交的买单。第一版只尝试一次next-bar entry。

生命周期：armed最多等待5个后续交易日；到第5天仍允许先评价该天触发，再于该天结束后到期。若任何待评价bar low<=S则失效；触发前close>entry_max则expired/extended。triggered后观察20个交易日；入场有效期仅下一可执行bar。

虚拟退出：初始硬stop；日close<L产生失败突破退出意图，下一可执行bar开盘执行；最多持有20个交易日，届满bar收盘后产生退出意图。没有固定获利目标。初版不移动stop、不分批止盈。

### 7.2 B2：强势趋势回撤恢复 `trend_pullback_reclaim_v1`

研究假设：趋势未破坏、相对强势仍在时，短期回撤后重新越过回撤结构，可能比持续追涨有更好的入场路径。

结构窗全部止于 D-1：

```text
A = ATR14[D-1]
H = max(high[D-20:D])
P = min(low[D-5:D])
depth = (H-P)/A
L = max(high[D-2:D]) + 0.05*A
S = P - 0.25*A
```

watching：close[D-1]>MA50[D-1]、MA50[D-1]>MA50[D-6]、20日及60日市场超额收益>0。

armed：watching 且1<=depth<=3；最近5日存在一次 `low<=MA20+0.5*A`；最近5根收盘全部>=各自MA50；`0.5*A<=L-S<=2.5*A`。冻结 L、S、A。

日级trigger：close[D]>L、close[D]>MA20[D]、close[D]<=L+0.5A、收盘位置>=0.60。不要求RSI阈值；成交量和RSI仅记录供消融实验。low<=S仍先判失效。

价格计划：entry_min=L、entry_max=L+0.5A、initial_stop=S；下一bar一次入场，规则同B1。

生命周期：armed最多3个后续交易日，末日评价后到期；triggered观察10日。

虚拟退出：硬stop；日close<MA50产生下一bar退出；持有10日时间退出。没有固定目标、不分批、不移动stop。

### 7.3 共同语义与参数管理

- D-1结构检测可在D-1收市后提前准备，随后每次评价复用冻结锚；若D收市后首次历史重建该结构，最早signal known time仍是D数据实际可见时间，不能倒推实际提醒时间。
- 结构条件不满足可保留watching记录，但尚未armed的watching最多3日，随后expired；候选创建时即保存锚来源和候选检测时间，不每天延长等待期。
- 每个族的检测、触发、观察期限、虚拟退出、可执行时间框架一起形成参数版本。
- 正式研究每族先只注册本文件默认参数一套；敏感性实验只对compression/entry extension等一个参数做预先登记的±20%扰动，不全组合搜索。
- 两个族的阶段不能互相升级。一个族出现失效，另一个族可以仍有独立机会，但必须展示解释与重叠风险。

## 8. 事件层：先做风险过滤，再做事件后交易假设

### 8.1 最小事件对象

`event_id, revision_id, symbol_or_market, event_type, scheduled_start/end, actual_release_at, published_at, received_at, source_url, source_content_hash, status, timing_precision, coverage_as_of`。

类型至少为earnings、guidance/company_announcement、FOMC、CPI、employment_release、corporate_action；其他重大事件支持人工录入并标 `manual`。官方宏观日历、公司IR/公告为优先源；不在本方案中假定已有财报或一致预期订阅。适配器能力、许可、覆盖实测后登记，缺源先实现fixture与人工结构化导入。

`confirmed_no_event` 只有覆盖查询成功且窗口明确时成立；空数组不能代表没有事件。未知财报时刻用整交易日覆盖，不猜盘前或盘后。修订/取消是新revision，旧Bundle仍引用旧安排。

首版事件覆盖SLA：财报查询在knowledge_cutoff前24小时内成功，明确覆盖未来2个交易日；宏观日历在本交易日开盘前或其后更新成功，覆盖当前session。已收到的新修订即时取代当前可用revision；旧Bundle不变。SLA不满足标coverage_unknown。SLA可配置，但改变应进入event_policy_hash。

### 8.2 默认过滤政策（实验参数，非已验证风控最优值）

- 个股普通技术策略：未来2个交易日内有财报，或财报发布后还没有一根完整常规日bar，禁止新actionable entry。
- 其他已确认重大公司公告：事件发生后直到下一根完整常规日bar结束，暂停普通技术新entry；事件策略独立处理。
- CPI/就业/FOMC：盘中入口在计划发布前30分钟到发布后30分钟暂停；发布延期且状态不明时保持blocked直到来源确认或当日结束。
- 日级入口不能假装绕过盘中窗口：实际模拟fill时再次检查当时已知事件政策；冲突时取消该次一次性入场，不延迟寻找更好fill。
- 已有虚拟交易：本版不因为事件临近临时改退出规则，只产生风险通知；事件强制退出属于单独执行政策实验，不能在回测中选择性启用。
- 事件coverage unknown：保留技术候选为research_only，不标为已通过过滤。

### 8.3 E1：事件后重新定价（P5 research_only）

首版仅正式财报，不把任意新闻叙述当事件。输入需要可见的财报时间及价格，不需要把LLM情绪分数当事实。

- `E0` 为财报公开后第一根完整常规日bar；盘中发布时当天记partial_event_session，E0为下一完整session。财报后连续3个交易日为候选窗口。
- 以前一完整非事件日ATR为A；E0开盘相对前收盘gap>=1A、E0量/前20日均量>=1.5、收盘位置>=0.70、E0市场超额收益>0，形成armed。
- 冻结 L=E0.high+0.05A，S=E0.low-0.25A；风险距离0.5A至3A；后续完整日close>L且<=L+0.5A触发，low<=S失效。
- 一次next-bar entry，最长10日持有；硬stop、close<E0.close下一bar退出、或时间退出。政策显式豁免本次财报的技术禁入窗口，其余事件照常过滤。
- 事件actual/consensus缺失时不计算surprise；有数据后另注册surprise增强实验。

## 9. 机会排序与分析暴露

### 9.1 排序之前的硬门

数据可用、时间有效、状态/价格符合、事件通过、流动性通过、策略发布资格通过，才可进入actionable队列。技术triggered但模型尚未验证，应进入 `research_triggered` 队列。

入场主机会、形成中观察、风险警报三个投影独立。风险警报包括invalidated、计划过期、事件变化；不与入场机会竞争同一primary槽位。

### 9.2 未校准阶段

不生成合成盈利分数。稳定字典序：

1. 可用性/研究阶段分队列；队列内triggered优先于armed优先于watching。
2. triggered按首次trigger_at倒序，armed按距离entry_min/ATR升序。
3. 同级按当前流动性代理降序，再按symbol、opportunity_id稳定打破平局。

`rank_basis=research_triage_unvalidated`；risk距离、延伸、剩余有效期、成本假设并列显示，不暗示风险距离越短收益越好。不同族的排序不能称为统计最优。

### 9.3 有验证结果之后

对相同可执行政策和信息集估计成本后 `net_R` 的条件分布。样本不足回退到族级研究结果，不输出伪精确的个股胜率。

- `expected_net_R` 直接来自该完整退出政策的样本/模型，不把“先碰+2R的概率”当成趋势持有政策的胜率。
- 若展示 `p_win`，定义必须是该政策的净P&L>0；`E= p_win*mean_win_R + p_loss*mean_loss_R + p_flat*0`，成本已计入则不可重复扣。
- 同族内可按expected_net_R的单侧95%置信下界排序；跨族仍显示占用期、风险和样本差异，不把E[R]/天直接称作可实现年化收益。
- 初版组合优先级使用第9.4节约束后保守排序；复杂效用权重需独立实验，不额外发明一个未经验证的总分。

### 9.4 可选分析暴露

默认没有真实账户连接。用户可导入带as_of的分析暴露快照：symbol、方向、数量/名义金额、主题标签、来源manual/paper；旧快照必须标stale。

- 没有快照：只展示候选间重复暴露，`portfolio_fit=unknown`，不输出真实建议股数。
- paper默认实验参数：每次计划风险0.25% NAV、总初始风险不超过1%、单一相关簇不超过0.5%、总多头名义金额<=100% NAV、单标的名义金额<=20%。这些仅用于研究比较，非用户实盘风险建议。
- 同一symbol多个族paper并存时合并计算symbol和簇风险，禁止重复使用现金；按冻结队列顺序分配。
- 相关簇以此前60日收益相关>=0.8的连通分量形成；缺相关数据时采用已知主题保守归组，完全未知独立标记并限制新增暴露。保存簇版本。
- 数量=floor(风险预算/(fill-stop))，再受现金、名义上限和参与度限制；参与度默认不超过此前20日ADV的0.1%。不得以极小stop规避名义限制。
- 组合阈值敏感性与单策略edge分开评估。

## 10. 结果标签、模拟成交与回测

### 10.1 两条账本

机会标签记录“之后发生什么”；paper交易记录“按固定政策能否成交以及结果”。两者不得混用，triggered不自动生成fill。

每个机会保存所有评价，包括未triggered、因价格跑远取消、事件blocked、数据缺失。按首次trigger产生唯一事件标签；每日持续触发不是新独立样本。

paper研究可以消费research_only的有效trigger，否则尚未晋级的策略将永远无法被评价；它只忽略“研究尚未验证”的发布门，不忽略数据、时间、流动性、事件和价格门。若注册了不使用事件过滤的纯技术消融，必须使用不同policy并标coverage限制，不能隐式绕过主政策。

标签期限：1/2/5/10/20个交易日，记录raw return、相对基准return、MFE、MAE及有效bar覆盖。标签参考价格明确区分signal observation price与paper fill。

- 可另记录固定±1R/±2R first-passage研究标签，但不得与族级持有退出政策的收益混用。
- 未来窗口不足为pending；停牌、退市、缺bar或路径不明为censored/ambiguous，并保留样本数，不能默默删除。
- 未来high/low只从参考时刻之后开始；不能把signal产生前同一bar的极值计入MFE/MAE。
- 同一bar同时达到上下障碍，缺细数据则label ambiguous，另算保守与乐观边界。

### 10.2 时间可执行性

定义 `decision_available_at = max(bundle.knowledge_cutoff, completed_bar_received_at, evaluation_finished_at)`；paper研究可使用预先注册的固定计算延迟代替真实完成时间，并标 simulated_latency。

交易只能发生在 `decision_available_at` 之后。日级政策是下一常规交易日开盘；若数据到达时已错过该开盘，则此次next-bar入口取消，不能偷用已发生的开盘价。盘中政策为下一完整触发周期bar的开盘；同理，错过该开盘不能回填成交。

触发bar收盘及next-bar开盘常为同一市场时间，真实计算/行情延迟通常使该开盘不可成交。因此P4的分钟回放使用“decision_available_at之后第一根完整1分钟bar开盘”作为实际执行点，且不得早于原定next-trigger-bar open；没有1分钟数据时只给理想化bar级对照，标 execution_approximation，不授予线上资格。

### 10.3 成交模型

- 日级baseline：next-session unadjusted open；close-only信号不允许当日close成交。
- 有同步双边报价：多头按ask买、bid卖，加额外滑点；报价年龄、价差和交易时段均必须有效。
- 无报价的日级历史：open + 单边成本代理；费用单独记录。初始实验用每边5/10/20bps三档，不声称代表所有股票实际成本。
- 初始离线bps场景定义为all-in单边成本（spread、滑点、佣金等的合计代理），fee_schedule另记`included_in_bps`而非再扣费用；上述“费用单独记录”指保留分项字段和是否已包含。实际bid/ask场景必须给出独立佣金/规费政策，未知则cost_fidelity=proxy并使用另一个all-in代理场景作对照，不能默认真实费用为零。
- spread代理已包含在bps时不得再次扣spread；有真实bid/ask时bps只表示额外滑点，并使用独立配置身份。
- 多头实际含滑点入场价须在[entry_min,entry_max]内；否则not_filled。fees不影响价格区间但进入净P&L。
- stop：开盘已低于stop时按开盘减卖出滑点；否则当bar low<=stop按stop减滑点。hard stop优先于收盘生成的退出意图。
- close-based或时间退出：bar完整可用后下一个可执行开盘卖出；不会回填此前收盘成交。
- entry日也可能触发stop。只有日OHLC且需要判断先后时采用保守次序并标approximation；保守/乐观结果差异必须报告。
- 借券、融资、期权行权不在首批多头现金策略范围。paper现金不足不fill，不借负现金制造杠杆。
- 持有期从fill所在session计为第1日，达到N日收盘产生时间退出意图，第N+1可执行开盘退出；Outcome horizon固定截止不受paper入场与退出变动影响。

### 10.4 历史来源与样本资格

保存当时Universe版本；现代watchlist向历史回填时标selection_bias_uncontrolled。不得以当前幸存标的历史证明历史选股能力。

上市/退市、公司行动、日历、可见修订、停牌、成交量均需要覆盖报告。拿不到完整历史成员时仍可研究“固定当前研究池的条件表现”，但结论范围必须收缩。

不因建设研究回放自动扩大Moomoo额度或下载全市场历史。先用现有合法数据和mock/replay实现；需要新数据的范围由后续独立任务明确授权。

## 11. 实验、统计评价与发布资格

### 11.1 每次实验先冻结

`experiment_id, preregistered_at, hypothesis, strategy/rule/parameter hashes, universe_manifest, source_manifest, date_range, train/validation/test boundaries, primary_metric, baselines, execution_policy, cost scenarios, exclusions, random_seed, max_trials`。

Experiment状态：registered/running/completed/failed；完成结果不可原地替换。调参、更换数据、改变成本、排除样本均创建新experiment并链接parent。失败实验也保留。

最低比较组：

1. B0a：相同Universe与成本模型的简单20日突破，固定20日持有及明确stop政策。
2. B0b：简单20日相对强弱排序，固定周频再平衡；与候选策略匹配名义和风险预算。
3. B0c：相同分析期间的基准持有，以及按此前数据缩放风险的基准；现金收益口径相同。
4. 现有v1与P0修正版：观察语义修复的影响，不能保留原bug制造一个容易击败的唯一基线。
5. B1/B2消融：分别移除收缩、相对强弱、量能/事件过滤，每次只改一个信息族。

B0a stop初始固定在信号前20日最低价减0.25ATR；与B1不同stop构造会混入执行影响，额外提供“B1同stop、只去收缩”的消融，不能把全部差异归因于收缩。B0b每周首个交易日开盘按上周最后完整日的20日超额收益选前20%（至少1只），只选超额>0，等风险分配并受同样现金约束。

### 11.2 时间切分

- 有足够历史时初始协议：24个月训练、6个月验证、3个月测试，按3个月滚动；所有边界预先写入manifest。
- 最长标签/持有退出跨度包含20日持有后的退出bar、等待期与实际延迟；采用精确样本区间重叠剔除，而非只硬编码20天。
- 任何训练标签尚未结束于验证/测试开始之前，则purge；允许使用测试前的已知历史特征作为warmup，但禁止读测试标签。
- 分割和bootstrap按时间同步覆盖所有symbol，不能对个股或同日15分钟截面独立随机切分。
- 保留最终至少6个月未用于选参的holdout。历史不足不能压缩到几天后仍称walk-forward validated；只运行engineering/shadow。
- 连续线上shadow不参与重新选择已注册参数；新版本重新开始独立版本计分。

### 11.3 指标

主要指标：在冻结paper组合风险/现金约束下的成本后日收益相对主基线的均值增量。配对日期，报告95%置信区间。

必要辅助指标：净期望R、胜率与盈亏幅度、收益中位数/尾部、最大回撤、时间在场、换手、未成交率、gap损失、MFE/MAE、资金占用、各市场背景和资产类别分层、候选集中度。

有概率模型后增加Brier/log loss和reliability，区间预测增加覆盖率与宽度；没有概率输出不要强行计算Brier。

对“抓住机会”的评价：比较固定风险预算下前K候选实际结果、可成交率、首次提示到触发时间、延伸取消率。可以事后定义“未来先达到+2ATR且未先达到-1ATR”的机会标签来衡量漏报，但该标签不是天然真值，也不能回流到候选构建。

### 11.4 置信与发布门槛

验证门槛分开：

- **Engineering ready**：通过时点/状态/成交/重放验收；不要求盈利。
- **Research measurable**：数据覆盖与实验manifest完整，结果可重放；不要求盈利。
- **Evidence supported**：至少100个非重复triggered机会、至少250个OOS交易日、至少40日线上shadow，且主要预注册指标的单侧95%置信下界>0；这些数量是内部保守最低门槛，不代表足够独立样本或盈利保证。
- 置信区间采用同步时间块bootstrap，初始block=20交易日，注册10/40日敏感性；样本不足或区间不稳定则不晋级。固定seed、5000次重采样，输出完整方法和实际有效日期数。
- 三档成本全部报告；主成本档通过且高成本压力档点估计不为负；不能以最低成本档为唯一依据。
- 删除表现最好的5个交易日后，主要增量点估计仍>0；若某一symbol贡献>30%正利润，必须进行剔除该symbol敏感性并缩小结论范围。
- 在实际覆盖的各背景分别报告；未覆盖的背景不授予资格。不能把日历250天视为已经覆盖所有市场状态。
- 多族/多参数检验：登记所有试验；对同一发布批次的主要假设用Holm校正，或预先只指定一个主假设，其余标exploratory。不能反复使用同一holdout直到通过。
- paper最大回撤默认门槛10% NAV，仅对应第9.4节paper预算；超限不授予当前配置资格，修改预算需要新实验。用户真实风险偏好不由此推断。

Evidence supported只允许默认展示“有历史证据支持的研究候选”；从不自动授权实盘。该状态由版本化assessment显式生成，不因测试通过、文件生成或agent宣布完成而设置。

关于多次试验带来的回测过拟合风险，参见[Bailey等原始论文](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。本方案的具体门槛为项目治理选择，不是论文给出的通用安全阈值。

### 11.5 运行后退化

连续记录质量失败率、重复事件率、未成交率、成本偏离和分背景表现。数据/契约错误立即暂停受影响版本actionable投影；性能波动先报告不确定性，不因连续几笔亏损自动重调模型。每月生成一次版本评估，只有预注册的评估窗口成熟后才更新资格；不创建本文件之外的自动通知或定时任务。

## 12. 盘中触发与期权增强

### 12.1 P4盘中变体

保留日级背景与冻结锚，先对同一候选集比较daily-close与15分钟close变体；30/60分钟只在另一个预注册实验中比较，不同时挑最优。

- B1/B2锚来自最近一次在盘中之前已冻结的日级结构。
- 15分钟trigger：已完成bar close越过L且在entry上限内、收盘位置>=0.60。
- B1量能用同一session时刻bucket此前20个交易日成交量均值，至少10个有效历史同bucket值，ratio>=1.20；不得把上午累计量除以整天均量。B2不增加量能门。
- B2的MA20使用冻结日级MA20，不能用未完成当日日K替换；若想使用盘中动态均线则是新策略变体。
- 暂停窗口、接收延迟、缺分钟bar和价格复权按第4/8/10节；一次next-bar入场政策保持一致。
- 日级与盘中变体使用独立opportunity stream，不同时在同一paper组合对同一机会重复加仓。
- 评价新增成本、提前量、假突破、错过率和总体净收益；不能只展示盘中买价比收盘更低的成功案例。

采集只覆盖已授权Universe内的armed候选、必要基准和分析已有暴露。以既有coordinator共享额度与限频；缓存已有分钟修订，避免股票候选与期权采集重复请求同一行情。收市Observation仍是日级运行入口；盘中为其冻结计划的子评价，保存parent daily bundle与采集run身份，不冒充完整收市运行。

现有盘中期权结果使用scheduled_at选未来窗口的行为需单独审计：若实际spot晚于scheduled_at，新的Outcome必须从实际可见/执行时间之后选bar。旧标签不覆盖，输出新label_version并并列展示差异。

### 12.2 P5期权增强的资格

- `ATM straddle midpoint` 显式命名，不自动转为68%区间；统计校准后才能有覆盖概率。
- GEX/DEX保存公式、符号假设、OI有效日、期限覆盖、Greeks来源。`modeled`字段不可改名成真实dealer inventory。
- IV/HV严格匹配期限、年化口径；综合IV代理不输出“便宜/昂贵所以买卖波动率”。
- Gamma Profile使用实际到期时间、到期剩余秒数、报价时刻；到期后不继续用1小时floor计算可交易0DTE结构。研究模型需明确美式/欧式近似与股息假设。
- quote<=0、crossed market、单边报价、过期quote或相对spread>10%的合约，不进入ATM跨式执行代理；记录被过滤比例。10%为初始质量参数，需按资产审查，非流动性保证。
- 先比较在价格、波动、事件已知后，加入期权字段是否提高同期限预测/交易结果；不把能解释当天收盘当成预测成功。
- Wall邻近研究在预测时冻结节点，按距离/ATR、DTE、价位密度分层；对照普通round-number节点与同等数量/距离的置换节点。不能在看到收盘后挑最近到期日/节点。
- 期权特征初期只分层记录，不改变B1/B2入场。通过消融研究后另发新参数版本。

Cboe关于净头寸与总量区别的[原始研究](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/)仅支持保留仓位假设边界，不证明任何Urus策略有效。Moomoo字段/权限实现以[官方快照接口](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)及实际部署SDK能力为准，不根据文档存在就自动开通采集。

## 13. AI的输入与权限

AI可以解释背景、总结事件原文、对比候选、指出缺失与反证。不得修改bar、机会状态、风险约束、执行计划、历史标签或统计资格。

新增冻结输入包含：Bundle身份、机会当前评价、转换历史摘要、资格/阻断原因、研究支持范围、可选分析暴露。事件引用保留原文证据；晚到AI结果不注入旧Bundle。

现有v3 workflow仍使用既有schema；新增 `opportunity_review` 合约需新输入schema、新binding、新published workflow版本。不偷偷向严格旧schema添加字段，不替换远端preset内容而保留旧hash。

远端preset实际提示词不在本地完整可见时，实施报告标external dependency。可以完成本地schema、fixture、预检和拒绝测试；不得猜测远端已经支持并发布。

LLM输出的行动排序与确定性队列进行单独shadow比较。未验证前只是附注，不作为状态机或交易模拟的输入。

## 14. 持久化与API

### 14.1 新表（主库，使用Alembic）

| 表 | 关键内容与约束 |
|---|---|
| `decision_evidence_bundles` | 不可变payload与hash；内容hash唯一（包含mode/身份语义） |
| `opportunities` | opportunity_key唯一；origin、初始预览hash、family/version/stream，不可变；可交易冻结锚引用首次armed transition |
| `opportunity_evaluations` | unique(opportunity_id,bundle_id,rule_bundle_hash)，conditions与结果不可变 |
| `opportunity_transitions` | unique(opportunity_id,sequence)，evaluation_id/from/to/reason |
| `opportunity_heads` | 当前状态、last_sequence、last_event_time，允许CAS更新，仅作可重建投影 |
| `opportunity_scope_links` | unique(opportunity_id,scope identity)，跨组关联 |
| `event_revisions` | unique(event_id,revision_hash)，发布时间与系统可见时间 |
| `outcome_labels` | unique(opportunity_id,reference_kind,horizon,label_version,source_manifest_hash)，append-only |
| `paper_trade_events` | experiment/policy、机会、fill/exit事件；unique(logical_event_key) |
| `research_experiments` | 注册manifest与状态；manifest内容不可变 |
| `research_assessments` | experiment/result/统计/资格版本，append-only |
| `analysis_exposure_snapshots` | 可选manual/paper快照与hash |

`opportunity_evaluations` 契约：

```text
schema_version, evaluation_id, opportunity_id, bundle_id, rule_bundle_hash
state_before, state_after, evaluated_bar_end, decision_available_at
conditions[]: {code, result: pass|fail|unknown, observed, operator,
               threshold, evidence_refs[], feature_version}
eligibility: {status, blocking_reasons[], research_assessment_id?}
plan: {direction, entry_min, entry_max, initial_stop, target?, price_basis,
       valid_until, entry_attempt_policy, holding_sessions, exit_policy_id}
context, quality, feature_values, transition_ids[], content_sha256
```

所有schema `extra=forbid`，reject NaN/Inf；金额/价格计算内部用Decimal或稳定定点单位，JSON按明确精度序列化。hash不包含随机ID与写入时间，但包含所有影响结果的输入、版本、参数和状态前序hash。完整bundle实现hash来自受控模块列表与参数manifest，不只依赖adapter源码反射。

### 14.2 事务与乱序

- 一次evaluate事务：读取head/version→纯计算→写evaluation/transitions→CAS更新head；冲突重读重算，不重复转换。
- 相同幂等键重试返回相同业务内容，不额外增加sequence；异步worker至少一次投递也必须安全。
- 比head更早的event_time只允许写历史评价分支，不回滚线上head。相同时点不同revision通过新replay分支处理，不静默改历史。
- append-only事实禁止通过API删除/修改；数据库修复需单独维护流程。本方案不授权破坏性迁移、清库或重置。
- 冻结跨库读不是分布式事务：先完成source manifest、验证所有payload hash，再一次提交主库Bundle。源读失败不提交半个可用Bundle。

### 14.3 API（建议新增，旧API保持兼容）

- `POST /api/opportunities/evaluations`：{bundle_id, rule_bundle_hash, stream_id}；不接受任意外部scope扩展，不从请求直接抓行情。
- `GET /api/opportunities?as_of=&symbol=&family=&state=&eligibility=&cursor=`：必须返回as_of实际使用值；默认当前在线投影。
- `GET /api/opportunities/{id}` / `/transitions` / `/evaluations` / `/outcomes`。
- `GET /api/opportunity-queues?bundle_id=`：entry_research、actionable、watching、risk，含rank_basis。
- `POST /api/research/experiments`：注册manifest，默认不发起网络采集。
- `POST /api/research/experiments/{id}/runs`：异步启动已注册本地数据研究；返回run ID。
- `GET /api/research/experiments/{id}` / `/results`。
- `POST /api/events/import`：结构化manual/已采集source导入，保留来源；不能覆盖同ID旧revision。
- `POST /api/analysis-exposure-snapshots`：人工分析快照，与真实账户明确区分。

HTTP冲突409（幂等键内容冲突/状态并发），契约422，引用缺失404；行情不足是业务unavailable，不能伪装500，也不能返回全零成功。

鉴权、访问范围、错误封装沿用仓库既有边界；研究运行有并发/CPU/内存上限，不能在FastAPI请求线程里无限回测。首版使用现有可部署进程/CLI+任务记录即可，不新增通用工作流平台。

## 15. 迁移与兼容

1. 记录基线git revision、adapter规则包和fixture；旧历史不变。
2. P0发布修正版策略版本与回归测试，保留legacy baseline可重放。
3. 增量建新表、新schema、新服务；feature flag默认off；旧UI/API继续读原投影。
4. 利用已冻结历史构建独立replay stream；没有当时输入的部分标historical_reconstruction。不能凭现有最后一条状态编造stage_since。
5. 新引擎shadow运行，保存新旧输出差异：资格、状态、首次触发、过期、风险队列。差异符合设计才通过工程切换。
6. 用户审查后切换读取投影；可以先以research_only展示新机会。切换不要求策略已经通过盈利验证，但必须明确其研究资格。
7. 发生问题将读路径切回旧版本；停止新评价任务，保留全部新数据供诊断。无需删表或降级破坏性迁移。
8. 新语义批准并上线后更新CONTEXT.md、当前开发设计与API文档；本提案未批准前不将其写成当前事实。

最小前端变更仅为承载语义：状态、资格、首次触发、价格区间、失效位、到期、数据时间、研究证据范围和风险独立入口。前端不得重新计算策略或把research_only翻译为买入建议。视觉重设计不在此方案中。

## 16. 核心验收场景

| ID | 输入/场景 | 必须结果 |
|---|---|---|
| A01 | 昨天突破历史101，今日回到99，昨日high106/close105 | 修正版识别failed breakout，不因昨日high进入阈值而漏掉 |
| A02 | 价格在区间内、量比2、收盘靠高 | 突破族不得创造bullish trigger |
| A03 | 超卖且分数高，但价格低于机器失效位 | invalidated或blocked，绝不confirmed action |
| A04 | 两个watch，无任何有效trigger | 合成不得升级actionable |
| A05 | bullish forming与bearish confirmed并存 | 风险队列独立可见，入场primary不遮蔽风险 |
| A06 | 同一bundle重复100次/两个worker并发 | 单evaluation、转换不重复、head与重放一致 |
| A07 | 同一标的加入两个重叠组 | 一个经济机会、两份scope link，收益只计一次 |
| A08 | 同一bar先后触发路径未知 | path_ambiguous，保守状态/收益，不挑有利顺序 |
| A09 | armed等待5天，第5天有效trigger | 可trigger；第6天不能复活旧机会 |
| A10 | 价格超过entry上限后回撤 | 旧一次性入口仍过期；不能事后选择回撤fill |
| A11 | 未来修订在cutoff之后收到 | 旧Bundle及重放输出不变 |
| A12 | 计划10:00，实际10:03取得spot | 标签不使用10:00–10:03极值；不得10:00成交 |
| A13 | 完整日信号D收市后产生 | 最早下一可执行开盘；当日close fill为失败 |
| A14 | 次日gap跌穿stop | 损失可超过1R，按gap执行，不截成-1R |
| A15 | 无下一bar/退市/停牌 | pending/censored显式存在，不静默剔除 |
| A16 | 拆股前后混用QFQ与原始分钟价 | 拒绝跨口径计划，或提供可审计映射 |
| A17 | 财报日历查询失败返回空 | event_coverage_unknown，不是no_event |
| A18 | 改辅助函数/退出政策/参数 | rule_bundle_hash变更，旧缓存不复用 |
| A19 | 同日77个期权截面共享一个收盘 | 不能计77个独立日样本，分割与重采样按日同步 |
| A20 | 模拟成本加倍 | 重算结果有独立policy identity，不能修改原实验 |
| A21 | LLM输出与规则相反 | 仅解释附注，不改head、plan、资格 |
| A22 | terminal后到达旧bar | 不回滚线上head，进入历史分支 |
| A23 | 收盘后导入新的历史数据 | historical_reconstruction，不假装当时系统已见 |
| A24 | 市场所有候选不合格 | actionable为空，不能强制选第一名 |
| A25 | 没有分析暴露快照 | portfolio_fit unknown，不生成真实股数 |
| A26 | 财报盘中发布 | E0不是当天混合bar，事件策略不读取发布前当日极值作为纯事件后信息 |

至少A01–A05应有直接调用真实策略的回归测试，而不是重复实现一套算法来证明自己。时间/生命周期/成交用明确价格路径fixture，端到端用离线provider replay；默认测试不访问Moomoo或远端AI。

## 17. 审查决定与实施边界

建议批准的设计选择：保留日级背景；首批现金多头B1/B2；机会生命周期和模拟交易分离；事件先过滤；盘中候选触发后置；期权先作为待检验增强；不以LLM代替统计验证。

可在审查时修改但已给默认值的项：B1/B2初始窗口与价格阈值、paper风险预算、事件禁入窗口、数据SLA、发布最低样本与回撤门槛。修改后提升方案/参数版本，agent不能自行“优化”。

需要实际外部条件才能完成的项：允许的历史数据源与成员覆盖、财报日历来源、盘中历史覆盖、远端workflow发布权限。对应功能可完成契约/fixture/离线实现并保持disabled；不可因等待这些条件而停止已可完成的P0–P3工程工作，也不可声称缺源功能已上线。

本提案自身只新增设计文档；不修改运行策略，不发起采集，不启用调度，不发布远程workflow。用户审查后的实施以配套任务清单为执行顺序。


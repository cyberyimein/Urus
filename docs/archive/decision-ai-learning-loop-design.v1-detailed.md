# 第二种决策 AI：可验证策略闭环设计

> 状态：提案
> 创建分支：`codex/decision-ai-learning-loop`
> 范围：研究与决策辅助，不包含自动下单、自动调仓或自动修改生产规则。

## 1. 结论先行

Urus 现阶段优先做第二种 AI：

> 数据采集 → 确定性策略判断 → 受约束的 AI 决策 → 客观回溯/验证 → 形成可证伪经验 → 离线测试 → 人工批准后生成新 Skill 或策略版本。

第一种“预测今日大盘走向的 AI”保留为后续能力，但不作为当前主线。它需要更宽的宏观、市场广度、事件、资金和跨资产数据，也更容易把叙事能力误认为预测能力。第二种 AI 的目标更窄，也更容易建立可复现的输入、可评分的输出和可回滚的学习过程。

这里的“准确”不能只定义为模型文字看起来合理，而应定义为：

1. 同一份冻结数据和同一版本规则可以重复得到同一组确定性策略信号；
2. AI 只能在策略允许的候选范围内做选择、排序和解释；
3. 每个决定都有明确的预测期限、确认条件、失效条件和 Evidence Reference；
4. 结果由程序使用未来实际数据评分，模型不能替自己改分；
5. 新经验必须经过历史回放或 walk-forward 验证，验证前只能作为候选经验；
6. 没有足够证据时可以明确 `no_action` 或 `insufficient_data`。

## 2. 两种 AI 的边界

| 类型 | 核心问题 | 当前定位 | 主要难点 | 评价方式 |
| --- | --- | --- | --- | --- |
| 大盘预测 AI | “今天大盘更可能涨、跌、横盘还是混合？” | 后续研究能力 | 输入空间大、状态变化快、事件影响强、容易产生貌似合理的解释 | 方向、区间、相对表现、概率校准、跨市场状态稳定性 |
| 策略闭环 AI | “在已经采集并经过策略筛选的证据下，现在是否有可执行的研究动作？” | 当前主线 | 需要先把策略、数据质量和评价方法固定下来 | 策略本身、AI 过滤效果、净结果、回撤、稳定性、可审计性 |

第二种 AI 不是完全放弃预测，而是把预测限制在一个明确的策略上下文中。例如：

- 不是让模型从所有指标自由得出“买入”；
- 而是先由策略判断某个标的是否满足“趋势 + 相对强弱 + 成交量确认”；
- 再让 AI 判断是 `setup_ready`、`watch`、`observe`、`avoid`，并说明还缺哪一个条件；
- 收盘后再由程序确认这个判断在指定期限内是否成立。

## 3. 当前问题的结构化诊断

当前仓库已经具备不少正确的基础：`Decision Dataset`、`Technical Report`、`Decision Session`、`Decision Trace`、`Forecast Evaluation` 和 `Forecast Experience` 都已经出现在领域模型或现有实现中。问题不是简单地“再采集十个指标”，而是这些能力还没有完全收敛成一条以策略为中心的验证闭环。

从当前代码和文档可以确认的主要摩擦如下：

| 摩擦 | 表现 | 后果 | 解决方向 |
| --- | --- | --- | --- |
| 策略和 AI 混在一起 | `urus-equity-decision` 既要理解指标，又要排名，又要给出动作 | 无法知道错在数据、规则、模型还是提示词 | 在 AI 之前增加独立、版本化的确定性策略 Module |
| `score` 和 `confidence` 没有天然含义 | 数值由模型输出，不等于历史校准概率 | 看起来精确，实际不可比较 | 使用 `score_type` 区分启发式分数和校准概率 |
| 候选生成和候选选择没有分开 | 模型可以把弱证据包装成强叙事 | AI 容易“脑补”一个不存在的 edge | 策略先产生候选和资格状态，AI 只能降级、排序或解释 |
| 经验层仍偏向候选缓存 | 当前复盘经验按 `pattern_key` 保存并继承，但盘前主要按最近状态取有限条目，尚未充分按市场状态、主题或反例筛选 | 经验可能变成 prompt 记忆或过拟合 | 经验先进入候选池，再做 point-in-time 回放和人工批准 |
| 质量 Gate 覆盖不完整 | 事件、期权和部分质量状态主要随 packet 传给模型，不能全部依赖模型理解 | 缺数据时仍可能输出看似完整的判断 | 在 Technical Report 后设置阻断性质量 Gate |
| 复盘评价预测质量，不等同于评价动作质量 | 当前评分主要评价方向、区间、相对表现和校准；`if_cash`、`if_held` 的动作结果还没有同等完整的客观收益评价 | 不知道“预测对了”是否转化为“决策有用” | 增加 Strategy Evaluation，并将动作规则固定 |
| 重复运行可审计但不等于稳定 | 已记录模型、Skill、输入 hash 和 Trace，但 provider 重试或采样仍可能带来差异 | 不能把一次输出当成稳定能力 | 对同一冻结输入做重复运行和 A-B，记录差异，不隐藏差异 |

因此，本方案的第一原则是：

> 先证明“某个策略在什么条件下有边”，再让 AI 帮助选择、解释和管理不确定性；不能让 AI 自己定义边，再用自己的文字证明边存在。

## 4. 目标架构

~~~text
Workflow Run
    │
    ├── 采集快照、事件、期权、宏观与质量信息
    ▼
Decision Dataset（冻结、带 cutoff_time）
    │
    ├── Technical Report（确定性事实整理）
    ├── Data Quality Gate
    ▼
Strategy Module（确定性、版本化）
    │
    ├── Strategy Signal：资格、信号、分项、风险、失效条件
    └── Candidate Set：允许 AI 处理的候选
    ▼
Decision Session
    │
    └── Agent Invocation（受约束的 AI）
          ├── 只读取 Strategy Signal 和冻结证据投影
          ├── 只能选择、降级、排序和解释
          └── 生成 AI Decision Report + Decision Trace
    ▼
Forecast Evaluation / Strategy Evaluation
    │
    ├── 程序计算命中、收益、区间、相对表现、Brier、回撤与成本
    └── 不允许模型修改 verdict、score 或实际结果
    ▼
Post-Close Review
    │
    └── 生成可证伪的 Forecast Experience candidate
    ▼
历史回放 / walk-forward / A-B / shadow mode
    │
    ├── 保留原版本
    ├── 人工批准新 Strategy 或 Skill 版本
    └── 失败则拒绝或回滚
~~~

每日节奏继续沿用现有设计：

| 阶段 | 数据动作 | AI 动作 | 产物 |
| --- | --- | --- | --- |
| `pre_market` | 冻结盘前行情、技术指标、事件、期权和质量状态 | 运行一次受约束的策略决策 | 当日研究决策、预测期限、确认/失效条件 |
| `pre_close` | 采集尾盘状态并冻结 | 不调用 AI，保持 collection-only | Observation、Technical Report、复盘证据 |
| `post_close_review` | 采集官方收盘、完整日线和事件结果 | 解释客观评分，提出经验候选 | Forecast Evaluation、复盘报告、候选经验 |

## 5. 五个 Module 的职责边界

| Module | 确定性 Implementation 负责 | AI 负责 | 不允许做的事 |
| --- | --- | --- | --- |
| Data Collection Module | 采集、时间戳、来源、快照、缺失和质量 | 无 | 让模型补齐缺失事实 |
| Technical Report Module | 指标、相对强弱、波动、量价、事件和图表数据 | 无 | 让模型自行重算数字 |
| Strategy Module | 资格 Gate、策略条件、信号、分项分数、风险和候选集 | 无 | 直接调用 LLM 决定策略条件 |
| Decision Module | 组装受约束输入、校验 schema、保存 Trace | 在允许候选内排序、选择、解释、给出观察条件 | 创造新 symbol、新数据或绕过 Gate |
| Evaluation / Learning Module | 实际收益、方向、区间、相对表现、Brier、成本、回撤、经验统计 | 解释偏差、提出可证伪经验候选 | 重新打分、自动改生产 Skill、把一次命中写成规则 |

这里的 AI 更接近“受约束的研究决策器”，而不是一个自由发挥的全能分析师。

## 6. 领域对象与血缘

### 6.1 继续使用的现有对象

- **Workflow Run**：在固定 `cutoff_time` 下的一次数据采集执行。
- **Technical Report**：由确定性程序根据冻结证据生成的事实整理。
- **Decision Dataset**：不可变的证据包，是回放、重跑和评分的共同输入。
- **Decision Session**：基于一个 `Decision Dataset` 的一次完整决策版本。
- **Agent Invocation**：一个具有固定 Task、Skill、证据范围和输出 Schema 的模型调用。
- **AI Decision Report**：只包含通过 Schema、业务规则和 Evidence Reference 校验的 AI 输出。
- **Decision Trace**：证据准备、模型返回、校验、分流和组装的可观察轨迹。
- **Forecast Evaluation**：程序根据实际收盘事实形成的客观评分。
- **Forecast Experience**：由复盘提出、可证伪、带 pattern key 和证据的候选经验。

### 6.2 建议新增或规范化的对象

这些对象先作为设计对象，不要求在本次文档提交中全部落库。

| 对象 | 目的 | 最小字段 |
| --- | --- | --- |
| `Strategy Definition` | 描述一条可回放的策略 | `name`、`version`、`hash`、适用 universe、输入契约、规则、输出契约、状态 |
| `Strategy Signal` | 在某个 cutoff 对某个 symbol 的确定性判断 | `dataset_key`、`symbol`、`as_of`、资格、信号、分项、风险、失效条件、Evidence Reference |
| `Candidate Set` | 明确哪些 symbol 可以进入 AI 决策 | 候选列表、排除原因、策略版本、数据质量状态 |
| `Strategy Evaluation Run` | 记录一组历史回放或 walk-forward | 策略版本、数据范围、评估配置、成本/滑点假设、指标、产物 hash |
| `Skill Version` | 记录 Skill 的版本和晋级状态 | `skill_name`、`skill_hash`、输入/输出 Schema、适用范围、验证报告、批准信息 |

`ai_decision_runs` 已经保存 `skill_name`、`skill_hash`、模型、输入 hash 和输出 Schema 版本。V1 不需要为了“记忆”另造一张宽表；先把策略版本、评估产物和 Skill hash 纳入同一条血缘即可。

一次决定至少要能由以下键重建：

~~~text
dataset_key
strategy_name + strategy_version + strategy_hash
skill_name + skill_hash
provider + model + temperature
decision_session_id
decision_run_id
evaluation_run_id（若已评分）
~~~

同一 `Decision Dataset` 的重跑必须创建新的 `Decision Session`，不能覆盖旧的 AI Decision Report 或 Decision Trace。

## 7. 第一条策略怎么具体化

第一版不要同时做大盘预测、股票选股、期权结构和 CTA 调仓。建议只做一条窄策略：

> 趋势对齐 + 相对强弱领先 + 成交量/价格确认。

它不是已经验证的交易策略，而是一个可检验的研究假设。第一版只使用当前已有或容易稳定保存的字段：

### 7.1 输入

- 冻结的盘前 quote 和上一有效交易日官方收盘；
- SPY、QQQ、SMH、SOXX、IGV 等市场/题材基准；
- MA20、MA50、MA100、MA200 和 252 日位置；
- 20/60/120/252 日收益及相对 QQQ 的表现；
- MACD、Bollinger、ATR、实现波动；
- `technical.rsi_context` 的确定性延续/反转分类；
- 成交量 effort/result；
- 财报、宏观和其他二元事件风险；
- 数据新鲜度、缺失、mock、partial 和冲突状态。

期权 DEX/GEX、Expected Move 和 IV/HV 可以作为后续的入场时机或波动风险过滤，但不进入第一条策略的方向分数。期权复杂度应单独回放，避免把两个未经验证的 edge 混成一个“更聪明”的分数。

### 7.2 策略 Gate

策略每次运行都必须先回答：

1. 当前 symbol 是否属于本次冻结 universe；
2. 价格、历史长度、技术字段和基准是否满足最小质量要求；
3. 市场/题材环境是否允许继续评估；
4. 趋势、相对强弱、确认和风险是否达到策略版本的条件；
5. 哪些条件缺失或冲突，导致 `watch`、`observe` 或 `insufficient_data`；
6. 如果条件成立，什么情况会让它失效。

策略不能只输出一个总分。至少要输出“为什么合格、为什么不合格、还缺什么、何时失效”四类信息。

### 7.3 `Strategy Signal` 草案

~~~json
{
  "schema_version": "urus.strategy_signal.v1",
  "strategy": {
    "name": "trend_relative_strength",
    "version": "0.1.0",
    "hash": "sha256:..."
  },
  "dataset_key": "2026-08-21-pre-market",
  "symbol": "INTC",
  "as_of": "2026-08-21T13:25:00Z",
  "eligibility": "eligible",
  "signal": "watch",
  "score": 0.72,
  "score_type": "heuristic_unvalidated",
  "score_semantics": "rank_only_not_probability",
  "factors": [
    {"name": "trend_alignment", "state": "supportive", "value": 0.80},
    {"name": "relative_strength", "state": "supportive", "value": 0.68},
    {"name": "volume_confirmation", "state": "mixed", "value": 0.45}
  ],
  "risk_flags": ["nearby_event", "incomplete_fundamentals"],
  "confirmation_conditions": ["..."],
  "invalidation_conditions": ["..."],
  "evidence_refs": [
    {
      "dataset_key": "2026-08-21-pre-market",
      "phase": "pre_market",
      "path": "observations.pre_market.instruments[INTC].technical"
    }
  ]
}
~~~

`score` 在没有校准前只是排序分数，不能写成概率，也不能直接映射为仓位。

## 8. AI 决策契约

AI 的输入应是由确定性程序编译出的紧凑投影，而不是一堆原始 JSON。投影至少包含：

- 当前 `Strategy Definition` 的名称、版本和规则摘要；
- `Candidate Set` 中每个候选的 `Strategy Signal`；
- 与候选直接相关的 Technical Report 事实；
- 数据质量 Gate 和缺失血缘；
- 可用且适用的 `Forecast Experience`，数量受控；
- 输出允许值、研究期限和风险边界。

AI 输出建议继续复用现有股票决策结构，并增加以下约束：

| 字段 | 约束 |
| --- | --- |
| `action` | 只能使用 `setup_ready`、`watch`、`observe`、`avoid`、`insufficient_data` 等有限枚举 |
| `if_cash` / `if_held` | 明确区分空仓和已持有情景，不包含仓位大小和下单指令 |
| `confidence` | 同时携带 `confidence_type=uncalibrated` 或 `calibrated` |
| `thesis` | 只能解释已有 Strategy Signal 和 Evidence Reference |
| `confirmation_conditions` | 描述需要出现的可观察条件 |
| `invalidation_conditions` | 描述什么变化会让当前判断失效 |
| `evidence` | 必须能解析回当前冻结 `Decision Dataset` |
| `no_action_reason` | 无 edge、质量阻断、信号冲突时必须说明原因 |

AI 的硬限制：

- 不能新增策略没有提供的 symbol；
- 不能把 `ineligible` 或 `insufficient_data` 升级成买入；
- 不能自行补齐缺失的基本面、事件或行情；
- 不能用自然语言重新计算价格、均线、收益或 Brier；
- 不能把期权结构或资金流标签直接当成方向事实；
- 不能输出自动执行、仓位大小、止损金额或保证收益；
- 不能把模型自己的 `score` 当成已经验证的胜率；
- 当没有足够 edge 时，必须允许 `no_action`。

最重要的分工是：策略可以阻止 AI 进入候选，AI 可以把候选降级或放弃，但 AI 不能凭叙事绕过策略 Gate。

## 9. 回溯、验证和指标

### 9.1 三层评价

| 层级 | 评价什么 | 典型指标 | 负责人 |
| --- | --- | --- | --- |
| Contract Quality | 输出是否合规、可审计、可复现 | JSON 合规率、Evidence Reference 解析率、重复运行差异、缺失处理 | 确定性校验 |
| Forecast Evaluation | 明确预测是否兑现 | 方向命中、区间命中、相对表现、Brier、unscorable 比例 | 确定性评分 |
| Strategy Evaluation | 策略和 AI 组合是否产生稳定研究价值 | 净收益、期望值、胜率、最大回撤、换手、成本/滑点后结果、分市场状态表现 | 回放/评估 Module |

必须区分以下三件事：

1. 模型 JSON 合规，不代表预测正确；
2. 预测方向命中，不代表策略有正期望；
3. 历史收益为正，不代表没有 look-ahead bias、幸存者偏差或成本遗漏。

### 9.2 最小对照实验

每个新 Strategy 或 Skill 版本至少比较：

- `Baseline`：简单基准，例如买入并持有或固定规则；
- `Strategy-only`：只运行确定性策略，不让 AI 改变候选；
- `Strategy + AI`：策略先筛选，AI 再做优先级和动作过滤；
- `Direct AI`：当前自由度较高的 AI 排名，仅作为诊断对照，不作为新生产路径。

如果 `Strategy + AI` 没有比 `Strategy-only` 在成本、回撤、稳定性或研究效率上带来可重复的增益，AI 就没有必要进入该策略的正式路径。

### 9.3 walk-forward 回放

回放的每个 cutoff 都必须遵循：

~~~text
只加载 cutoff_time 之前可见的数据
→ 生成当时版本的 Technical Report
→ 运行固定 Strategy Definition
→ 运行固定 Skill / model 配置
→ 在预先定义的 horizon 结束后加载实际结果
→ 记录 Forecast Evaluation 和 Strategy Evaluation
→ 移动到下一个 cutoff
~~~

禁止：

- 用当前修订后的历史值替代当时可见值而不记录修订；
- 用未来事件结果、未来财报或未来 universe 成员筛选过去；
- 把同一天的多次重复调用当作多条独立样本；
- 只挑选成功运行或存活至今的 symbol；
- 为了提高命中率临时修改 horizon、入场价或失效条件。

样本不足时，系统应返回“无法验证”，而不是给出漂亮的收益率。现有单日 fixture 只能验证 schema、血缘和回放流程，不能证明策略有效。

## 10. 再学习与 Skill 生命周期

### 10.1 经验不是记忆，是假设

`Post-Close Review` 可以提出：

~~~text
pattern_key
statement
applicability_tags
supporting Evidence Reference
counter-evidence
confidence（候选置信度，不是交易概率）
~~~

例如：

> 当 SMH 与 SOXX 同向、标的相对 QQQ 领先且成交量确认不足时，趋势延续候选更容易变成 watch，而不是 setup_ready。

这句话首先只是可证伪假设，不是生产规则。

### 10.2 建议生命周期

~~~text
candidate
  → under_test
  → recurring / validated_candidate
  → human_approved
  → shadow_active
  → active
  → retired / rejected
~~~

现有 `Forecast Experience` 的 `pattern_key`、适用标签、support/contradiction 次数和状态可以承载第一阶段；关键是增加“经过哪一组历史数据验证”和“是否已经影响生产决策”的区分。

### 10.3 Skill 变更规则

V1 不允许模型直接改写 `SKILL.md`、系统提示词或生产阈值。正确流程是：

1. 复盘生成候选经验；
2. Registry 去重、合并 support/contradiction，并保存证据；
3. 离线回放比较旧版本和候选版本；
4. 人工批准生成新的 Skill 或 Strategy Definition 版本；
5. 先以 `shadow_active` 运行，只记录“如果启用会怎样”；
6. 达到晋级 Gate 后再成为 `active`；
7. 指标恶化时回滚到旧 hash。

每个 `Decision Session` 必须锁定当时的 Skill hash。以后修改 Skill 不能回写历史报告。

## 11. 架构深挖机会

按现有架构词汇，最值得加深的是以下 Module。目标是让 Interface 足够小、行为足够深，并把测试面固定在 Seam 上。

### 11.1 Strategy Module

- **当前摩擦**：策略判断分散在 prompt、`coordinator.py` 和报告组装中，删除其中一处后复杂度会重新出现在多个调用方。
- **目标 Interface**：`evaluate(Technical Report, Strategy Definition, cutoff_time) -> Strategy Signal[]`。
- **Adapter**：`trend_relative_strength_v1`、未来的 CTA 或均值回归策略。
- **收益**：策略输入和输出可独立回放；AI 不再承担指标计算和候选生成；策略版本有明确的 Locality。

### 11.2 Forecast Evaluation Module

- **当前摩擦**：客观评分和报告组装都集中在 `reports.py`，容易把解释、事实和评分混在一起。
- **目标 Interface**：`score(frozen_forecast, realized_observation, evaluation_policy) -> Forecast Evaluation`。
- **实现要求**：只读冻结输入；不依赖 provider；不能读取未来生成的报告。
- **收益**：方向、区间、相对表现和 Brier 可独立测试，模型无法影响 verdict。

### 11.3 Experience Registry Module

- **当前摩擦**：`Forecast Experience` 已能保存和继承，但“候选经验”和“已验证规则”的生命周期仍需要更明确。
- **目标 Interface**：`propose`、`record_outcome`、`select_applicable`、`promote`、`retire`。
- **收益**：学习从 prompt 文本变为有状态、有证据、有反例的 Module；可限制每次盘前继承的经验数量。

### 11.4 Decision Projection Module

- **当前摩擦**：同一份原始证据被不同 Agent Invocation 以不同方式压缩，容易出现路径、窗口或 scope 不一致。
- **目标 Interface**：`compile(Decision Dataset, Strategy Signals, applicable Experiences) -> bounded read-only projection`。
- **收益**：输入 token、字段血缘和数据质量规则集中；同一投影可以被不同 provider/model A-B 重放。

### 11.5 Provider Adapter

当前已有 OpenRouter 和 Fake Provider，这是一个真实的 Seam。后续模型对比应保持：

~~~text
decide(system_instructions, decision_projection, response_schema) -> decision_json
~~~

切换 Adapter 不应改变 Strategy Signal、Evaluation 或 Experience 的事实来源。

## 12. 建议的最小持久化改造

不建议一开始把所有回测结果拆成大量表。V1 可以按以下顺序：

1. 在 `Decision Dataset` 或其投影中保存 `strategy_name`、`strategy_version`、`strategy_hash` 和完整 `Strategy Signal`；
2. 在 `Decision Session.policy_json` 保存决策使用的策略版本、Skill hash、评估期限和候选规则；
3. 增加一个 `strategy_evaluation_runs` 记录回放配置、数据窗口、成本/滑点假设和汇总指标；
4. 只有当需要按 symbol、日期、策略因子查询时，再把 `Strategy Signal` 和逐项结果拆成独立表；
5. Skill 版本审批和 shadow 结果稳定后，再考虑 `skill_versions` 表。

`strategy_evaluation_runs` 至少需要：

~~~text
id
strategy_name
strategy_version
strategy_hash
skill_name / skill_hash（可空）
data_start / data_end
cutoff_policy
forecast_horizon
cost_model_version
slippage_model_version
baseline_definition
status
metrics_json
artifact_hash
created_at
~~~

## 13. 实施路线

### Phase 0：固定评价问题

- 明确第一条策略的 edge 假设；
- 固定 universe、cutoff、horizon、入场/退出定义；
- 明确成本、滑点、数据修订和缺失处理；
- 固定 `score`、`confidence`、`unscorable` 的语义。

退出条件：任何人拿到同一个 `Decision Dataset` 都能知道“什么叫预测成立”。

### Phase 1：先做 Strategy-only

- 建立 `Strategy Definition` 和 `Strategy Signal` 契约；
- 把趋势、相对强弱、量价确认和风险 Gate 放到确定性 Module；
- 用冻结数据回放测试策略；
- 暂不让 AI 改变策略结果。

退出条件：同一输入、同一策略版本产生相同信号；每个信号都有证据、排除原因和失效条件。

### Phase 2：接入受约束 AI

- `Pre-Market Composite Decision` 只读取 `Candidate Set`；
- AI 只能做优先级、动作、解释和观察条件；
- 保留 `Strategy-only` 与 `Strategy + AI` 两条结果；
- 仍然禁止交易执行和仓位建议。

退出条件：AI 无法绕过 Gate；所有输出通过 Schema、业务校验和 Evidence Reference 校验。

### Phase 3：历史回放与 A-B

- 建立 `Strategy Evaluation Run`；
- 运行 baseline、Strategy-only、Strategy + AI；
- 采用 walk-forward 而不是随机切分；
- 记录净结果、回撤、换手、成本、稳定性和样本覆盖。

退出条件：至少能明确说明 AI 是提升了结果、降低了风险，还是只提升了报告可读性。

### Phase 4：经验和 Skill shadow

- `Post-Close Review` 输出结构化 experience candidates；
- Registry 记录 support、contradiction 和适用范围；
- 候选经验进入历史验证，不直接进入盘前 prompt；
- 通过验证的新 Skill 先 shadow，不影响正式决策。

退出条件：能够回答“这条经验在哪些时间段成立、在哪些状态失效、是否有反例”。

### Phase 5：有限晋级

- 人工批准新策略或 Skill 版本；
- 记录 promotion decision 和旧版本；
- 逐步扩大 universe 或加入期权/CTA 上下文；
- 任一关键指标恶化时按 hash 回滚。

退出条件：新版本在预先定义的 OOS Gate 下优于旧版本，且没有通过放宽规则或偷看未来得到提升。

## 14. 第一条开发切片

建议从以下最小范围开始：

- **Universe**：`QQQ`、`SMH`、`INTC`，再加少量已稳定采集的核心标的；
- **Horizon**：盘前决定到官方常规收盘；
- **策略**：趋势 + 相对强弱 + 成交量确认；
- **AI**：只处理 `Strategy Signal`，不直接浏览完整原始数据；
- **期权**：第一条策略只使用确定性期权上下文作为可选风险提示，不生成期权结构；
- **数据源**：先使用当前已经有质量记录的 Moomoo/OpenD、Yahoo/FRED 和事件数据；
- **Provider**：先 Fake Provider 和冻结回放，再做 OpenRouter A-B；
- **结果**：只生成研究排序、`if_cash`、`if_held`、确认条件和失效条件；
- **回放**：现有单日 fixture 用于流程验证；有效性结论必须等待足够的历史样本。

这一步的目标不是找出“最强股票”，而是证明整条链路不会把未验证的叙事当成策略 edge。

## 15. Definition of Done

第二种决策 AI 达到第一阶段可用，至少需要满足：

1. 采集、Technical Report、Strategy Signal、AI Decision Report 和 Forecast Evaluation 有完整血缘；
2. 所有输入都受 `cutoff_time` 限制，并记录 source、as-of 和质量状态；
3. 策略版本和 Skill hash 被保存，重跑不会覆盖历史版本；
4. 策略判断可以不调用 AI 单独回放；
5. AI 不能新增候选、补数据或绕过策略 Gate；
6. `score` 与 `confidence` 明确标注是排序分数、启发式分数还是校准概率；
7. 盘后评分完全由确定性程序完成，模型只能解释；
8. `Forecast Experience` 在验证前只能是 candidate，不会自动改生产 Skill；
9. 有 baseline、Strategy-only、Strategy + AI 的对照结果；
10. 数据不足、样本不足或结果冲突时，系统能够输出 `insufficient_data`、`unscorable` 或 `no_action`；
11. 没有自动下单、自动调仓、自动改 prompt 或自动写入生产规则；
12. 前端显示的数值来自已校验的结构化报告，而不是模型自由生成的 Markdown。

## 16. 仍需确认的产品决策

下一步真正需要确认的不是“再加哪些指标”，而是：

1. 第一条策略的 edge 是趋势延续、突破、均值回归，还是 CTA 状态过滤；
2. 研究动作的 horizon 是当日收盘、数日 swing，还是更长周期；
3. `if_cash` 的 `buy` 是否只代表进入研究清单，还是允许进入纸面组合；
4. 成本、滑点、流动性和事件风险采用什么默认假设；
5. 通过多少有效样本和哪些 OOS 指标后，策略/Skill 才能从 shadow 晋级；
6. 谁负责批准、启用和回滚新的 Skill 或 Strategy Definition；
7. 何时把期权 IV/HV、DEX/GEX 或 CTA 上下文纳入第一条策略。

在这些决定明确前，不应继续扩大 AI 的自由度，也不应把更多指标直接塞进 prompt。

## 17. 相关文档

- [Urus Research Context](../CONTEXT.md)
- [Stage 4B AI 决策设计](stage4b-ai-decision.md)
- [CTA 分支 AI 决策与 IV/HV 需求](cta-ai-decision-requirements.md)
- [Stage 4B 研究报告前端重构方案](stage4b-report-frontend-redesign.md)
- [市场数据源与质量策略](market-data-sources.md)
- [Urus Agent 详细设计与开发需求](urus-agent-design-requirements.md)

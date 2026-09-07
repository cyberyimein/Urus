# 历史归档：W00–W13 清单已撤回，由 v2.0 的 R00–R07 取代

# Urus 机会引擎：实施任务与验收清单

状态：Proposed；用户批准主方案后，按任务依赖实施。  
日期：2026-09-05  
规范来源：[完整改善方案](strategy-opportunity-engine-improvement-plan.md)

## 1. 给实施 agent 的统一说明

本清单将主方案拆为可审查任务，未启动任何实施agent。不要将“编译通过”报告为“策略有效”，不要将缺少历史数据等同于可以伪造收益报告。

每次只承担一个已分配任务包。先阅读当前 `AGENTS.md`、`CONTEXT.md`、本任务关联的主方案章节及对应现有代码。代码库可能同时有其他人的修改；不要回退、覆盖或整理不属于任务的改动。共享接口以已合入的契约为准，发现冲突报告并调整自己的实现。

不得实施：实盘下单、扩大Universe/数据订阅、清库、覆盖历史证据、递归删除、hard reset、force push，或未经明确授权发布外部workflow。正常代码变更和测试按用户批准的任务范围执行，破坏性动作仍遵循仓库的显式确认要求。

交付必须包括：具体行为变化、涉及文件、测试与结果、与旧输出的差异、尚未具备的外部条件。不允许仅写“实现完成”。任何偏离主方案的算法/阈值/状态规则先形成差异说明，不擅自改善参数。

## 2. 依赖图与里程碑

```text
W00 基线与契约定位
  |
  +--> W01 现有规则修复
  |
  +--> W02 统一契约与数据表
           |
           +--> W03 时间证据与revision桥接
           +--> W04 状态账本
                  |
          W03 + W04 --> W05 特征/背景/B1/B2
                  |
          W03 -------> W06 事件风险层
                  |
          W05 + W06 --> W07 服务/API/投影
                  |
          W04 + W05 --> W08 标签/执行/实验
                  |
          W07 + W08 --> W09 离线闭环、统计与shadow
                  |
          W09 -------> W10 盘中触发（可选第二阶段）
          W09 -------> W11 事件/期权增强（可选第三阶段）
          W07 -------> W12 AI解释合约（非主链依赖）
                  |
          W09 --> W13 切换、回退与文档
```

可并行任务仅表示依赖许可，不是要求自动启动多个agent。主库Alembic迁移由同一个任务负责人按真实head依次生成，其他任务不得猜revision号；共享contracts/schema由W02统一拥有。W13基础切换不等待W10/W11/W12，也不需要虚假地先宣布盈利。

里程碑：M0=W00/W01；M1=W02–W07研究机会闭环；M2=W08/W09可重放评价；M3=W13研究模式切换；M4=可选增强。Evidence supported只有主方案第11节评估真正满足后才获得，独立于M0–M4。

## 3. W00：冻结实施基线与范围

责任：交付记录，暂不改算法。

1. 记录当前HEAD、工作区状态、实际生效的strategy registry及API调用链；不读取或输出秘密配置。
2. 对照主方案F01–F10，定位测试与调用方；区分已实现能力、旧提案和外部preset。
3. 查明运行的数据库类型、迁移head和现有测试命令；仅记录不敏感摘要。
4. 保存代表性离线fixture：正常趋势、区间放量、失败突破、财报unknown、拆股、迟到数据；合成数据明确标synthetic。
5. 核对旧版本策略是否可直接按历史commit重放；不能访问时明确说明，不复制当前修正算法冒充v1。

验收：一份基线记录、fixture来源清单、实际测试结果；不发起Moomoo/AI请求。

## 4. W01：现有策略语义修复

责任文件：`backend/app/decision_harness/strategies.py`、`setup_projection.py`、相关`contracts.py`及`backend/tests/test_strategies.py`；如果schema改变，先协调W02。

按主方案第2节实现F01–F08的最小修复，不提前实现新机会引擎：

- 修正失败突破前置窗口；60日极值样本>=61。
- 无价格突破不以放量创造方向；方向、触发、风险分开。
- 修正版均值回归明确重返布林带的机器条件与不同的stop，不靠score确认。
- watch不因投票升级；风险队列独立、invalidated不成为entry主机会。
- 缺生命周期历史时返回unknown/null，不伪造bars_in_stage。
- 日线成交密度与量价代理名称诚实；历史字段通过兼容层保留，旧数据不重写。
- 规则版本与实现身份变化；为旧baseline保留可重放路径。

验收：A01–A05、最小样本边界、空头镜像和旧payload读取回归。必须真实调用策略，不在测试里重新计算另一份期望算法。

完成不表示旧策略获得研究资格；默认置信度仍unvalidated。

## 5. W02：领域契约、版本与增量迁移

责任文件：新`opportunity_engine/contracts.py`、`schemas/opportunity.py`、`models/opportunity.py`及主库Alembic迁移。

按主方案第3/6/14节定义Bundle、Condition、Plan、Evaluation、Transition、Experiment、Outcome与发布资格：

- 使用封闭枚举、`extra=forbid`、NaN/Inf拒绝、时间/金额校验。
- opportunity经济身份与scope关联分离；watching origin预览与首次armed冻结plan分离。
- 不可变事实与可重建head分表；头部带CAS版本。
- 增加active_key唯一约束，保证同stream同族同symbol参数/基准/周期只有一个非终态机会；终态在同一CAS事务释放active_key。
- 精确幂等键及hash规范；包括特征辅助模块、参数、执行政策与前序状态hash。
- IntradayOptionBase继续独立，不导入主库metadata。

验收：schema正反例、迁移前后旧数据可读、空库升级、新表唯一约束和事务回滚；不执行生产破坏性downgrade。提供一个合成Evaluation JSON golden fixture，后续任务复用。

## 6. W03：双时间证据与盘中只读桥接

责任文件：新`opportunity_engine/evidence.py`、必要日线repository只读方法、盘中revision只读adapter及相应测试。

- 实现market_cutoff/knowledge_cutoff过滤，完整bar、source revision选择及冻结manifest。
- 严格PIT与historical_reconstruction明确分模式。
- 对分钟latest/revision表明确选择规则，不读晚到最新修订。
- 实现价格basis校验；无复权映射时返回清晰unavailable。
- 先核验所有源payload hash，再提交主库Bundle；缺源不提交貌似完整的数据包。
- 不修改既有盘中采集scheduler、数据库或采集范围。

验收：A11–A13、A16、A23、DST、提前收盘、半根bar、错序时间、双库源缺失；相同manifest生成相同业务hash。

## 7. W04：机会状态机与持久化

责任文件：`opportunity_engine/lifecycle.py`、`repositories/opportunities.py`及生命周期测试。

- 纯函数输入 previous_state + evaluation_input，不读取系统时钟。
- 状态与eligibility正交；失效优先、末日触发边界、一次性入口、终态不复活。
- watching最多3日；armed冻结plan，不能延长有效期或移动突破位。
- append evaluations/transitions，head用CAS；幂等重试和并发重算。
- scope跨组关联不增加经济机会；旧bar进入replay分支，不更新live head。
- bars_in_state按实际处理bar计数，elapsed_sessions按日历计算。

验收：A06–A10、A22，100次重试、双worker冲突、失败事务无半条转换。由ledger全量重建head必须与实时head一致。

## 8. W05：背景、两类策略与组级诊断

责任文件：`opportunity_engine/features.py`、`context.py`、`strategies/breakout.py`、`pullback.py`；组级改动限`group_phase.py`/`group_ranking.py`和相关测试。

- 逐项落实主方案第5/7节公式、前置窗口和参数默认值；禁止自行加入RSI/MACD投票。
- 新族使用独立等待/持有期限与退出政策；方向不等于trigger。
- 每个条件输出pass/fail/unknown与证据，价格anchor来源可追溯。
- 明确ATR SMA口径、美元成交额proxy、基准映射和不足样本。
- 组级变化区分新增与窗口退出；排名与entry资格脱钩。
- 参数读取独立versioned配置；保存本方案默认值，不进行收益选参。

验收：每族至少一个有效路径、一个近似但不触发的路径、所有阈值等号边界、day D不进入前置极值、市场trend_down/unknown、group重叠与全部无机会。A24必须成立。

## 9. W06：事件覆盖与风险过滤

责任文件：新事件schema/adapter服务与`opportunity_engine/risk.py`中事件部分；共享迁移由W02负责人追加。

- 先实现fixture/manual结构化导入、revision与覆盖语义。
- 确认actual/published/received/scheduled不同意义；空响应不代表no_event。
- 财报、重大公告和宏观时间窗口按第8节；日期精度不足保守覆盖整session。
- 事件risk改变entry eligibility，不回写价格状态或虚拟退出规则。
- 新provider仅在明确有访问条件时接入；没有来源保持disabled，不用LLM补数值或日期。

验收：A17、延期/取消、盘中发布、盘后发布、历史revision、未来actual不可见、普通策略与E1豁免边界。没有事件源时仍可完成全部离线验收。

## 10. W07：Observation服务接缝、API与最小投影

责任文件：`services/opportunities.py`、`api/opportunities.py`、router注册、Observation调用接缝；最小前端API/视图映射。

- Observation完成冻结后，在feature flag下创建Bundle并评估；失败有单独状态，不破坏已有日线/期权事实。
- 新POST只消费冻结bundle，不隐式扩scope或抓实时数据。
- entry_research/actionable/watching/risk投影独立；rank_basis明确；未验证默认research_only。
- 查询as_of返回当时版本，不是过滤标题后仍混用当前head。
- 无分析暴露时portfolio_fit unknown；最小人工/paper快照接口可选启用。
- UI消费后台契约，不新增指标计算；风险不得被入场primary遮蔽。

验收：API422/404/409及业务unavailable、旧API响应兼容、flag off路径、A05/A07/A24/A25、冻结read model与数据库一致。

## 11. W08：标签、模拟执行与实验注册

责任文件：`research/outcomes.py`、`execution.py`、`experiments.py`及相应repository；不让online模块import未来标签模块。

- 按第10节实现两条账本、参考时刻、MFE/MAE窗口、censored/ambiguous。
- next-bar一次性入场、gap stop、价差/费用/滑点不重复、cash/名义/参与度约束。
- 实现不含网络采集的实验manifest、CLI或worker任务记录；注册内容不可变。
- B0a/B0b/B0c及旧版/修正版baseline，确保对比不是只打败bug。
- paper组合与单机会分析并存；预注册队列顺序决定资源冲突时的分配。
- 初始成本5/10/20bps是独立场景，actual quote执行是另一政策。

验收：A08/A10/A12–A16/A19/A20；手算的盈利、亏损、超1R gap、未成交、同时stop/target、缺bar结果必须对得上。只使用synthetic/replay，不购买历史数据。

## 12. W09：研究验证与完整shadow闭环

责任文件：`research/validation.py`、运行脚本、实验报告及端到端测试。

- 按第11节实现时间切分、精确样本区间purge、未用holdout和warmup。
- 日期配对主基线、同步block bootstrap、成本压力、集中度与多重检验。
- 每项发布门槛输出pass/fail/unknown和证据；数据不足必须unknown，不因软件实现完成设置Evidence supported。
- 以现有合法历史做一次真实可重放实验（有足够数据时）；没有足够数据则交付运行器和真实coverage报告，不能编造统计。
- shadow报告对照新旧语义、重复提醒减少、候选失效/过期、实验可用样本；不是挑几张成功图。

验收：同seed同输入可重算、改变cutoff不泄露未来、测试集不参与选参、100机会门槛不会按重复评价凑数、所有unknown如实阻止晋级。

## 13. W10：盘中候选触发（第二阶段）

责任文件：盘中证据adapter、候选评估调度接缝、B1/B2的15分钟变体、执行时间处理。

- 仅在W09可评价之后交付；保留每日冻结锚与独立变体stream。
- 从已有授权分钟revision聚合完整bar；同时间bucket量基线，不用整日均量。
- 真实decision_available_at之后的分钟执行点，不能回填已发生next-bar open。
- 缺源、迟到、事件窗口、额度不足只降级受影响候选；不重请求既有数据。
- 新数据范围或运行调度启用需要任务明确授权，本包离线实现不等于授权扩大订阅。

验收：提前收盘、缺一分钟、迟到120秒以上、财报/宏观窗口、跨日冻结、每日与盘中变体在同一paper资金下不重复占用；输出对照实验而不是只比较成功交易入场价。

## 14. W11：事件策略与期权增强（第三阶段）

责任：E1策略、期权质量/期限规范和增量实验；不修改B1/B2默认行为。

- E1按第8.3节实现，保持research_only；actual/consensus缺失不推算surprise。
- 期权按第12.2节处理跨式命名、OI/Greeks时间、真实到期秒数、spread过滤、模型假设。
- 审计既有intraday outcome的scheduled_at/actual-time差异，生成新label_version，不覆盖旧label。
- 期权增益必须有price/volatility/event基础对照及节点对照，不把事后邻近当命中。

验收：A12/A19/A26、期权到期后不可交易、无双边报价、混合期限不可比、真实dealer字段无法从proxy生成；新旧标签差异报告。

## 15. W12：AI解释接缝（可选）

责任：新远程输入输出schema、预检、binding fixture与解释投影；外部preset实际发布另有明确任务才执行。

- 只读冻结机会、反证、资格和暴露摘要，不能修正事实。
- 新contract使用新binding/definition identity，不改变旧v3兼容路径。
- 无法读取外部preset提示词时标unknown；不得报告它已符合新规则。
- 对AI与规则冲突、晚到结果、无效证据引用提供拒绝或附注行为。

验收：A21、旧workflow fixture测试、新schema严格拒绝扩scope、结果不修改head和paper账本；无外部连接也能完成本地验收。

## 16. W13：研究模式切换与最终交付

责任：feature flag、read-model切换、部署说明、CONTEXT.md和当前设计文档。

- 仅在用户批准的实施范围内切换；研究UI可先上线，Evidence supported不得伪造。
- 记录新旧projection差异、数据库版本与回退读路径。
- feature flag回退保留全部事实，不删表、不破坏旧历史。
- 对数据库/数据源/remote workflow分别列部署状态；尚未授权部分保持disabled。
- 文档中清除“已经实现”与“下一阶段”的混淆。

验收：flag双向切换、旧dataset读取、无新源情况下日级原功能正常、全部需要的schema/migration测试通过；最终交付列工程完成度与研究证据等级两个独立结果。

## 17. 每个任务的交付模板

```text
任务：Wxx
基线commit / 规则包身份：
实现范围与行为变化：
涉及文件与公共契约变更：
主方案章节和验收ID：
执行的测试命令及结果：
使用数据：synthetic / replay / observed_pit / historical_reconstruction
是否访问外部源及明确授权依据（未访问写无）：
旧行为兼容与回退方式：
工程状态：complete / partial / blocked
研究资格：unvalidated / research_measurable / evidence_supported
缺少的数据/外部条件与保持disabled的功能：
方案偏离及原因：
```

建议的初始局部回归命令（先核对当前环境）：

```bash
cd backend
.venv/bin/python -m pytest tests/test_strategies.py tests/test_daily_evidence.py tests/test_observation.py tests/test_daily_comparison.py
```

W01/W02等按改动添加新测试；涉及远程契约、期权、迁移时运行相应既有测试。不要因为新文件数量多而重复运行无关测试；有新故障或行为疑问再扩大验证。

## 18. 可直接交给首个实施agent的任务

> 请先完成W00和W01。阅读本清单与主方案第2节，记录当前实现与工作区基线，然后为F01–F08修复建立真实策略回归测试并实施最小修正版。保留旧历史与baseline身份，不实施新机会引擎、不扩大行情访问、不发起远端AI、不调整未授权参数。你不是代码库的唯一修改者，不要回退其他人的更改。以A01–A05及兼容性测试为主要验收，按第17节模板交付。测试通过不代表策略已验证盈利；继续保留unvalidated。遇到实际实现与方案冲突时，先列精确差异和可行处理，不自行发明新的交易逻辑。


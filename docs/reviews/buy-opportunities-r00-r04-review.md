# R00–R04 代码验收与返工要求

> 历史审查记录：针对外部agent修正前的v2工作区。此后用户明确系统无交易与推送，并授权按[主动查看v3设计](../strategy-opportunity-engine-improvement-plan.md)实施。本文的默认响应、订单/组合链完整性要求不再作为产品验收门；事实/时间/来源问题须对最新代码重新验证，不能直接宣称仍未修复。

审查日期：2026-09-05。审查对象：当前工作区；基线 HEAD `786f7b32178530acd9470c295949ae5142589e4d`，新增模块尚未提交。规范为 [主方案 v2](../strategy-opportunity-engine-improvement-plan.md) 与 [R00–R07 清单](../strategy-opportunity-engine-implementation-handoff.md)。本报告不修改交易设计，也不授权实盘或上线。

## 验收结论

**不通过 R00–R04 整体验收。R01 有可保留的基础，R02 未闭合，R03 执行/退出及 R04 评价主链需要返工；R00 有越界改动及遗漏。建议先停止推进 R05。**

目标是用户收到提醒、响应、重新核查后，实际可执行的买入在既定退出/成本/资金政策下是否有净价值。目前代码能产生结构、成交和收益字段，但不能可靠回答这个问题。既不能据此认定 S1 盈利，也不能据此否定 S1：测量链本身存在偏差。

可保留：S1 的手算 L/S/A/U、扫描保留部分拒绝原因、Q0 先确定数量的思路、Q1 超 U 不伪造成交、低于 L 的不利成交仍保留、pending/censored 契约、修正昨日突破阈值及无突破放量方向、两个 bullish watch 不升级 prioritize、阶段年龄置空。无需推倒重写所有代码。

| 阶段 | 判定 | 距离交付的主要差距 |
|---|---|---|
| R00 | 部分实现，不通过 | 均值回归越界改公式；原版/修正版身份、完整规则 hash、独立风险入口未完成 |
| R01 | 基础可复用，不通过 | 时间可见性、复权与原始执行价映射、scan 关联、真实覆盖清单和扫描入口不足 |
| R02 | 未完成 | 经济事件幂等、结构生命周期、交易日历、新结构重建、知识事件重评未闭合 |
| R03 | 需返工 | 成交时间可逆、退出提前、入场日风险遗漏、资格未接入、无共享组合账本 |
| R04 | 需返工 | CLI 只汇总预计算收益；锁定期/延迟/模式隔离、基准、区间和漏报诊断不符合规范 |

## 验证证据

在 `backend` 下执行：

```sh
.venv/bin/python -m pytest tests/test_buy_opportunities.py tests/test_buy_evaluation.py -q
.venv/bin/python -m pytest tests/test_strategies.py tests/test_technical.py -q
.venv/bin/python -m pytest ../docs/reviews/test_r00_r04_review_regressions.py -q --tb=short
```

结果：新增模块原有测试 19 passed；既有策略/技术指标相关测试 28 passed；本次独立验收反例 **7 failed**。不是运行环境错误，失败分别为错误正向评价、重复 trigger、响应前成交、已有持仓仍 eligible、failed coverage 仍 eligible、收盘前退出、scan 外键不匹配。见 [可运行反例](test_r00_r04_review_regressions.py)。该文件断言的是目标行为，故当前预期为红；不应通过降低断言使之变绿。

另通过直接调用复现：成交日 bar 的 low 跌破 stop，只有该日数据时退出模拟返回 pending。仅凭日线无法判断 low 在买入前还是买入后，因此这里不能简单改成“必定 stop”；应补成交后路径，无法确定时明确 ambiguous，不能无条件跳过风险。

范围：静态审查新增 buy_opportunities/research/CLI、两个已有模块修改及相关测试；未运行全仓库测试、未进行真实历史收益实验、未访问账户/发送提醒。当前交付中未找到 R01–R04 所需的完整真实覆盖摘要、冻结实验与净收益决策报告；不能用缺少报告反推真实源一定没有数据。

## 需要修复的发现

以下 P1 均阻断本轮验收；P2 也必须在对应阶段重验前完成。代码位置按本次工作区记录。

### F01 · P1：评价把开发期/快速响应收益说成默认 15 分钟优势

位置：`backend/app/research/buy_evaluation.py:504`，尤其 515–548；`EvaluationConfig:43`。

`build_assessment` 按 data_source 汇总，未按 lock 日期、primary_policy、15 分钟响应及主 variant 选择评价总体。30 条开发期、响应 1 分钟且没有 benchmark 的盈利行，实际输出 `default_15m_net_value=positive_relative_to_baseline`。虽然 applicability 仍是 research_only，决策页的正向归因已经错误。

返工：建立显式 experiment cohort 键：策略/参数、执行/退出/资金/用户政策、source mode、variant、评价区间。主表只接受预注册主模式及锁定期的合格、去重记录；开发期与对照单列。日期区间不重叠，跨期 outcome 按预注册边界处理。绝对净值、对现金、对基准优势分字段表达，缺相应证据即 unknown。禁止以 len(rows)>=30 代替有效成熟样本及独立日期块覆盖。

验收：B12/B21/B28；仅 1 分钟盈利、仅开发期盈利、重复相同机会、未知政策、全部未成熟、缺 benchmark 均不得证明主模式优势。

### F02 · P1：成交入口没有强制因果顺序

位置：`backend/app/research/buy_execution.py:291,400,499,565`。

Q0 未保存其 quote ID/接收时间为 recheck 证据，execute_q1 接受任意 sent_at；复现中 ready=14:15，但 14:00:02 已 filled。只检查发送时未过期，未完整限制成交窗口。M1 不等待首分钟 available_at；D1 不校验事前意图及所属交易日。M1/D1 没有与 Q1 等价的意图状态门。

返工：单个执行协调器强制 `trigger_known <= created <= delivered <= ready <= Q0_known/recheck <= sent < Q1_execution`。Q1 的报价必须满足发送后的事件时间、接收时间、年龄、订单等待窗口及意图截止；不能把发送前生成、稍后收到的旧 quote 当作未来执行证据。订单窗口政策按规范冻结。保存每次观测与取消原因。M1 用首个完整且实际可用的观察完成决策，再取其后可执行 open；D1 验证前一时点已承诺参与，保留低开不利结果。

验收：B08–B13；Q0 延迟到达、ready 前 sent、发送前旧报价迟到、过期后 Q1、取消意图、缺送达时间、迟到分钟与隔日 open 全部有失败路径；任何模式都不得使用未来成交价决定是否下单或数量。

### F03 · P1：退出存在未来信息泄漏，并漏掉成交日风险

位置：`backend/app/research/buy_execution.py:623`，关键 641–680。

退出直接排除 fill 当日；20 日计数也不含成交日。收盘跌破 L 后按日期拿 sell_quote，不要求 quote 晚于收盘可知时间；复现中 20:00 才知道的收盘失败，14:00 就卖出。stop 的 exit_at 使用 bar.collected_at，也不是对应执行事件。退出未体现既定滑点政策。

返工：保护 stop 从 fill 后生效；使用可验证的成交后路径处理当日 stop，日线歧义单列。收盘失效与时间退出先形成可知事件，再经历对应用户窗口/延迟并取后续可执行卖价。交易所 session 计数包括 fill 日，处理假期/提前收市。费用、滑点、gap、保护单与手动退出模式独立且可追踪。

验收：B09/B24 及 R03 退出要求；增加成交后当日止损、买入前 low、收盘前高价 quote、次日跳空、20-session 边界、缺退出报价、手动模式的逐步 fixture。不能简单把当日最低价一律视作买入后发生。

### F04 · P1：可交易性门没有接入成交链，事件规则与设计相反

位置：`backend/app/research/buy_execution.py:127,291`。

已有持仓只写 reasons，status 仍 eligible；failed coverage 同样 eligible。函数本身也未由建单/执行链调用。`holding_period_event` 被硬挡；`days_since_exit<2` 发明了退出后两日冷却。设计中的两日禁入指未来财报距离，持有期可能遇到事件只是单独提示。

返工：以当前知识时间的事件证据重查真实财报窗口、coverage、标的、结构有效性、持仓/资金与用户模式；每个门输出独立事实及合成可执行状态，由唯一协调入口消费。ETF 公司财报不适用。late_discovery/cross_unobserved/超 U 事实不能直接成为 fresh 买入入口。删除无规范依据的冷却和持有期全禁入逻辑。

验收：B06/B07/B13/B23/B25；测试当前已持仓、coverage failed、未来两 session 财报、只有持有期事件、ETF、迟到公告在 ready 时使入口失效。拒绝仍保留诊断样本。

### F05 · P1：没有真正的资金占用与并发执行模型

位置：`backend/app/research/buy_execution.py:208,374`；`backend/app/research/buy_evaluation.py:272`。

数量规划器虽有组合参数，建单调用只传 nav/cash/quote/stop，每笔相当于独立空仓。没有串联持仓、现金预留、退出释放与并发排序的 replay ledger。费用及价格上浮的资金预留也不足。汇总独立单笔收益不能代表规定资金约束下用户能赚到的钱。

返工：实现最小离线共享账本即可，不需要先做数据库。按固定 FIFO 处理同一事件时间的多标的竞争，预留现金/风险，成交、未成交、取消、退出各自释放；执行同 symbol、最多3个、单名/总风险/总仓位/主题限制。对未平仓保留占用，并记录每日 mark-to-market NAV。独立信号实验与真实资金政策模拟分别报告。

验收：四个同时机会只允许预算内成交；同 symbol 跨组不能加仓；现金不足含费用；Q1 上浮后不透支；未退出不能复用资金；展示排序不能暗中改变 FIFO。

### F06 · P1：trigger 不幂等，结构重建会错过上涨后的新机会

位置：`backend/app/buy_opportunities/breakout.py:171,256,300`。

trigger ID 包含 known_at，同一 bar 后一秒重放会生成新 ID。触发函数不检查 setup 终态，也没有离线生命周期协调层。重建用最新 close 与旧 L 比较，阻止价格在更高位置形成新 base 后再次突破。valid_from 用自然日；session_number、断档判断由调用方裸传，未提供日历/游标闭环。

返工：经济 trigger 的身份独立于处理时间，知识评估另存；同结构首次触发即解决 setup，outcome maturation 不占 active 身份。基于真实交易日历、闭合后的3个新 session 与新锚/base 判定重建，价格对新 L 校验。收市结构与未来 trigger 的时间关系必须验证。实现离线状态转换器、市场游标与知识游标，线上唯一约束留待 R05。

验收：B04–B07/B14–B16；100 次不同接收时间重放仍一个经济 trigger；resolved 不再次触发；旧标签 pending、第7日更高新 base 可进入；周末/假期、最后session、旧bar与新公告分别测试。

### F07 · P1：证据与真实/代理模式没有贯穿链条

位置：`backend/app/buy_opportunities/evidence.py:35,90`；`contracts.py` EvidenceBar/FrozenEvidence；`buy_execution.py:482,546`；`buy_evaluation.py:59`。

缺少接收时间时以 knowledge_at 补齐，却允许 observed_pit；可见性只看 available_at，缺少完整 bar 的交易日/收市约束。Q1 返回硬编码 observed_pit，EvaluationRow 默认 real，分钟模式返回 daily_execution_proxy。报价无 symbol/price basis，复权结构可与无映射原始报价直接比较。由此既可能时间泄漏，也可能把 fixture/历史重建包装成真实模式。

返工：来源模式必须从原始证据传递，不由执行函数升级；schema 禁止默认 real。Q1/M1/D1 执行政策与证据来源分别建模。observed_pit 缺真实可用时间即不具资格；历史重建采用显式假设并降级。冻结包含原始 revision、可知时间、完整 session 和价格口径；报价校验 symbol，拆股/复权转换有冻结映射。多 revision 按 cutoff 选择当时可见版本，保留证据，不简单把重复日期一律视作错误。

验收：B16–B18/B28；未来完整日线、缺接收时间、迟到修订、拆股、不同 symbol quote、synthetic 输入经过整条链后仍不计入 real。

### F08 · P1：R04 还不是端到端实验，也不能衡量漏掉了什么

位置：`backend/scripts/evaluate_buy_opportunities.py:66`；`backend/app/research/buy_evaluation.py:432,485`。

CLI 输入是预计算 EvaluationRow，接受调用者提供的 P&L；没有串起原始证据→全池scan→setup→trigger→响应→成交→退出→标签。10:00 diagnostic 只按 hour/minute 筛字典，没有统一入场/退出反事实。过滤器的 reduced_loss 用两组净和的负部差值计算：基线 +100/-50、过滤后只剩 +100 时会报告减少亏损0，而实际去掉亏损50。

返工：保留当前工具为 row summarizer，新增真正的离线 replay 入口。全池每个session的固定10:00诊断走相同冻结政策；不触发/拒绝/缺数据/事件/注意力/已有持仓逐项追踪。消融用稳定 opportunity ID 做成对比较，分别计算去掉的亏损、漏掉的盈利、频率和组合净差，不把缺失数据视作零损失。未来标签独立于执行退出，按实际 session 识别缺口、pending/censored，并补 MFE/MAE。

验收：B18–B20/B26/B28；输入原始冻结证据即可生成完整实验，而不是手写净收益；上述 +100/-50 fixture；缺分钟/退市不按可用bar数量偷换 horizon；没有真实分钟数据明确 daily proxy，不伪造10:00。

### F09 · P1：基准、回撤和统计区间不足以支持收益判断

位置：`backend/app/research/buy_evaluation.py:272,332,368,421`。

benchmark 仅对存在 row.benchmark_return 的交易名义额求和，缺失回报等效0；这不是规范中的独立100%基准持有组合。回撤来自已实现盈亏累计，遗漏持仓浮亏。date_block_estimates 是30自然日分箱后对交易样本做正态区间，既不是20交易日同步块bootstrap，也没有10/40敏感性或5000次固定seed；相关交易会制造虚假精度。

返工：同资金/区间的独立 buy-and-hold/cash 账本，缺基准数据明确 unavailable；日 NAV 计算回撤与占用。实现主方案已规定的跨标的同步交易日块 bootstrap、敏感性及有效块不足规则。成本压力、延迟曲线和集中度按相同 cohort 分别输出，不能混合 synthetic 或重复政策。不得通过降低样本门槛让结论变绿。

验收：B12/B20/B21/B28；缺 benchmark 不能称跑赢；大幅浮亏后收复仍有回撤；同一天30只高度相关盈利不能充作30个独立时期；同seed重放相同结果，样本不足没有伪精确区间。

### F10 · P2：追溯链断裂，实验文件仍可被覆盖

位置：`backend/app/buy_opportunities/scanner.py:245`；`backend/app/research/manifest.py:94`；`backend/scripts/evaluate_buy_opportunities.py:94`。

setup.origin_scan_id 与最后 _new_scan 计算的 scan_id 使用两套哈希算法，已复现不相等。manifest 只 hash evaluation_rows，scans/diagnostics 可改变报告却未计入输入身份。write_jsonl_atomic 使用 os.replace 覆盖已有目标；原子性不等于不可变性。

返工：定义一次 scan ID 并传递，避免 setup/scan 内容哈希循环依赖；全链引用可解析。manifest 冻结所有输入、代码/规则/政策/日历/价格映射及seed，主实验配置事先固定。已存在 run 的不同内容必须拒绝；同内容可验证后幂等返回，禁止替换已完成实验。输出重放读取需处理 type envelope（现CLI将输出 evaluation_row 的 type 原样传入严格schema）。

验收：B14/B16及不可变实验要求；所有 origin 引用可解析；改变 diagnostic/scans 导致新输入身份；同 run 不同内容拒绝；roundtrip 可读、校验失败明确报错。

### F11 · P2：R00 越界改变均值回归，却没有完成规则版本隔离

位置：`backend/app/decision_harness/strategies.py:398`、`BaseStrategy.version:58`；`backend/app/decision_harness/setup_projection.py:99`。

此次加入 band/RSI reentry 交易确认和新的 stop，违反 R00“不得重新发明均值回归”约束；同时仍沿用默认1.0.0。确认条件与中轨 trigger 展示也没有闭合。setup_projection 仍以 bullish 优先，独立风险入口未交付；未见完整规则依赖 hash 的修改。

返工：R00 回到保守降级和明确bug修复；新均值回归假设另开未来独立版本，不混入基线。补原版可重放身份、修正版身份及完整依赖hash，风险独立投影；提交同fixture修复前后结果。不要机械回退整个 strategies.py，保留已正确修复的其他部分。

验收：B01–B03、F05/F08；旧payload可读、旧版本结果可解释，改辅助计算影响规则身份；机器失效与高分矛盾不能建议买入，风险不被买入候选排序隐藏。

## 返工分批安排与交付要求

1. **先修测量事实：F02/F03/F04/F07。** 交付因果时序、模式和资格统一的执行入口；逐步手算成功/失败fixture。任何现有收益输出均不用于正向收益结论。
2. **补机会与资金闭环：F05/F06/F10。** 用最小离线状态机、账本和不可变文件实现，不提前做 R05 数据库平台。补齐旧标签未成熟仍可捕捉新结构、同一机会只计一次的纵向案例。
3. **重做研究入口与评价：F01/F08/F09。** 从证据重放产生结果；交真实覆盖清单、冻结manifest、完整默认15分钟报告和1/5/30对照。样本不足交 unknown 与具体缺口，工程仍可验收，禁止合成数据冒充收益证据。
4. **并入 R00 收尾：F11。** 版本/基线与风险投影修复后跑相关旧回归，不扩大策略范围。

每批交付：涉及的 F/B 编号、修改文件/版本、复现转绿记录、端到端命令、输入/输出manifest、至少一条完整事件流水，以及剩余工程缺口和收益证据状态。七项独立反例只是最低回归集，不能替代本报告新增的边界场景。

最终重新验收必须现场从冻结原始输入运行一次完整 R01→R04，能够逐笔解释：为什么发现它、何时能知道、用户何时响应、为什么准入/拒绝、用了什么可执行价格和资金、何时为何退出、净收益如何计算、同一政策下漏掉哪些机会。此链可信以后，再由报告决定继续 S1、修改假设或否定当前策略；不要用测试通过或字段齐全代替这一判断。

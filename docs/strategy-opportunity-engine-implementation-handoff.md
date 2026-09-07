# Urus 主动查看机会引擎 v3：R00–R07实施清单

2026-09-05，用户授权本地实现与测试。唯一规范：[主方案v3](strategy-opportunity-engine-improvement-plan.md)。旧v2清单已归档，不再要求推送、默认响应或交易账户。保留外部agent正确修正，先补R00–R04，再接R05/R06；R07由研究证据决定。

## 交付原则

不下单、不推送、不假设用户持仓。所有当前判断基于可见证据，未来标签只在研究层。不得把触及区间说成成交、参考价变化说成实际盈亏、日线说成实时。没有真实数据足够证明有效时明确unknown，仍完成可运行代码和fixture。

各agent只改分配文件；共享工作区，不回退其他人更改。保留已有模拟研究作为optional legacy，不为收窄范围删除历史功能。新增schema/政策版本，不偷偷改旧实验语义。不部署/订阅/外部调用/定时运行/清库。当前任务已授权常规本地代码/测试，无需重复确认。

## R00：核对已有修正

检查strategies/technical/setup_projection及现有测试，保留失败突破、无突破量能、矛盾降级、风险独立、proxy与hash修正。不重写均值回归。新增改动只修发现的真实回归；报告现有修正通过情况，不重做整个旧任务。

## R01–R02：证据、结构与事件

复用contracts/evidence/scanner/breakout/lifecycle，补v3需要的价源/时间/身份约束。61bar、L/S/A/U不改参数；原始成交额proxy、完整bar知识边界、实际session、同symbol/basis、最新可见revision严格处理。结构首次事件resolved不代表历史机会不能查看；经济身份独立于view snapshot。

验收V02–V06/V10/V15/V16，原有生命周期回归保留。

## R03：查看投影替代默认执行链

新增view_contracts.py和view_projection.py（或同等独立边界），输入冻结setup/trigger、可见价格/事件、查看时间/知识时间，输出主方案第5节契约。可选单独参考观测模型，不强迫依赖buy_execution.QuoteObservation。

实现日线/报价时效、状态优先级、S/L/U、已延伸后回落不复活、事件风险和unknown收益资格。无推送、ready、订单、资金依赖；既有research执行器不删。用真实实现测试缺quote、旧Dataset、失效、waiting、延伸、源口径、未来数据。

交付V01–V08/V12纯函数回归与可手算投影fixture。

## R04：机会质量离线主入口

新增research/opportunity_quality.py及scripts/evaluate_opportunity_quality.py，无需预填P&L，冻结输入生成全池scan、结构/事件、view snapshots及独立labels。daily首版为必需，intraday有数据才评估。

主cohort锁定日期/来源/粒度，标签session horizon、evaluation_as_of、pending/censored/ambiguous、reference return/benchmark excess/MFE/MAE与coverage/拒绝诊断。多snapshot经济机会去重。默认不输出利润支持，显式价格代理。

复用现有manifest/统计可行部分，不混入legacy成交政策。提供fixture JSON、CLI实际运行产物与报告；真实覆盖缺失如实写明。

交付V01/V07–V12/V14/V16，期间R03契约由其负责人维护，其他agent不能冲突修改。

## 第一验收点

R00–R04有关测试与v3端到端入口通过，检查产品无订单/推送依赖、价格不倒填、source不可升级、unknown资格正确。不以legacy模拟链曾经存在问题否决v3，也不以字段齐全忽略事实错误。

2026-09-06主agent验收通过：新增view/replay/quality共26项测试；原始fixture的两次CLI分别验证0与1个合格主样本，均保持统计unknown。实际产物和既有R00回归见[交付记录](reviews/opportunity-view-v3-delivery.md)。后续审查意见由主agent按职责、证据及可复现行为裁定，不把可选增强当成阻断。

## R05：只读主动查看接缝

接入现有Daily Dataset读取服务/API及InstrumentDecisionView，复用v3投影，不import研究标签。只读、不补采、不改历史、无新增推送或账户表。页面显示日线研究计划/状态/价格区间/as-of/质量/条件/失效/研究unknown。API 404与旧调用兼容，前端只消费后端事实。

先稳定后端payload再前端接入；只做最小展示，不重设计。验收V02/V03/V04/V13，后端集成测试、前端类型/构建与针对性测试。

## R06：查看回放与交付验证

同一数据集在不同查看时间、无quote、假期/session边界、来源不可用、重复打开、未来证据等回放。运行相关全套测试，检查当前旧功能。交付验证报告与数据覆盖缺口，真实研究不足仍unknown。不新建周期shadow或推送监控，不等待真实用户买卖记录。

## R07：条件性后续

没有独立研究证据时不增加新策略或复杂排名。完成一页后续研究判定：是否需要增强、依据、尚缺什么、下一预注册实验。不得仅因任务序列到R07就加入新指标/模型。

## 分工与结果模板

任务/文件/版本；对应V编号；测试命令/结果；一条成功和失败流水；数据源/粒度/是否synthetic；与legacy边界；工程完成度；收益证据及未知项。

旧docs/reviews/buy-opportunities-r00-r04-review.md是修正前v2审查记录，不是当前未修复清单。需要按新工作区重新跑证据，旧独立反例可作legacy回归，但不能要求重建推送/交易产品。

2026-09-06交付：R05只读Dataset接口与个股页面接入完成；R06真实冻结Dataset回放及最终测试通过（后端251、前端59、生产构建）。R07依据证据不足决定暂不增加策略。详情与运行产物见[交付记录](reviews/opportunity-view-v3-delivery.md)。

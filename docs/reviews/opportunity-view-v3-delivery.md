# 主动查看机会引擎 v3：实施与验证记录

日期：2026-09-05至2026-09-06。规范：[设计v3](../strategy-opportunity-engine-improvement-plan.md)、[实施清单](../strategy-opportunity-engine-implementation-handoff.md)。本文件随本轮实际验证更新，不把历史v2审查缺陷直接套到外部agent修正后的代码。

## 产品边界

用户主动查看，系统提供截至证据时点的结构、价格研究区域、条件、失效与数据限制。没有推送、默认响应、下单、账户或真实持仓假设。固定期限价格变化不是实际盈亏，未来触及区域不是成交。已有交易模拟仅保留为可选历史研究工具。

## 本轮执行顺序

1. 主agent修订设计/清单并归档v2；三名Luna Builder分工进行查看投影、机会质量研究及既有修正验收。
2. 先验证R00–R04调整，再接入现有Dataset的主动查看读取路径与个股工作区。
3. R06完成本地回放/集成验证；R07仅在独立证据支持时引入新策略，本轮不预设增强一定值得做。

## 改动前基线

前端在新增功能前执行 `npm test -- --reporter=dot`：14文件、54测试通过；`npm run build`通过。

后端在新查看/质量测试加入前执行 `.venv/bin/python -m pytest -q`：全套通过，exit 0；仅已有Starlette/httpx和Python sqlite日期适配弃用警告。这说明本轮以外部agent修正后的工作区为起点，不能继续宣称修正前七个问题仍然存在。

Luna独立验收：`test_buy_opportunities.py`、`test_buy_evaluation.py`、`test_buy_research_controls.py`、`test_strategies.py`、`test_technical.py`及历史审查反例合计59通过；`test_daily_evidence.py`、`test_api.py`、`test_observation.py`合计43通过。历史七个反例已转绿；v3的新验收另列，不把旧执行链测试等同于主动查看目标已实现。

## 已落地的工程边界

`backend/app/buy_opportunities/daily_replay.py`统一日线scan、稳定setup、首次trigger及后续生命周期；查看和质量研究共用这条路径。第5个session先检查当日证据再判过期，缺session不会被相邻bar掩盖。

`view_contracts.py`与`view_projection.py`提供独立只读投影，保留L/S/A/U、证据引用、查看时点、知识时点、来源口径及状态原因。只有日线而没有合格quote时，不会声称当前处于可参与区间；陈旧证据保留时效限制。

`backend/app/research/opportunity_quality.py`与`backend/scripts/evaluate_opportunity_quality.py`从冻结原始输入生成研究产物。全池查看覆盖与生命周期历史scan分开计数；主评价固定锁定期、来源、期限、有setup且状态合格的候选，重复查看按经济机会另行去重。未来标签只存在于研究层。

## R00–R04独立验收

主agent在额度恢复后运行 `.venv/bin/python -m pytest tests/test_daily_opportunity_replay.py tests/test_opportunity_view.py tests/test_opportunity_quality.py -q -o addopts=''`：26 passed。验收覆盖5个完整session的边界、缺session、结构重建等待、日线/quote区分、陈旧证据、已失效/延伸后回落、事件覆盖、未来标签成熟度、固定主cohort及重复机会去重。保留原先R00回归结果。

实际调用CLI生成两份不可变产物，均使用[构造输入](../examples/opportunity-quality-v3-synthetic.json)、1-session主期限、historical_reconstruction/synthetic来源和固定种子5000：

| 产物 | 开发期 / 锁定期 | 主样本 | 统计结论 |
| --- | --- | --- | --- |
| [空主样本](../../output/opportunity-quality-v3/root-verified-empty-primary-20260906.jsonl) | 3月30日–4月3日 / 4月6日–10日 | 0 | unknown |
| [单主样本](../../output/opportunity-quality-v3/root-verified-one-primary-20260906.jsonl) | 3月30日–31日 / 4月1日 | 1 | 样本不足，unknown |

日期均为2026年；created/knowledge/evaluation时点固定为2026-05-23T22:00:00Z。每份包含12个查看截面、12条对应scan、216条完整历史scan、8个setup、1个trigger和30个期限标签。两个锁定期用于验证筛选行为，不是挑选真实研究结果。有效样本数不足时不输出置信区间；10/20/40-session统计按抽到的时间块累计sum/count，维持与机会均值相同的权重。

## R05页面接入

InstrumentDecisionView新增“日线研究计划”卡片，消费同一Dataset的 `GET /api/daily-evidence/datasets/{dataset_id}/opportunity-view`。展示状态、日线参考价、证据截止和查看时点、研究区间、失效位、条件及质量限制；无结构时显示原因，收益研究支持保持未知。长scan标识放入可展开追溯信息。

切换Dataset/标的时立即清空旧结果，迟到请求不能覆盖新结果；响应及嵌套机会的Dataset/标的须匹配。加载中、演示数据或没有Dataset时不请求机会接口。卡片不触发冻结、补采或其它写入；原页面既有获取Dataset流程不因本卡片而重构。

前端 `npm test`：15文件、59 tests passed；主agent在补充未知knowledge_at类型后再次执行 `npm run build`，类型检查和生产构建通过。

## R06本地冻结Dataset回放

主agent通过SQLite `mode=ro`读取Dataset `0932d61c-122f-4959-8a32-2cf392eb5932`及对应图表，直接调用同一路由使用的纯适配器，未启动采集或真实数据库迁移。INTC与QQQ各有370根冻结日线，日期2025-03-11至2026-08-28；scope中的机会标的是INTC。逐bar可见时间与revision映射缺失，输出明确使用historical_reconstruction/unknown，研究支持unknown。

[真实存量数据回放产物](../../output/opportunity-quality-v3/root-real-dataset-view-20260906.json)保留原始payload指纹及两个查看时点。同一证据截止2026-08-30T09:16:01.121035Z，在冻结后一小时与2026-09-06查看时，参考价均89.47、市场证据均截至8月28日收盘；freshness由fresh变为stale。结构已失效，status按优先级保持invalidated，不能为展示stale而掩盖失效事实。重复相同输入/时点结果相同，均无in_entry_zone；数据库total_changes为0。

主agent另外在内存副本上复现并验证：Dataset符号质量conflicted→不可用；显式scope选择QQQ且QQQ同时为benchmark→仍保留QQQ；缺失截止时间→knowledge_at为null、无机会和参考价；未来截止时间→不可用、无参考价。没有把这些构造变体写回数据库。

## 最终验收结果

2026-09-06主agent完成最终验证：

| 检查 | 结果 |
| --- | --- |
| 后端完整测试 `.venv/bin/python -m pytest -q -o addopts=''` | 251 passed，31.78秒 |
| 前端完整测试 `npm test -- --reporter=dot` | 15文件、59 passed |
| 前端 `npm run build` | 类型检查与生产构建通过 |
| `git diff --check` | 通过 |
| 原始fixture CLI及真实冻结Dataset纯适配器回放 | 通过，产物见上文 |

接口新增12个测试用例，包括实际反例的截止时间、符号质量、显式基准成员回归，以及成功响应JSON序列化。新接口只读取冻结Dataset与chart；不存在Dataset返回404，缺chart按scope保留不可用行。完整后端仅有已有Starlette/httpx与SQLite日期适配弃用警告，无失败。

R00–R06工程交付完成。R07完成条件性研究判定，当前证据不足以引入额外策略。审查意见由主agent按本轮职责和可复现行为裁定，没有把可选增强或真实收益证明当成代码交付的前置条件。

## 收益证据范围

本轮实现不是盈利证明。构造数据只验证工程时序、状态和指标计算，不能作为真实市场优势样本。没有足够真实锁定评价与报价覆盖时保持unknown；没有推送系统也不虚构送达、响应或用户成交统计。

只读盘点本地 `backend/urus.db`（SQLite `mode=ro`，非线上数据库）：canonical日线含 `moomoo_opend_history` 10,050行、28标的，日期2025-03-21至2026-08-28；另有legacy来源6,802行、19标的，不能把重叠来源累加为独立历史。28个symbol按未去重行数均超过61，但这不等于61根合格、不冲突的完整session已验证。冻结Dataset共127份（124 ok、3 partial），交易日范围2026-08-21至2026-08-28。

本地最新Dataset交易日为2026-08-28，截止2026-08-30；在本次2026-09-06查看时不能伪装为当前行情。此盘点仅证明本地存在可供日线只读接入的存量数据，不证明盘中quote/PIT接收覆盖，也不证明真实市场收益优势。没有执行新增采集或更改这些记录。

## R07后续研究判定

当前不新增回踩策略、量能过滤、收益预测排名或期权入场条件。先用v3统一截面的全池报告定位问题：若延伸机会多但随后出现可验证的新结构，再预注册单独回踩条件；若相同资格候选的相对强弱存在独立样本差异，再研究排序。每次只增加一个假设，与同截面、同覆盖的S1配对，分别报告去掉亏损、漏掉盈利、候选频率及价格表现差异。数据不足就是暂不引入增强的依据。

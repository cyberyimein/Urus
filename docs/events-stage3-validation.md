# Stage 3 事件验证实现

本阶段只启用“预期事件”调查员，突发新闻 Agent 仅保留配置名，不进入运行路径。
Anomalo 的预设 Agent 调用方式见 [preset-agent-integration.md](./preset-agent-integration.md)。

## 生命周期

1. 1B/3B 的规律事件工作流包含两个独立步骤。`schedule_step` 按“事件定义 + 主体”检查数据库中今天之后是否已有未取消的未来事件；只有存在缺口时，才调用 `discover_schedule` 日历 API。已有未来日历时复用 SQLite；对于没有具体日期的 `unverified/expected` 记录，在其 `next_check_at` 到期前也视为已检查，避免每天重复联网。3B 因此会逐只股票判断，而不会因为 INTC 已有财报日期就跳过 NVDA。
2. `result_step` 独立检查今天以前已经到期但没有最终结果的事件。命中时逐条调用 `collect_result` 结果 API；若 `result_expected_at` 缺失，则以已经过去的 `scheduled_at` 作为兜底。
3. 两个步骤使用不同的请求、响应 Schema、触发条件和调用审计。日历步骤的跳过或失败不能阻止结果步骤执行，结果步骤的失败也不改变已经成功写入的未来日历。
4. Agent 必须使用网页来源并严格返回 `json_schema`；Urus 将事件定义、实例、来源和每次 Agent 调用写入 SQLite。相同 `event_key` 更新原记录，结果按版本递增。
5. `post_close_review` 是每日第三次执行。它不改变事件结果；对尚未留下有效复盘反应的已确认事件，只在首次可用的盘后快照中写入价格、昨收和涨跌幅。已有 `measured` 记录的历史事件不会在后续每日复盘重复写入。

FOMC 的 `confirmed/revised` 结果还有额外语义门槛：至少一条 `facts.actual` 必须非空，且必须带结果来源；只有 fact 名称、全部实际值为空的响应会被结果步骤拒绝入库。

## 正式查询范围

1B 只覆盖当前会显著影响大盘、且有官方日历的七类规律事件：FOMC 利率决议、CPI、PCE、非农就业、GDP、ISM 制造业 PMI、ISM 服务业 PMI。

3B 只覆盖 `EVENT_INSTRUMENT_SYMBOLS` 中自选股的季度财报发布与电话会。SPY、QQQ、SMH、IGV 等 ETF 不具备公司财报，因此不进入 3B 财报日历；它们的价格反应仍由市场数据步骤处理。演讲、产品发布、股东会和突发新闻暂不启用，避免在信息源与材料性标准未确定前扩大检索量。

未来日历窗口统一为 120 天（约四个月）。这是每次补缺调查的范围，不代表每次工作流都会联网；只要 SQLite 已覆盖相应定义与主体，日历 API 就会跳过。

## 历史漏采验证（FOMC）

验证“错过上周会议”的路径时，不应把上周日期硬编码到生产发现器，也不应把验证数据写入开发数据库。测试夹具会在临时 SQLite 中补录一条已经发生的 FOMC 事件，保留 `scheduled` 状态且不创建 `event_results`：

1. 先执行未来日历调查。Agent 返回 `events=[]` 时必须同时返回 `missing_definitions=["macro:fomc_decision"]`，否则 Urus 判定调查不可用。
2. 因为补录事件的 `result_expected_at` 和 `next_check_at` 已经过期，协调器会在同一次调度步骤中请求结果；第一次返回 `not_released`，写入结果版本 1 和新的 `next_check_at`。
3. 将 `next_check_at` 推进到期后再次执行。协调器再次调用结果调查，返回 `confirmed`，写入版本 2；事件实例仍只有一条，来源按 URL 去重。

因此，1B/3B 的一次执行可能包含两种不同的 Agent 请求：日历请求只在数据库缺少未来事件时发生，结果请求只针对已到期且没有最终结果的事件发生；两者不共用触发条件，也不互相短路。

对应自动化验证：

```bash
cd backend
pytest -q tests/test_events.py -k 'historical_fomc or schedule_lookup or schedule_failure'
```

这验证的是生命周期和持久化，不把夹具中的“已确认”事实当作真实 FOMC 数据。真实网页调查仍通过 `backend/scripts/anomalo_fomc_validation.py` 单独进行。

对已经发生的会议做只读真实 API 验证可运行：

```bash
cd backend
ANOMALO_TEST_TIMEOUT_SECONDS=600 \
  .venv/bin/python scripts/anomalo_fomc_result_validation.py
```

脚本只验证 Anomalo 的 `collect_result` 结构化返回，不写 SQLite；`tool.error` 会被统计为工具告警，
只要 HTTP 请求成功、`output` 通过严格 Schema 且 `event_key` 匹配，就不会把整次结果调查判为失败。

## 验证开关

```dotenv
ANOMALO_ENABLED=true
ANOMALO_BASE_URL=https://your-anomalo-host
ANOMALO_TIMEOUT_SECONDS=600
ANOMALO_SCHEDULED_AGENT=scheduled-event-investigator
EXPECTED_EVENTS_ENABLED=true
BREAKING_EVENTS_ENABLED=false
EVENT_DISCOVERY_HORIZON_DAYS=120
EVENT_INSTRUMENT_SYMBOLS=LITE,COHR,MRVL,NOK,AMD,INTC,NVDA,NBIS,ORCL,MSFT,NOW,RKLB,AMZN,AAPL,GOOG
```

未打开 `EXPECTED_EVENTS_ENABLED` 时，1B/3B 保持跳过；未配置 Anomalo 时，打开后会明确显示
`unavailable`，不会把缺少联网数据伪装成成功。`BREAKING_EVENTS_ENABLED` 目前只是审计字段，
代码不会调用突发 Agent。

## 联网调查耗时基准

2026-08-03 使用同一份 FOMC 未来 120 天日历提示词进行了三次端到端 API 测试：

- 第一次在 300.45 秒触发客户端读取超时，没有取得响应体。
- 第二次在 144.30 秒成功，包含 8 次工具调用和 6 次 LLM 请求。
- 第三次在 182.34 秒成功，包含 7 次工具调用和 5 次 LLM 请求；前两次工具调用并行启动。

两次成功结果都返回 2 条 FOMC 会议记录，通过严格 JSON Schema 校验，没有思考过程泄漏或
`tool.error`。当前单次规律事件日历调查应按约 2.5～3 分钟估算，并保留 600 秒读取超时；延迟仍有
明显波动，不能把它放在要求快速响应的页面请求链路中。搜索工具本身只是总耗时的一部分，多轮
Agent/LLM 循环和结构化输出同样占用主要时间。

## 全量时间表初始化

系统现在提供独立于每日 1B/3B 工作流的“全量时间表初始化”命令。它用于首次部署、数据库重建、
新增事件定义或新增自选股后的批量预热，不应由普通页面请求隐式触发。

运行方式：

```bash
cd backend
ANOMALO_BASE_URL=https://your-anomalo-host \
  .venv/bin/python scripts/initialize_event_schedules.py
```

可用参数包括 `--category macro|instrument|all`、`--symbols INTC,NVDA`、
`--batch-size`、`--force`、`--database-url`、`--agent` 和 `--timeout-seconds`。默认批次大小为 1，
即每次 Agent 请求只调查一个“事件定义 + 主体”，用于规避 Anomalo 单次约 300 秒的运行上限。
默认模式是幂等的缺口初始化：
已有未来事件的“定义 + 主体”不会再次联网；`--force` 才会刷新全部请求目标。

命令会实时输出 `[schedule-init]` 进度行，包含批次 ID、分类、请求序号、目标数量、完成状态和错误；
所有请求结束后再输出最终 JSON 汇总。因此单个请求等待数分钟时也能确认进程仍在运行。

初始化功能满足：

1. 人工或后台任务显式启动，按 1B 宏观事件和 3B 个股财报拆分任务。
2. 对全部启用的“事件定义 + 主体”查询未来 120 天日历，并写入同一套 SQLite 事件表。
3. 支持逐任务进度、成功/失败状态、耗时、重试次数和 Anomalo 调用审计；单个主体失败不回滚其他成功结果。
4. 支持断点续跑和幂等更新，重复运行依靠稳定 `event_key` 更新，不制造重复事件。
5. 限制并发，避免全部自选股同时触发大量网页搜索；失败项应单独重试。
6. 初始化完成后生成覆盖报告，列出已覆盖、缺失、不可确认和即将过期的定义/主体。
7. 日常 1B/3B 仍只负责增量补缺和到期结果采集，不重复承担全量初始化成本。

每次初始化会在 `event_schedule_initializations` 写入批次状态、请求目标、耗时、调用次数、
发现数量、缺失数量和分类结果；每次 Agent 调查仍写入 `event_agent_runs`。因此联合测试前先运行
一次初始化，后续测试只会复用 SQLite，除非显式使用 `--force` 或数据库覆盖已经缺失。

## 调查提示词

日历查询和结果查询的用户提示词统一存放在
`backend/app/events/prompts/scheduled_events.yaml`。固定指令全部使用英语，生产代码把本次定义、主体和时间窗口注入后，再将完整用户消息序列化为 YAML。后续修改调查策略不需要改 Python 字符串。

Schema 字段的许可值均以 `allowed_values` 数组表达；数值置信度使用二元素的 `numeric_range` 数组。YAML 提示词只是给 Agent 的明确约束，API 仍同时发送严格 JSON Schema，返回后仍由 Pydantic 二次校验。

结果模板还按 `event_type` 定义 `expected_facts`、`required_actual_facts`（或
`required_actual_any_of`）和 `require_sources`：

- CPI/PCE：要求总体与核心指标的环比、同比实际值。
- 非农：最低要求新增非农、失业率和平均时薪环比。
- GDP：最低要求实际 GDP 年化季环比。
- ISM 制造业/服务业：最低要求对应 PMI；分项属于期望字段。
- 财报：最低要求摊薄 EPS 和营收；毛利率与前瞻指引属于期望字段。
- FOMC：目标利率区间、变动基点、投票结果至少有一项，并要求结果来源。

缺少最低必需字段的 `confirmed/revised` 结果会被拒绝入库；最低字段满足但缺少其他
`expected_facts` 时会成功入库并产生完整度告警，便于正式联调时区分“不可用结果”和“可用但不完整结果”。

## 结构化调用

后端每次调用携带 `response_format.type=json_schema`。发现任务的 schema 名称为
`scheduled_event_discovery`，结果任务的 schema 名称为 `scheduled_event_result`，Schema 由
`backend/app/events/contracts.py` 生成并由 Pydantic 再次校验。Anomalo 响应中的 `output` 才是
业务数据，不能从 `final_text` 猜字段。

手动在 Anomalo 对话框验证时没有后端 `response_format` 的硬校验，可直接复制生产代码渲染出的 YAML 用户消息；手动对话可验证来源和语义，但不能替代 API 的结构化输出验证。

## 精度与后续工作

验证阶段先接受 Agent 的时间估计和官方来源覆盖情况；正式落地前需要补充交易所/公司日历
交叉校验、时区和提前收盘处理、共识数据来源、结果修订语义及价格反应窗口（例如
`release_to_close`、`next_session`）。突发新闻 Agent 也应在确定信息源、材料性阈值和去重策略后
单独验收，不能与预期事件调查混用。

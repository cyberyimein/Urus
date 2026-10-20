# Urus 调度与数据采集检查（2026-10-04）

## 结论与范围

性能优化可以按本轮约定的阶段收尾。两项 P2 审查问题已修复，前一轮后端全量 315 tests、前端 88 tests 和构建记录见性能交付及审查文档。本次 `git diff --check` 再次通过，未修改性能实现，也未重复无变化的全套测试。Discovery 第二阶段批量读取、线上并发/P95、PostgreSQL 集成仍不是已完成项。

生产数据检查的结论是：**近期日线、收盘期权快照、观察报告和盘中采样持续正常；收盘分钟线及收益标签的下游入库存在确认的断点，不能把整个采集链路认定为完整健康。**

检查时间为 2026-10-04，周日；最新预期交易日是 2026-10-02。通过 Mac mini 容器进程、API、SQLite `mode=ro` 和部署源码进行只读核查。未重启、部署、重新采集、补写或清理生产数据。本文件只保留聚合证据，不包含完整私有配置、原始行情或原始日志。

生产 API 运行独立 API supervisor，采集运行在独立 `urus-moomoo` 容器。进程检查未发现旧 backend market/intraday scheduler 同时运行。仓库保留旧入口，不应将其缺陷直接归因于当前生产。

生产三个性能相关源码文件的 SHA-256 均不同于当前工作区：Observation Repository、Observation API、opportunity replay service。因此本次生产健康结果不构成新性能补丁的上线验收；工作区性能改动仍待提交和部署。

关联文档：

- [性能交付](performance-optimization-delivery-20261004.md)
- [性能代码审查及修复验收](performance-optimization-code-review-20261004.md)

## 生产证据

| 项目 | 只读核查结果 | 判断边界 |
| --- | --- | --- |
| API / 数据库健康 | `/api/health` 返回 production、status=ok、database=ok | 该健康接口不能代表调度器心跳或采集完整性 |
| 最新日线 | 当前计划 46 个标的全部到 2026-10-02；主库当天 46 个标的 quality_status=ok | 仅确认最新覆盖，不代表全部历史日线无缺口 |
| 收盘市场采集 | 46 个请求、46 个返回、unavailable=0、is_mock=false、quality_status=ok | 实际采集范围来自动态计划，不能按静态配置数量判断漏采 |
| 收盘期权快照 | 19 个请求、19 个返回、unavailable=0、is_mock=false | 收盘快照与盘中报价质量是两套检查 |
| 观察报告 | 9 月 22 日至 10 月 2 日，连续 9 个交易日均有 succeeded run | 最近 8 份报告进一步核查：每份 10 个分组成功、失败 0、quality_issue_count=0 |
| 最新报告时刻 | 10 月 2 日 20:36:34 UTC 完成；发布账本 20:36:56 UTC 记录成功 | 对应日本时间 10 月 3 日 05:36；不能仅按日本自然日归集交易日 |
| 盘中调度时点 | 上述 9 日各 77/77；每天 fast=71、structure=6，全部 succeeded，late=0 | 77 个时点为美东 09:35–15:55，每 5 分钟一次 |
| 盘中任务完成延迟 | 各日中位数约 14.1–15.5 秒，九日最大值 24.8 秒 | 从计划时间到 scheduler 完成，不等同于报价年龄 |
| 最新盘中质量摘要 | 77 个 run 均 complete=true、3/3 标的；报告的最低 quote_coverage=1.0 | 此 coverage 没有纳入 stale/zero_quote，见 S3 |
| 最新收盘 finalizer 原始包 | complete=true，3 个收盘价、1,170 根分钟线、warnings=0 | 下游最终分钟线和 outcomes 未入库，见 S1 |
| 最新 EOD 原始包 | complete=true，3 个标的、1,170 根分钟线 | 本次未验证其他应用对 EOD 导出的消费结果 |
| 发布待处理账本 | 没有已登记但未发布的日期 | 不意味着不存在从未登记的整日缺失 |

盘中核查日期：2026-09-22、23、24、25、28、29、30、2026-10-01、02。日期均按 America/New_York 归属，避免跨日本午夜导致错误的漏点判断。

## S1 / P1：收盘 finalizer 只存原始包，没有完成下游入库

状态：**生产已确认，优先修复。** 不影响当前日线观察报告，但使盘中研究所需的收盘修订和监督标签缺失。

位置：`urus-moomoo/src/urus_moomoo/runtime.py:65`；`storage/intraday.py` 的 `CanonicalIntradayStore`。旧 backend 对应语义在 `backend/app/intraday_options/storage.py:495` 的 `finalize_trading_day()`。

证据：

- 独立 runtime 只有 `capture_intraday` 分支调用 canonical store；`capture_session_close` 仅写 collector 原始包，再按 payload.complete 标记任务完成。生产部署源码同样缺少此分支。
- 10 月 2 日收盘原始包完整，包含 3 个标的共 1,170 根分钟线。
- 消费者实际使用的 intraday v2 数据库中，3 个标的最后分钟线均为 `2026-10-02 19:55:00 UTC`，即美东 15:55。
- `intraday_option_outcomes` **全表 0 行**，不是仅当天延迟。

修改建议：

1. 在独立 `CanonicalIntradayStore` 实现收盘落库方法，保持 sidecar 独立性，不导入 backend 应用模块。复用已有表契约：补齐/修订 regular-session bars，记录 revisions，并为当天 accepted run 的 selected attempt 生成每标的 outcome。
2. 在 `CollectorRuntime.collect_task()` 的 `capture_session_close` 分支显式调用该方法。只有原始包持久化和下游事务都成功，才允许 scheduler 标记 completed。两个库之间不假装具有单一事务；设计可重复执行的恢复路径。
3. 按 `(capture_run_id, symbol)` 幂等 upsert；收益标签严格使用该 run 选中的 attempt 和该交易日的收盘数据，不使用最新现价或其他交易日结果。
4. 提供读取已存收盘原始包的恢复入口，支持 dry-run、指定日期和恢复数量。历史调度记录已完成，修复代码后仅重启不会自动补齐这些日期；必须显式恢复。不要重新抓取今天的快照来冒充历史数据。

验收：完整普通交易日的 3 个标的分钟线到达原始包最后 regular-session bar；77 个成功 run、每个 3 个可用 spot 时应有 231 条 outcomes；重复执行不增加重复行；注入下游事务失败后不能出现新 completed；已有 completed 的日期可由恢复入口修复。覆盖缺标的、缺 selected attempt、半日市和跨 UTC 日期的边界。

## S2 / P2：每轮恢复扫描全部历史包，并同步阻塞采集循环

状态：**生产代码与数据规模已确认；近期尚未造成漏点。**

位置：`urus-moomoo/src/urus_moomoo/publisher.py:292`、`:315`；`cli.py:101`；`services/scheduler.py:128`。

每轮 `on_idle()` 调用 `retry_pending()`，它先查询所有已完成收盘 slot 的完整 `payload_json`，再逐包解析、校验、计算 hash。生产精确匹配该 SQL 的 v2 包有 28 个，共 **51,205,661 字节，约 48.8 MiB**。poll 配置是 10 秒，空闲周末也重复扫描；实际周期还要加上扫描和发布耗时。该数值是逻辑读取及处理量，不能直接当成物理磁盘 I/O。

此外，`on_idle()` 在 due 列表计算后、真正采集前执行。API 故障时同步发布超时会推迟已到期盘中任务；coordinator 的释放在发布重试之后。`retry_pending()` 按日期顺序执行，某日抛异常就退出本轮，后续日期没有逐项异常隔离。

修改建议：

1. 日常 tick 只扫描新增或尚未确认的 capture 元数据，再读取其 payload；持久化已验证身份 `(capture_id, content_sha256)`。不要仅用最大交易日期作 watermark，以免遗漏迟到历史包。
2. 为每日期维护 attempts、next_retry_at、last_error、published 状态；错误隔离到单个日期，退避并限制单轮处理数量。永久不合法包需可见地标记，避免每 10 秒重复阻塞。
3. 将 provider lock 释放与网络发布分开，优先执行到期采集。发布使用独立、有界 worker 或显式耗时预算，不共享同一个 SQLite connection 跨线程。
4. 保留显式的全量账本重建/校验入口，支持账本丢失恢复。正常 tick 的增量化不能破坏原始包验证及不可变身份检查。

验收：历史包从 30 增至 3,000 时，无新增包的日常 tick 不再加载所有 payload；发布 API 超时不会推迟盘中采样；首日发布失败时次日仍可完成；恢复进程重启后幂等，无重复 Run。

## S3 / P2：采集完整率与可用报价率混为一谈

状态：**数据与代码已确认；不应简单把所有 stale 当成 API 采集故障。**

位置：`urus-moomoo/src/urus_moomoo/integrations/moomoo_intraday_options.py:423`；`storage/intraday.py:339`。

10 月 2 日 77 个 run 所选 attempt 的合约快照共 49,584 条：

| 当前入库状态 | 数量 | 占比 |
| --- | ---: | ---: |
| ready | 14,309 | 28.86% |
| stale | 31,265 | 63.05% |
| zero_quote | 4,010 | 8.09% |

同一日每个 run 的 quality_summary.complete 都为 true，最低 quote_coverage=100%。原因是 provider coverage 判断仅要求 bid/ask 存在、bid>=0、ask>=bid；store 则另行把年龄 >30 秒标记 stale，把零 bid/ask 标记 zero_quote。

完整抓回低流动性合约并不等于它们都具有可交易报价。当前 provider quote_time 来自 update_time，本次没有进一步验证供应商该字段是否等同于双边报价更新时间，因此不宜据此武断认定网络延迟或直接调宽阈值。

修改建议：保留 capture completeness，同时独立统计 ready/stale/zero_quote/unknown_time 数量、fresh coverage 和报价年龄分位数；在消费接口/研究输入中明确用途与过滤策略。按 symbol、DTE、moneyness、fast/structure 分层观察，验证 update_time 语义后再设阈值。不要为了 freshness 不达标而无限重采低流动性链，也不要把原始事实直接丢掉。

验收：构造“结构完整但全为 stale/zero_quote”的输入，采集完整可仍为 true，但不能展示成 100% 可用实时报价；下游知道使用了多少新鲜/过期/零报价，并遵守自身筛选规则。

## S4 / P2：交易日历瞬时异常会退出整个调度循环

状态：**代码及本地无外部依赖复现确认；未据此归因历史缺口。**

位置：`urus-moomoo/src/urus_moomoo/services/scheduler.py:128`、`:223`。

`run_once()` 只隔离 runner 异常，`due()` 内的 calendar/provider/store 异常会经 finally 继续抛出；`run_forever()` 没有恢复边界。使用抛出 TimeoutError 的 calendar 执行真实 `run_forever()`，异常立即逃出循环。生产部署实现相同。

修改建议：在顶层 tick 对可恢复异常记录结构化错误并指数退避，保留信号和主动停止语义；配置错误、坏 schema 等持续错误应明确 degraded/fatal，避免吞掉后无限空转。增加 last_tick、last_success、consecutive_errors 的健康状态与外部进程监测。

验收：calendar 首次超时、次次恢复后仍能执行到期任务；重试不会忙循环，不重复执行完成 slot；存储错误与配置错误可区分；停止信号正常生效。

## S5 / P2：历史整日缺失没有进入发布待办账本

状态：**确认缺口，根因未确认。**

主库缺少 2026-09-17、18、21 的 Observation Run；这三天经安装的 XNYS calendar 确认均为交易日。collector 对应期间也没有 market/options 收盘原始包。publisher 当前却没有 unpublished 日期，因为其待办来源是“已有 capture”，不会发现“根本没有 capture”的整日缺失。

这不是当前周末不更新，也不能仅凭数据推断为某次部署或 S4 导致。当前连续健康区间从 9 月 22 日开始。

修改建议：以交易所 calendar、任务有效配置和启用区间生成 expected slots，与 completed captures、canonical rows、published runs 分层对账。标记 missing、incomplete、collected_not_published、published_not_materialized；按半日市、休市和暂停状态排除误报。对历史 gap 单独出恢复计划；可重建日线与不可重建的历史期权快照须区分，不能用今天数据伪造过去的时点。

验收：删除测试数据中的某个交易日全部采集/账本后仍能检测 missing；周末无误报；只有 API 健康但采集停滞时能检测；报告缺失不被最新日期覆盖。

## 后续容量与维护

- collector SQLite 约 6.55 GiB，intraday v2 约 574 MiB，主业务数据库约 1.78 GiB。单次大小不能推算日增长率；先记录按 kind/date 的字节趋势、磁盘余量及恢复耗时，再制定归档与保留策略。清理需要另行明确授权，不应边检查边删除。
- 本次首要性能收益点是 S2 的重复全量 JSON 校验，不是立即替换 SQLite。
- `/api/version` 当前不能明确识别已部署 Git revision；建议加入构建 SHA 和 schema revision，调度状态展示实际 sidecar 心跳及来源。
- API 的 schedule 设置与独立 sidecar 配置属于不同控制路径，应明确谁实际生效。不要把未运行的旧 backend scheduler 当成当前权威，也不要再启动它造成重复采集。

## 验证记录与交接顺序

本轮补充执行 `test_eod_scheduler_recovery.py` 与 `test_publisher.py`：**9 passed**。这些测试覆盖既有 EOD 恢复及发布行为，没有证明收盘 outcomes 链路正确，S1 的生产证据已说明这一覆盖缺口。日历异常使用纯本地 stub 复现，无生产写入。

推荐顺序：先为 S1 补行为测试并修复、用存量原始包设计幂等恢复；随后完成 S2/S4 的调度隔离和容错；再将 S3/S5 的质量与缺口对账暴露给监控。性能补丁按自身范围收尾，以上调度问题单独交付，避免把已验证的局部性能收益与尚未完成的数据链路修复混为一项验收。

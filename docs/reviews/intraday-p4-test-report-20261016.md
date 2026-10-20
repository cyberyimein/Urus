# P4 盘中事件验收记录（2026-10-16）

## 范围

本轮只验证 P4 最小契约中的后端行为：纯评估器、质量门槛、状态迁移、事件持久化幂等、源任务隔离和只读 API。测试使用确定性的 P3 形状投影和临时 SQLite；未连接 OpenD、Moomoo、SSH、生产 sidecar、生产数据库或容器，也未执行 migration、部署或后台循环。

契约来源：[P4 最小接口契约](/private/tmp/urus-intraday-p4-review-20261016/p4-contract.md)。测试文件为 [test_intraday_events_acceptance.py](../../backend/tests/test_intraday_events_acceptance.py)。

## 当前红灯证据

执行命令：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_events_acceptance.py -q
```

在开发持续接入 event service/API 的过程中，最新一次定向执行收集到 **44 项：40 passed，4 failed**。命令仍为：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_events_acceptance.py -q
```

当前剩余红灯如下：

- regular session 与 2026-11-27 半日市的 `expire_at_session_close` 尚未按交易日历迁移候选；
- health 会把实际已过期的 `last_status=ok` cursor 报成 `ok`（本地测试按当前时间构造了两小时前 cursor）；
- source 缺失或 SQLite 解析失败时，`run_once` 返回错误但没有把 `last_error` 写回 cursor；
- API 事件历史 `status` 与 `current_status` 已通过真实 candidate→confirmed 两次 repository 迁移验证。

新增红灯还固定检查了：future/非 5m bar 不得进入当前槽、缺口后只使用连续后缀、非法 spec 必须显式拒绝，以及 expiry transition 的 `evaluated_at` 不能为 null。失败不是由 OpenD、远程 API 或生产环境造成的。

## 已冻结的验收行为

- MA5 缓冲使用 `max(0.10 * ATR14[t-1], 0.0002 * C[t-1])`，规则版本为 `atr_price_buffer_v1`；ATR 缺失、零值和负值必须 unavailable。
- P3 `ma5_slope` 保持相邻 MA5 价格差。
- 单根价格跌幅严格小于 `-2 * ATR14[t-1]` 才触发异常急跌；等于边界不触发。该事件不依赖 RVOL，但必须通过数据质量门槛。
- `minute_timestamp_boundary_verified` 不是严格 `true` 时，不能发布正式 MA5/急跌事件，能力结果需说明原因。
- S1 使用同窗口 RS 的 `t` 与 `t-2` 比较，变化缓冲为 0.05 个百分点，并要求两个相邻有效槽同方向确认。
- partial 投影逐标的处理：坏成员不能屏蔽拥有完整连续窗口的 SPY；缺口会重置确认并以 `coverage_gap` 过期，不伪造为失败。
- 同一 source slot 重跑不重复写不可变事件；processor 使用不同 source path 时拒绝复用既有状态；`max_slots` 限制单次追赶范围。
- 两个并发的本地 `run_once` watcher 不重复写同一 source slot；不同 processor 的 state/cursor 可共存；source cursor 在 130 个小槽的有界追赶中保持单调且最多保留 128 个 slot identity。
- 默认 `intraday_events_enabled=False`；事件和 health GET 只读，不调用 `run_once`、不创建 sidecar、不写事件或消费游标。

## 待开发完成后复测

1. 开发完成后重新执行本文件的 44 项后端验收，记录通过/失败数及失败用例。
2. 针对新增 SQLAlchemy 模型确认事务失败不会留下半个 state/event/cursor 组合。
3. 已使用真实 `urus_moomoo.contracts.capture_contract` + `CollectorStore.write_scheduled_raw` 生成临时 sidecar；producer 的 envelope `captured_at` 与 raw row `stored_at` 有意不同，服务必须仍可读取该槽。继续补 partial source 和 latest-slot 读取证据时仍只使用临时 sidecar。
4. 在业务冻结后运行 P4 隔离 Playwright，确认事件列表筛选、冻结确认/失效线、关闭/idle/error 文案和重复刷新无写入。前端不编写单元测试。

## 隔离 Playwright 准备

已准备固定 fixture 和 runner：

- `/private/tmp/urus-intraday-p4-review-20261016/p4_fixture.mjs`
- `/private/tmp/urus-intraday-p4-review-20261016/p4_playwright_code.js`
- `/private/tmp/urus-intraday-p4-review-20261016/run_playwright_p4.sh`

runner 每次选择新的 `127.0.0.1` 端口启动 Vite，Playwright route 拦截全部 `/api/intraday/*` 请求；未知 API、非 loopback 请求、console/page error 都硬失败。fixture 包含 A/B/C/D 标的、5m 蜡烛和 MA5、三层 MACD marker、disabled/no_data/invalid_source/stale/warmup、候选→确认→失效同 setup 多序列、同 cutoff 多事件、历史 `status` 与 `current_status` 差异、同日冻结线及跨日不叠加事件。当前页面尚未接入 P4 event panel，因此 runner 暂不宣称通过，待前端接口冻结后执行。

本报告不把红灯测试通过前的页面可打开或一次 CLI 成功视为 P4 完成，也不验证真实 provider 容量或交易环境行为。

后续 P5 S4–S8 结构事件 UI 验收单独记录在 [intraday-p5-test-report-20261016.md](intraday-p5-test-report-20261016.md)，本报告不继续累积该阶段的长期结果。

## 后续复验记录（2026-10-16）

在 P5 结构策略代码接入 `app.intraday.events` 之前，重新执行 P4 验收时收集到 **47/47 passed**。随后新增了两条 S1 跳槽验收（纯 evaluator 状态链和临时 SQLite repository 持久化），并扩展了真实 RS fixture 到 index 17：

- `test_s1_gap_expires_old_candidate_before_creating_a_new_setup_identity` 使用 13、14、15 后跳过 16，再评估 17；要求旧 candidate 以 `transition_seq=2` 过期，新 candidate 使用不同 `setup_id`/`transition_seq=1`，且不得确认。
- `test_s1_gap_transitions_are_persisted_as_distinct_repository_events` 使用真实 `IntradayEventRepository.save_slot` 和临时 SQLite，要求旧 setup 的 candidate/expired 两条迁移与新 setup 的 candidate 共存，不被 `(setup_id, transition_seq)` 幂等键错误合并。

当前工作树的 P5 代码在 `events.py:260` 调用了未定义的 `_session_open_utc`。因此最新定向执行为 **49 项：13 passed，36 failed**；新增两条 S1 用例均在公共 evaluator 入口处被该 `NameError` 阻塞，尚未能对 S1 生命周期做最终绿灯判断。这是业务代码的阻塞，测试没有为其添加兼容分支，也没有修改业务文件。

前端独立验收已在业务代码冻结的前一快照完成：`npm --prefix frontend run build` 通过；Playwright 只启动本轮独占的 loopback Vite fixture，全部 `/api` 请求由固定 fixture 拦截，蜡烛/MA5、H/ΔH/Δ²H tabs 与 marker、ET 时间轴、候选过滤后的终态更新、冻结线清除、disabled/no_data/error/stale，以及 15 秒事件/60 秒行情刷新均通过硬断言。证据文件为：

- `/Users/waynewong/code/Urus/output/playwright/urus-intraday-review-20261016-p4/p4-intraday-full.png`
- `/Users/waynewong/code/Urus/output/playwright/urus-intraday-review-20261016-p4/p4-candle-ma5.png`
- `/Users/waynewong/code/Urus/output/playwright/urus-intraday-review-20261016-p4/p4-macd-markers.png`

Playwright runner 使用 `/private/tmp/urus-intraday-p4-review-20261016/run_playwright_p4.sh`，执行后确认 `127.0.0.1:5187` 无残留监听进程。上述 UI 结果是隔离 fixture 证据，不代表真实 provider 或生产环境验证。

## P5 结构事件补充验收（2026-10-16）

新增 [test_intraday_structure_acceptance.py](../../backend/tests/test_intraday_structure_acceptance.py) 目前收集 **10 项**，与 P4 定向测试合并执行结果为 **59/59 passed**：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_events_acceptance.py backend/tests/test_intraday_structure_acceptance.py -q
...........................................................              [100%]
```

新增的持久化验收使用真实 `evaluate` 生成 S5 parent candidate，再用下一根完整 bar 生成 S5 `invalidated` 与 S7 `confirmed` child，最后通过临时 SQLite 的 `IntradayEventRepository.save_slot` 写入 JSON。测试确认：

- parent 的 `parent_setup_id` 关系、`frozen_evidence` 和状态迁移被保留；
- S7 child 的 `parent_setup_id` 与 evidence 指向真实 parent；
- `payload_json`、`state_json` 可提交并读取；
- immutable `event.status` 历史为 parent `candidate → invalidated`、child `confirmed`；
- `event_statuses` 返回 parent `invalidated`、child `confirmed`，与 current status 语义一致。

S1 跳槽新增的两项 evaluator/repository 验收也已独立通过；当前 P4/P5 联合结果不包含 provider、OpenD、生产 sidecar、容器或部署操作。

## P5 生命周期与序列去重红灯（2026-10-16）

按后续审查新增 5 项参数化/序列验收，文件目前收集 **15 项**：

- S6 V/A candidate 在下一根连续 bar 使滚动 detector 不可用时，仍保留 candidate、冻结证据和生命周期，不产生 `coverage_gap`；
- S6 已 confirmed 后，即使 detector 不再成立，越过冻结 invalidation line 仍产生 `frozen_level_invalidated`；
- S8 count=9 已发出后跳过一次 source observation，再以完整无 bar gap 的 count=11 投影评估，不得重复发九转事件。

测试 fixture 使用真实连续 OHLCV，并通过公开 `evaluate` 串联 state；当前开发 WIP 曾在 `events.py:130` 以未初始化的 `freshness_reason` 计算 `lifecycle_input_available`，因此这 5 项暂时会被该 `UnboundLocalError` 阻塞。测试已通过语法检查，未添加业务兼容分支；待开发修复后再进行最终 P4/P5 联合复验。

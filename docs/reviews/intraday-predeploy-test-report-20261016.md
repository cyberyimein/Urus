# 日内功能部署前旧功能回归记录（2026-10-16）

本轮先验证待部署版本已有的 Eoflora、期权日内采集、session close、采集器恢复、
发布、额度和共享 pacing 行为。测试只使用现有测试夹具和 fake provider，没有修改
业务代码、已有测试、部署脚本或运行中的容器。

## 隔离条件

- 所有命令继承 `/private/tmp/urus-p7-network-guard-20261016/sitecustomize.py`：
  `socket.getaddrinfo` 以及 IPv4/IPv6 `connect`、`connect_ex` 会直接抛出离线错误。
- `APP_ENV=test`；`MOOMOO_ENABLED`、`FRED_ENABLED`、`YAHOO_ENABLED`、
  `ANOMALO_WORKFLOW_ENABLED`、`INTRADAY_EQUITIES_ENABLED`、
  `INTRADAY_EVENTS_ENABLED`、`SYSTEMATIC_RISK_ENABLED` 均显式为 `false`。
- `DATABASE_URL` 和 `SIDECAR_CAPTURE_DATABASE_PATH` 指向
  `/private/tmp/urus-predeploy-20261016/db/` 下的新路径；测试自身的 SQLite 也由
  pytest `tmp_path` 创建。
- collector 测试在 `urus-moomoo/` 运行，使用本地 `backend/.venv/bin/pytest`，并将
  `backend` 与 `urus-moomoo/src` 放入 `PYTHONPATH`；没有使用缺少 pytest 的
  `urus-moomoo/.venv`。

## 定向命令与结果

collector 旧功能定向回归：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
pytest -q tests/test_config.py tests/test_independent_boundaries.py \
  tests/test_eod_scheduler_recovery.py tests/test_scheduler_hardening.py \
  tests/test_publisher.py tests/test_preflight.py \
  tests/test_intraday_equities_acceptance.py
69 passed in 0.71s
```

覆盖内容包括：

- Eoflora `capture_eod_options` 的 DTE 1–45 选择、请求 tier/band/max-DTE、完整合约
  字段、canonical content hash、ready/complete 状态和无效 row 拒绝。
- IV 清洗与 backfill：midpoint 计算、provider IV 保留、低于无套利边界的 midpoint
  拒绝、无效 Greeks 不进入 ready 结果。
- `capture_intraday` fast/structure 的 raw contract、quota 不足时保留报价并延后
  历史请求、共享 provider pacing、重复 slot 和失败重试。
- session-close outcome 的最后 bar、同日校验、事务回滚、幂等写入和 recovery
  preview/apply 分离。
- scheduler 的临时日历故障退避、legacy complete capture 恢复、重复槽恢复和
  runtime completion 映射。
- publisher 的幂等发现、失败重试、状态持久化和 capture hash 校验。
- equity planner/runtime 的分组、轮换、quota、原始 SQLite 写入和 task 隔离。

backend 旧功能定向回归：

```text
cd /Users/waynewong/code/Urus
pytest -q backend/tests/test_intraday_options.py \
  backend/tests/test_history_quota.py backend/tests/test_moomoo_opend.py \
  backend/tests/test_scheduled_collection.py backend/tests/test_trading_calendar.py \
  backend/tests/test_sidecar_captures.py backend/tests/test_database_health.py
48 passed

pytest -q backend/tests/test_intraday_options_scheduler.py
3 passed
```

backend 定向覆盖了期权 fast/structure/session-close 的 adapter/service、历史额度
准入、OpenD fake adapter、scheduled collection、sidecar contract、SQLite health、
交易日历和 full-session/half-day option slot 规划。两组命令仅产生已有
`TestClient`/SQLite datetime deprecation warning，没有失败。

额度 detail identity 修复的独立复核：

```text
pytest -q backend/tests/test_quota_detail_identity.py backend/tests/test_history_quota.py
13 passed
```

新增三项确认 detail parser 只从 `code`、`symbol`、`stock_code`、`security_code`
及已知 detail 容器读取身份，不会把公司名称或 warnings 中的文本误计为标的；旧额度
计划、live admission、quota snapshot 和共享 pacing 的 10 项回归继续通过。

## 当前版本收尾回归

新增额度测试和当前开发窗口变更纳入后，在 `cwd=backend` 正确限定测试根目录执行：

```text
.venv/bin/pytest -q
526 passed
```

该命令的首次 root 级尝试曾误收集 `artifacts/`、`urus-moomoo/` 与 `docs/` 下的
同名测试，出现 collection/import mismatch；没有进入业务测试。改为 backend cwd
后收集到 526 项并全部通过。日志为
`/private/tmp/urus-predeploy-20261016/logs/backend-full-correct.log`。

随后 references 接入后的 backend 全量由 root 在同样的临时库与严格 network guard 下复核，
共 **550 passed**；日志为
`/private/tmp/urus-predeploy-20261016/logs/backend-normalized-final.log`。本报告不重复运行
该 backend 全量，后文只记录本轮 collector 与独立桥接验收。

前端只执行构建检查，没有新增 UI 单测：

```text
npm --prefix frontend run build
vue-tsc --noEmit && vite build   # passed, 84 modules
```

日志为 `/private/tmp/urus-predeploy-20261016/logs/frontend-build.log`。

## 证据与限制

定向测试日志保存在：

- `/private/tmp/urus-predeploy-20261016/logs/collector-targeted.log`
- `/private/tmp/urus-predeploy-20261016/logs/backend-targeted.log`
- `/private/tmp/urus-predeploy-20261016/logs/options-scheduler-targeted.log`
- `/private/tmp/urus-predeploy-20261016/logs/backend-full-correct.log`
- `/private/tmp/urus-predeploy-20261016/logs/frontend-build.log`

本轮证据确认 fake provider、临时 SQLite 和本地调度逻辑没有破坏旧 Eoflora 与采集器
行为。backend 全量和前端 build 已完成；collector 的早期 119 项回归已由后续归一化
存储用例扩展。当前严格 networkguard 全量结果为 141 项中 140 passed、1 failed（具体
失败见文末）。上述结果不等同于真实 OpenD 连接、真实账户额度或 provider 时间边界
验证；网络 guard 没有允许任何外部连接，也不能把本地回归解释为部署授权或生产
shadow 结果。

## 离峰股票维护红测（待业务冻结）

本轮新增独立验收文件
`urus-moomoo/tests/test_equity_maintenance_independent_acceptance.py`，先以最小公共
边界形成红灯，不修改业务实现或既有测试。用例要求：

- `maintain_intraday_equities` 使用 `pre-open` 调度，在 XNYS session open 前 90 分钟
  生成唯一维护槽；旧 `capture_intraday` 等任务的首末槽不变。
- 当前时钟距 session open 小于 30 分钟时，不得把离峰维护槽 catch-up 成补跑。
- G1/finalizer 的运行不应同步触发维护 provider 请求；维护失败应返回 pending、不得被
  scheduler 标为 completed。
- 维护已经启动但执行期间跨过开盘前 30 分钟截止线时，必须中断并回滚进度，不标记维护
  槽完成；之后的 Eoflora 槽仍可独立执行。
- 容量门控应继续拒绝或延期维护请求，并保留 pending 诊断。

在 scheduler 的 `pre-open` 实现落地后，原有边界已先独立重跑；随后追加的真实 runtime
回滚用例使当前定向结果为 **6 项：5 passed，1 failed**。失败集中在尚未接入
`CollectorRuntime.collect_task` 的 `maintain_intraday_equities` 分支，当前日志为
`ValueError: unsupported collection operation: maintain_intraday_equities`；因此真实
SQLite trace 尚未进入 DELETE，也还没有 runtime 回滚证据。

真实 runtime 用例使用临时 `CollectorStore` 的 refs-v1 过期 capture，并在首个 DELETE
的 SQLite trace callback 中把注入时钟推进到 09:01 EDT。实现完成后，它会检查所有股票
数据表的计数和内容回滚、`last_applied_date` 不写、pending 诊断保留、scheduler 不标记
维护完成，以及随后 16:00 Eoflora 仍能通过真实 scheduler/runtime 与本地 fake provider
执行。该用例与前面的 runner 传播测试分开记录；待 developer 冻结后重跑并把结果与
collector 全量区分记录。

### 20 个历史交易日 + 当前单槽性能（本地 fake provider）

为估算新窗口在有 20 个历史 XNYS 会话时的本地处理成本，使用临时 SQLite 预置
100 个股票、20 个真实交易日、每日 78 根 5m bar，然后通过真实
`CollectorRuntime`、`CollectorStore` 运行一个当前槽的 G1/G2/G3 和 finalizer。
脚本与 JSON 证据为：

```text
/private/tmp/urus-normalized-single-slot-capacity-20261016.py
/private/tmp/urus-normalized-single-slot-capacity-20261016.json
```

结果如下，时间是单次本地进程的 wall-clock；provider 是固定 fake seam，没有网络请求：

| 阶段 | 用时 | provider calls | 完成/terminal |
| --- | ---: | ---: | --- |
| G1 | 3.275 s | 1 | true / true |
| G2 | 3.143 s | 2 | true / true |
| G3 | 3.189 s | 3 | true / true |
| finalizer | 9.350 s | 3 | true / true |
| 当前槽合计（四阶段） | 18.958 s | 3 | — |

预置历史数据耗时 2.939 s。运行结束时有 156,600 条 bar revision（20 日的
156,000 条 5m 加当前槽的 600 条 1m）、14 条 capture ref、4 个 raw capture、1 条
scheduled capture 和 1 条 scheduler run。临时主库为 **117,178,368 bytes**，该次运行
观察到的 WAL 为 **2,715,112 bytes**；`measure_capacity` 的逻辑数据为
67,646,507 bytes，表与索引合计为 117,637,120 bytes。

内存观测为 tracemalloc peak **135,115,311 bytes**，进程 `ru_maxrss`
**506,806,272 bytes**。这些数值只用于离线容量和时间量级判断；它们不代表真实
Moomoo 额度，也不能替代真实 provider 的延迟、SDK 时间边界和账户限流验证。该测量
没有重跑此前耗时很长的 21 日 + 2 日完整窗口。

## 1+3+1 窗口独立全链路验收

开发窗口实现冻结后，新增独立验收文件
`urus-moomoo/tests/test_equity_windows_independent_acceptance.py`。测试直接驱动
`CollectorScheduler`、`CollectorRuntime` 和真实 `CollectorStore`，provider 只使用
本测试文件内的 fake seam；不会调用 OpenD、期权接口或共享数据库。

在同一网络 guard、临时 SQLite 和显式关闭运行开关下执行：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
../backend/.venv/bin/pytest -q \
  tests/test_equity_windows_independent_acceptance.py \
  tests/test_intraday_equity_windows.py tests/test_intraday_equity_runtime.py \
  tests/test_intraday_equity_shard_provider.py tests/test_intraday_equity_budget.py \
  tests/test_intraday_equities_acceptance.py tests/test_eod_scheduler_recovery.py \
  tests/test_scheduler_hardening.py tests/test_independent_boundaries.py \
  tests/test_publisher.py
101 passed in 0.70s
```

独立验收覆盖：

- G1/G2/G3 在 T+60/T+120/T+180 使用同一个 T cutoff，G1 只做一次全池报价，
  G2/G3 不重复报价；T+240 finalizer 不产生 quote、minute-history、quota 或期权
  provider 请求。
- finalizer 只写一条 source task 的 `scheduled_captures` 记录，并用真实 backend
  `build_intraday_projection` 解析合并后的 5 分钟合约；四个 symbol 的完整窗口、
  group role 和 source cutoff 保持一致。
- G2 缺失或 timeout 生成 partial coverage，不借用前一槽分钟线；G1 缺失也不借用
  前一槽 quotes；partial finalizer 仍为 terminal，重复 tick 不新增 provider 请求
  或 scheduled slot。
- 同一个 runtime 重试、关闭后重启 runtime 重试均保持 shard capture id 幂等；
  Eoflora 当前槽不完整以及前一槽 timeout/incomplete 时，股票窗口都记录
  `skipped_priority`。

日志保存于：

- `/private/tmp/urus-predeploy-20261016/logs/equity-window-independent.log`
- `/private/tmp/urus-predeploy-20261016/logs/collector-full-final.log`

这 101 项包括新增独立集成验收 7 个参数化结果和既有窗口/adapter/runtime/额度/
scheduler/publisher 定向回归。fake provider 只证明本地调用边界、契约合并和幂等，
不能替代真实 provider 的请求耗时、账户额度、SDK 时间边界或全量 symbol pool 容量
实测。本段回归未部署、重启、替换容器或连接生产；归一化存储变更后的最新 collector
全量结果见下文。

## 当前 collector 源码身份

本次全量回归结束后，关键源码 SHA-256 为：

```text
runtime.py                 22666a18b37178be292292f8190300ff3735fe1255d0926316c824c6e5f20f46
services/scheduler.py      a0957290c8628a06d0101580fdb16843ea8b5e28a0a91ed862c0ba24d9c74164
config.py                  be5d1e90ee70c570ed9f1c32d41dba706502a5f24c78763a268de25a3ad0dcf9
integrations/moomoo.py     01fc2a167bc13c7a757630e13ad49cd87de9a45df2d0a53d40508cb3ad30a97f
storage/sqlite.py          2e5fb90a01c504631b5dff0501f2c67b250b6be034f7cc5db729c0bcb7e49287
services/intraday_equities.py
                            972694fba1e77da79c0d26966b73cafca348496035ecbe887a24be348c4d4cab
storage/equity_history.py  50fe36b7eed26ea42e6b49b007ccdfccce12f6117d0b864b02f3e1e4ab847e0d
tests/test_equity_maintenance_independent_acceptance.py
                            446cf0060d22a8ae73ad567233d82e0b95c5e8ca10fa85a9b209d04b3b1ba448
tests/test_equity_windows_independent_acceptance.py
                            346be595782d4cae363d52aa0276c45b3849e8b4170c77c6032b45cd9a305e2d
tests/test_equity_normalized_independent_acceptance.py
                            00508d60edf334c0584e7b5e4233a4a6cbccddd4ca598dc2480a904475e8f581
tests/test_equity_history_refs.py
                            63a513bf88ba0520f32bbd2db1f1c4f6867701854032d2266eee24aa9a691a33
```

此前使用报告中声明的严格 network guard（含 `getaddrinfo`、IPv4/IPv6
`connect`/`connect_ex` 拦截）得到过 `140 passed, 1 failed in 5.73s`（141 项）的
中间结果；唯一失败是当时尚未迁移的旧 inline schema 断言。归一化引用测试和维护实现
冻结后的最终 collector 全量结果见文末“最终冻结验收”，此前 119/141 项结果不作为
最终总数。

## 同库规范化引用独立验收（2026-10-16）

本轮新增独立验收文件：

- `backend/tests/test_intraday_normalized_independent_acceptance.py`
- `urus-moomoo/tests/test_equity_normalized_independent_acceptance.py`

backend 通过 5 项：

```text
cd /Users/waynewong/code/Urus/backend
.venv/bin/pytest -q tests/test_intraday_normalized_independent_acceptance.py
5 passed
```

覆盖同一临时 sidecar 中的四张规范化表、三组 bar refs 和 quote ref，经真实 resolver
进入 P3 后再交给 P4；同库只读 loader 不写回、不把解析 rows 嵌回 raw；缺失行与较新
revision 不回退、存储 hash 损坏显式失败；历史 MACD 连续后缀和 RVOL 只使用前一日
同槽基线，当前日缓存行不污染；compact capture 出现 inline duplicate data 时拒绝合并。

collector 通过 5 项：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
../backend/.venv/bin/pytest -q tests/test_equity_normalized_independent_acceptance.py
6 passed in 4.44s
```

通过项验证了 bar 的重复写入不增加 revision、A→B→A 保留三个 watermark 版本并可按
旧 ref 读取，分片重叠 bar 只保留一个逻辑键，dry-run 不改变 2 日 1m/quote 与 21 日
5m 清理前的表和文件，apply 只删除 refs-v1 股票 raw/ledger 和过期股票数据，保留
有效 ref、legacy equities 与 Eoflora；并以 100 个 symbol、23 个本地 XNYS session
fixture（每股每 session 78 根 5m，最近两日每股各 390 根 1m）测量实际主库/WAL 与
逻辑字节，硬容量门控不阻止 Eoflora raw 写入；该容量用例运行约 4.3 秒。

容量 fixture 的实际 SQLite 计数和字节证据如下（临时数据库，100 股、23 个本地 XNYS
交易日；5m 共 179,400 根，1m 共 78,000 根，股票 bar 总计 257,400 根）：

| 项目 | 字节/数量 |
| --- | ---: |
| 主库 `collector.sqlite` | 193,392,640 bytes（184.434 MiB） |
| WAL 文件 | 0 bytes |
| `equity_bar_revisions` 行 | 257,400 |
| `equity_data_watermarks` 行 | 257,423 |
| 1m 逻辑数据 | 33,841,540 bytes（32.274 MiB） |
| 5m 逻辑数据 | 77,210,932 bytes（73.634 MiB） |
| 股票逻辑数据合计 | 111,052,472 bytes（105.908 MiB） |
| 股票表及索引合计（实现的 `measure_capacity`） | 193,314,816 bytes（184.359 MiB） |
| `dbstat`：`equity_bar_revisions` 表 | 150,999,040 bytes |
| `dbstat`：`equity_data_watermarks` 表 | 15,093,760 bytes |
| `dbstat`：`equity_capture_refs` 表 | 4,096 bytes |
| `dbstat`：`equity_quote_batches` 表 | 4,096 bytes |

上述 `measure_capacity` 结果还报告 `equity_quote_bytes=0`、
`equity_ref_bytes=0`、`equity_refs_v1_raw_bytes=0`；这组容量 fixture 专门测 bar
上限，Eoflora raw 随后仍成功写入。数字由
`/private/tmp/urus-normalized-capacity-report-20261016.json` 的一次性脚本输出，临时
数据库在脚本退出后清理；不把该临时文件或数据库作为运行时数据。

这组 23-session bars-only 数字是在 TTL `apply` 之前测量的，且没有注册 active
capture ref，所以 23 个 session 的行都还在库中；它不是“5m 保留 23 日”的结论。
实际 SQL 分项是 179,400 根 5m（100 × 23 × 78）和 78,000 根 1m（100 × 2 × 390），
合计 257,400 根。若在最新交易日执行普通 dry-run，5m 的日历窗口应保留最近 21 个
XNYS session；已有清理验收另外覆盖 active ref 的精确 revision 保护，不能把 active
ref 的保护误算成普通 TTL 窗口。

新增的 XNYS 日历边界用例故意不采集当前 21 个 XNYS session 窗口中的一个交易日，
再写入窗口外的旧 5m bar。`recent_trading_dates()` 仍返回真实日历的完整 21 个会话，
缺采集日仍占一个窗口位置，旧 bar 出现在 dry-run 清理候选中；因此缺数据不会把 TTL
窗口向更早日期延长。该用例没有替换日历 provider，也没有连接交易数据源。

为补足 bars-only fixture，本轮又用本地 fake provider 走了一次真实
`CollectorRuntime` 的完整两日窗口（不连接任何网络）：每个交易日 78 个 5m 槽，每槽
G1/G2/G3 三个分片和一个 finalizer，G1 每槽一批全池报价。命令与输出证据为：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/python \
  /private/tmp/urus-normalized-full-window-capacity-20261016.py \
  | tee /private/tmp/urus-normalized-full-window-capacity-20261016.json
```

结果是 156 个 final envelope 全部 `complete+terminal`，fake provider 调用 468 次
（2 日 × 78 槽 × 3 分片），并且实际落库：

| 项目 | 数量/字节 |
| --- | ---: |
| 1m bar revisions | 78,000 |
| 5m bar revisions | 15,600 |
| bar revisions 合计 | 93,600 |
| quote batches | 156 |
| quote rows | 15,600 |
| shard raw envelopes | 468 |
| final scheduled/raw envelopes | 156 |
| `scheduled_captures` / `scheduler_runs` | 156 / 156 |
| `equity_capture_refs` | 2,184（14 refs/slot，含三 shard 与 final 引用） |
| 1m 逻辑字节 | 39,721,950 bytes（37.882 MiB） |
| 5m 逻辑字节 | 8,023,256 bytes（7.652 MiB） |
| quote 逻辑字节 | 1,646,580 bytes（1.570 MiB） |
| refs registry 逻辑字节 | 2,169,204 bytes（2.069 MiB） |
| refs-v1 raw envelope 逻辑字节 | 6,238,385 bytes（5.949 MiB） |
| 股票逻辑字节合计 | 57,799,375 bytes（55.122 MiB） |
| 股票表及索引物理字节 | 84,918,272 bytes（80.984 MiB） |

该运行临时开启 WAL，并在每天 finalizer 完成后执行 `PRAGMA wal_checkpoint(TRUNCATE)`：
第 1 日 checkpoint 前主库/WAL 为 45,412,352 / 4,527,912 bytes，checkpoint 后为
46,202,880 / 0 bytes；第 2 日 checkpoint 前为 92,094,464 / 4,663,872 bytes，
最后 checkpoint 后为 92,164,096 / 0 bytes。因此 `measure_capacity` 最终报告主库
92,164,096 bytes、WAL 0；本轮观测到的 WAL 峰值是 4,663,872 bytes，已与 checkpoint
后的稳定文件大小分开记录。该完整窗口仍只使用临时 SQLite 和 fake provider，未把
容量结果外推成真实 Moomoo 账户请求容量。

同一脚本的 `SKIP_WARMUP=1` 对照还测了当前两日完整窗口的清理成本（这不是 21+2
warmup 结果）：156 个 final slot、2,184 个 capture refs 的
`cleanup_expired(..., dry_run=True)` 用时 **155.803 秒**。dry-run 的候选计数全为
零，前后六张相关表行数和主库文件大小完全不变，证明它没有写入；这是 ref_id 去重优化
之前的基线，耗时超过本轮约定的 45 秒门槛。优化后的重复 descriptor 定向用例已经通过，
但没有再次启动完整两日长测。21 个历史 session warmup 加两日完整 runtime 的试跑在约 10 分钟后仍停在
finalizer 对 21 日/100 股 ref 的重复 hash 扫描，已终止自有临时进程，未将其当作完成
的 21+2 容量结果。

开发随后修正了同槽 quote 重试的 canonical receipt 处理：重试未提供
`received_at` 时复用已存 batch 的 receipt，不因新的 `captured_at` 改变报价身份；相同
报价内容返回原 ref，真正内容变化仍失败关闭。当前独立窗口验收为 **7/7**，归一化存储
独立验收为 **7/7**；其中 timeout-G2 不再生成指向旧槽的 bar ref，最终 compact contract
经 resolver 可见为 partial，且不借用上一槽数据。

在同一严格离线环境下，归一化引用迁移尚未完成时曾执行过一次 collector 全量（本地
`sitecustomize.py` 拦截 AF_INET/AF_INET6 的 connect、connect_ex、getaddrinfo，且显式
关闭所有 provider/日内开关），中间结果为 **141 项：140 passed，1 failed，5.73s**，没有
网络尝试。cleanup 优化验收已通过：重复 active bar ref 只读一次，损坏 duplicate ref
按当前公开 reason contract 处理。该次唯一失败是当时仍存在的
`test_intraday_equities_acceptance.py` 仍断言旧 inline schema
`moomoo-intraday-equities-v1`，当前归一化实现正确写入
`moomoo-intraday-equities-refs-v1`；旧测试随后已迁移。未修改业务代码或既有测试来掩盖
失败。

本轮没有运行真实 provider、生产 SQLite、SSH 或部署操作；完整窗口只使用本地 fake
provider。2 日 cleanup dry-run 的已测成本仍是 **155.803 秒**（候选为零且无写入）。
重复 ref 的最小去重修复已通过定向契约测试，但尚未重新完成这项长测，因此暂不能宣称
完整 2 日 workload 已满足 45 秒门槛。13:02 一次未产出有效 JSON 的容量脚本临时目录已
消失，该次没有写入有效结果，也未计入本报告；报告中的容量数字只来自此前已完成的
`SKIP_WARMUP=1` 两日运行。

## 最终冻结验收（维护实现后）

developer 冻结维护 operation、`pre-open` 调度、DST/半日市时间计算、开盘前 30 分钟
硬截止和真实 SQLite 回滚后，按最终源码重新执行了本轮独立验收：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
../backend/.venv/bin/pytest -q tests/test_equity_maintenance_independent_acceptance.py
6 passed in 0.35s

../backend/.venv/bin/pytest -q tests/test_equity_windows_independent_acceptance.py
7 passed in 0.47s

../backend/.venv/bin/pytest -q tests/test_equity_normalized_independent_acceptance.py
7 passed in 5.19s
```

维护 6 项包含真实 `CollectorRuntime.collect_task` + 临时 `CollectorStore` 的 DELETE
回滚用例：在首个 SQLite DELETE 时推进注入时钟到 09:01 EDT，过期股票 capture、相关
表内容和计数保持不变，成功日期 meta 不写，pending 诊断保留，维护槽不完成；随后
16:00 Eoflora 仍由真实 scheduler/runtime 调度成功。其余 5 项覆盖 pre-open 90 分钟、
DST/半日市、30 分钟 catch-up 截止、G1 不同步维护和失败 pending。

窗口独立验收 **7/7**，归一化存储独立验收 **7/7**。两组测试均使用临时 SQLite 和
本地 fake provider；没有连接 OpenD、真实行情 provider 或生产数据。

同一严格离线 network guard 和显式关闭 provider/日内开关下，collector 全量最终为：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
env PYTHONPATH=/private/tmp/urus-p7-network-guard-20261016:/Users/waynewong/code/Urus/urus-moomoo/src:/Users/waynewong/code/Urus/backend \
  APP_ENV=test DATABASE_URL=sqlite:////private/tmp/urus-predeploy-final-20261016/db/fallback.sqlite \
  SIDECAR_CAPTURE_DATABASE_PATH=/private/tmp/urus-predeploy-final-20261016/db/sidecar.sqlite \
  MOOMOO_ENABLED=false FRED_ENABLED=false YAHOO_ENABLED=false \
  ANOMALO_WORKFLOW_ENABLED=false OBSERVATION_UNIVERSE_SOURCE_URL= \
  INTRADAY_EQUITIES_ENABLED=false INTRADAY_EVENTS_ENABLED=false SYSTEMATIC_RISK_ENABLED=false \
  ../backend/.venv/bin/pytest -q
157 passed in 5.88s
```

最终 collector 日志：
`/private/tmp/urus-predeploy-final-20261016/logs/collector-full-final.log`。
独立测试日志分别为：

- `/private/tmp/urus-predeploy-final-20261016/logs/equity-maintenance-independent.log`
- `/private/tmp/urus-predeploy-final-20261016/logs/equity-window-independent-final.log`
- `/private/tmp/urus-predeploy-final-20261016/logs/equity-normalized-independent-final.log`

最终运行未触发 network guard；未重复 backend 551，也未重跑 21 日 + 2 日容量长测。
collector 全量使用的关键源码和独立测试 SHA 已列于上方“当前 collector 源码身份”。

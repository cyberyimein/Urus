# Urus 股票盘中采集 P2 测试报告（2026-10-15）

本轮只准备股票盘中采集的后端红灯验收，不连接生产、Moomoo OpenD、SSH 或容器，也不读取或修改本机私有任务配置。测试使用 fake SDK/context 和 pytest 临时目录。

## 现有边界调查

当前采集器已有以下可复用边界：

- `OpenDMarketAdapter._collect_market_snapshot()` 使用 `MAX_SNAPSHOT_CODES_PER_REQUEST = 400` 分批报价，并在一次 collection run 内缓存快照。
- `OpenDMarketAdapter._history_summary()` 在 `request_history_kline()` 前调用 `HistoryAdmission.acquire()`；历史请求和批量报价共用 `CollectionCoordinator` 的 `moomoo_quote_history` pacing rate class。
- `CollectorRuntime.collect_task()` 通过 `capture_contract()` 生成 `urus.moomoo.capture.v2`，并使用 `CollectorStore.write_raw()` 写入现有原始 SQLite。
- `CanonicalIntradayStore` 当前是期权盘中 canonical schema，包含 `intraday_option_*` 表；本轮股票采集不应复用它或扩展期权表。

## 本轮最小公开契约

测试按以下最小接口编写，若开发采用不同的等价命名，应先协调后调整测试：

1. `urus_moomoo.services.intraday_equities.build_intraday_equity_plan(groups, slot_index)` 返回 `quote_symbols`、`minute_symbols`、`group_by_symbol` 和 `slot_index`。报价覆盖 A/B/C/D 全池；分钟线覆盖 A/B/C 加当前 D 轮换组。D 使用 canonical 稳定顺序的 `index % 3` 轮换；同一标的跨组只保留最高优先级（A > B > C > D）。纯 planner 不预留额度。
2. `OpenDMarketAdapter.capture_intraday_equities(quote_symbols=..., minute_symbols=..., trading_date=...)` 一次批量报价、逐分钟线标的请求历史 K 线。每次历史请求由 `HistoryAdmission.acquire()` 在请求前准入；额度不足时跳过历史请求但保留报价。返回值保留 `quotes`、`minute_bars`、`history_quota`、`minute_bar_unavailable_symbols` 和 `provider_raw.market_snapshot_batches/history_kline_batches`。
3. `capture_intraday_equities` 作为独立 runtime operation，通过现有 `capture_contract()` 与 `CollectorStore.write_raw()` 落盘；`capture_intraday` 的既有期权路径继续工作。

## 红灯证据

命令：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_intraday_equities_acceptance.py
```

初次结果：`8 failed, 1 passed`；补充边界和开发落地 planner 后复测为 `7 failed, 7 passed`。

- planner 五项现已通过；新增边界包括 task symbol scope、`US.AAPL`/`AAPL` 去重、`BRK.B` 保留和非法市场拒绝，D 轮换按稳定 `slot_index` 工作。
- adapter 五项（分批／额度降级／cutoff／部分失败／共享 pacing）因 `OpenDMarketAdapter.capture_intraday_equities` 尚不存在而失败。
- scheduler slot 注入一项因股票 operation 尚未得到稳定 `slot_index` 参数而失败。
- runtime 原始 contract 一项因 `CollectorRuntime` 尚不支持 `capture_intraday_equities` operation 而失败。
- 既有 `capture_intraday` 期权 dispatch 回归用例和 `CollectorStore.write_scheduled_raw` 同槽有界原始包用例通过。

新增测试文件：

[test_intraday_equities_acceptance.py](../../urus-moomoo/tests/test_intraday_equities_acceptance.py)

覆盖的行为包括：

- 401 个报价标的必须按不超过 400 个一批请求；分钟线逐 symbol 请求；请求使用 `K_1M` 与指定交易日；结果保留开盘第一根和收盘前最后一根分钟；不调用期权 API。
- 历史额度为 0 时不发送任何历史请求，仍保留全池报价，并说明延迟标的。
- A/B/C 每轮分钟线、D 三组轮换、稳定顺序、跨组最高优先级去重。
- 新 operation 使用现有版本化 raw capture contract 落入 SQLite。
- 现有 `capture_intraday` 期权 dispatch 不回归。

测试文件已通过 `python3 -m py_compile`；红灯命令只运行本文件，没有运行全套系统测试。

## 待开发实现后复测

开发完成后复测本文件，再针对 `test_independent_boundaries.py` 中的 quota/pacing、runtime/scheduler、intraday option 相关用例做定向回归。仍不启动 OpenD，不使用生产 SQLite，不部署或替换容器。

## P2 实现后的独立复测

开发实现 planner、股票 OpenD adapter、scheduler slot 参数和 runtime raw capture 接线后，复测命令为：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_intraday_equities_acceptance.py
```

结果：`14 passed`。

本次通过的行为包括：

- A/B/C 每轮分钟线、D 按稳定 `slot_index % 3` 轮换，重复 slot 得到同一批次；跨组标的按 A > B > C > D 去重。
- task scope 越界拒绝，`US.AAPL` 与 `AAPL` 去重，`BRK.B` 保持完整，非 US 市场代码 fail-closed。
- 401 个报价标的分成 400 + 1 两个批次；分钟线按标的一只一只请求 `K_1M`，首根和收盘前最后一根可保留。
- 历史额度不足时不发分钟历史请求，报价仍返回；单个分钟历史失败时保留其他标的并将质量标为 partial/incomplete；scheduled evidence cutoff 过滤未来分钟。
- 报价与历史请求共用 `moomoo_quote_history` pacing class，fake context 未收到任何期权 metadata/chain 请求。
- 股票 intraday scheduler 注入稳定 `slot_index`，不带旧期权 `capture_tier`/`band_percent`；runtime 用现有 `capture_contract`/`CollectorStore.write_scheduled_raw` 落原始 contract，同一 slot 的不完整重试不会无限增加 raw 包。
- 既有 `capture_intraday` 期权 dispatch 用例仍通过。

实现后的测试没有接 OpenD；fake SDK 的 provider 请求记录覆盖了批次、请求参数、准入、cutoff、失败和 pacing。相关 quota/runtime/scheduler/期权回归在下面的收尾章节完成，范围没有扩大到全仓测试。

## 收尾回归与边界复核

开发冻结后再次执行了以下本地测试：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_intraday_equities_acceptance.py
```

结果：`14 passed`。

相关采集器回归（quota/pacing、runtime/scheduler、期权 dispatch、配置和本地 preflight/publisher）使用：

```text
PYTHONPATH=src:../backend ../backend/.venv/bin/pytest -q \
  tests/test_independent_boundaries.py tests/test_scheduler_hardening.py \
  tests/test_eod_scheduler_recovery.py tests/test_config.py \
  tests/test_deploy_config.py tests/test_preflight.py tests/test_publisher.py
```

结果：`52 passed`。

后端历史额度、股票采集器外部边界和期权兼容回归使用：

```text
cd /Users/waynewong/code/Urus/backend
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_history_quota.py \
  tests/test_intraday_options.py tests/test_intraday_options_scheduler.py
```

结果：`23 passed`（仅有既有 `httpx`/Starlette 弃用警告）。股票新增模块、adapter、runtime、scheduler 和验收测试也通过 `py_compile`，仓库 `git diff --check` 无输出。

当前验收确认新增 operation 只加入代码允许列表和显式 task dispatch；本轮没有添加或修改本机私有任务配置，默认运行配置没有自动启用股票盘中采集。测试进程均在本地结束，未启动 OpenD，未访问生产 SQLite、远程 API、SSH 或容器，也未执行部署/重启/替换。

fake SDK 证明的是请求编排、批次边界、额度准入、共享 pacing、cutoff 和落盘契约；它不能证明真实 Moomoo 账户余额、OpenD 网络行为或生产服务容量。真实额度仍需在交易时段外用专用凭证和隔离环境另行验证。

## 追加边界红灯（2026-10-15）

协调验收又提出四个必须先固定的行为边界，已在同一 fake SDK 测试文件中补充红灯：

- runtime 的 scheduled slot 是权威输入。任务参数中若带有 `trading_date`、`scheduled_at` 或 `evidence_cutoff`，实现必须覆盖为调度槽，或明确拒绝冲突值，不能用 `setdefault` 静默接受伪造的时间。
- 同一个不完整 scheduled slot 重试时，如果 `write_scheduled_raw()` 返回 `False`，runtime 返回的 contract/capture id 仍必须能 `read_raw()`，并且必须和 `scheduled_captures` 槽位账本中的 capture id 一致。
- provider 分钟时间戳必须显式声明 `minute_timestamp_role=start|end`。两种口径都用 UTC aware 的 `bar_start_at`、`bar_end_at` 表示，cutoff 按 `bar_end_at <= cutoff` 过滤，同时保留原始 `bar_time`。测试分别覆盖 start 和 end，避免把未经验证的 start 默认假设写进生产语义。
- 缺失或非法 OHLC、NaN/Inf、负 volume、缺失 volume 都不能成为 `quality_state=ready`；缺字段不能以 0 静默补齐。

红灯命令：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_intraday_equities_acceptance.py
```

当前结果：`14 passed, 4 failed`。四个失败分别对应上述四项，说明验收测试能在业务修复前确实捕捉到问题。尚未复跑既有回归；待开发修复后先复测本文件，再重复此前的 quota/pacing、runtime/scheduler、期权兼容回归。全程继续使用 fake SDK 与临时 SQLite，不连接 OpenD、生产、SSH 或容器。

## 追加边界修复后的收尾复测

开发修复后重新执行同一组验收：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_intraday_equities_acceptance.py
```

结果：`18 passed`。

这次绿灯确认了：

- runtime 不再让任务参数覆盖 scheduler 提供的交易日、槽时间和 cutoff；冲突值会被拒绝或以权威槽值覆盖。
- 同槽不完整重试在 `write_scheduled_raw()` 返回 `False` 时，仍返回已落盘且与 `scheduled_captures` 账本一致的 capture contract/id。
- `minute_timestamp_role=start|end` 均显式转换为 UTC aware 的 `bar_start_at`/`bar_end_at`，只保留 `bar_end_at <= cutoff` 的分钟线并保留原始 `bar_time`。
- 非法 OHLC、NaN/Inf、负 volume 和缺失 volume 不再生成 `quality_state=ready` 的分钟线。

相关采集器回归也重新执行：

```text
PYTHONPATH=src:../backend ../backend/.venv/bin/pytest -q \
  tests/test_independent_boundaries.py tests/test_scheduler_hardening.py \
  tests/test_eod_scheduler_recovery.py tests/test_config.py \
  tests/test_deploy_config.py tests/test_preflight.py tests/test_publisher.py

cd /Users/waynewong/code/Urus/backend
PYTHONPATH=src ../backend/.venv/bin/pytest -q tests/test_history_quota.py \
  tests/test_intraday_options.py tests/test_intraday_options_scheduler.py
```

结果分别为 `52 passed` 与 `23 passed`；第二组只有既有 Starlette/httpx 弃用警告。验收测试和 P2 采集模块通过 `py_compile`，`git diff --check` 无输出。所有测试仍只使用本地 fake SDK、临时 SQLite 和临时目录，没有连接 OpenD、生产、SSH 或容器，也没有部署或重启。

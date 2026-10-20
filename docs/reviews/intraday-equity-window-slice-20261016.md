# 日内股票窗口切片本地验收（2026-10-16）

## 范围

本次完成现有 `urus-moomoo` 调度进程中的 quota 明细解析和股票日内 1+3+1 窗口切片。没有改 backend、API、私有任务 TOML、部署脚本或运行容器，没有调用真实 OpenD/provider，也没有写生产数据库。

日内新任务由当前采集器调度进程执行；G1 同时完成槽内唯一一次 quote pass 和第一组分钟线，G2/G3 分别完成另外两组分钟线，finalizer 只做本地汇总。新日内网页 GET 是只读路径，不会触发 provider 请求。本切片没有新增采集器、手动采集入口或锁服务。

## 实现

- quota detail 只从 `code`、`symbol`、`stock_code`、`security_code` 以及已知 detail 容器读取代码；公司名和未知元数据容器不会被当成额度窗口标的。`HistoryAdmission.acquire_many()` 让每个分钟分片共用一次 quota 快照，并继续应用 reserve。
- 每个 5 分钟槽固定同一个 cutoff：G1/G2/G3 分别在 T+60/T+120/T+180 开始，各有 60 秒窗口和从窗口起点计 45 秒的 provider 预算。任务不会跨窗补采；截止后可写本地终态 skip。
- G1、G2、G3 分别覆盖配置的完整且互斥的 symbol partition。G1 捕获全池报价，G2/G3 不发起 quote 请求。finalizer 在 T+240 汇总为一个 `scheduled_at=T` 的标准合约。
- shard 尝试只写各自的 `raw_captures`；`scheduled_captures` 的股票日内接口槽只由 finalizer 以 `(source_task_name, T)` 写入。运行时重启后可由确定性的 shard capture id 复用已落盘数据；T+240 finalizer 只读 raw，不调用 provider。
- Eoflora 保留原调度锚点和 end margin。当前 due 集合中的所有 `capture_intraday` 都先于股票执行；只要其中任一槽失败或不完整（包括较旧槽仍在重试、较新槽已经成功），本 tick 的股票分片就记录 `skipped_priority`。分片按 cutoff 和 shard id 幂等；缺失分片会在 finalizer 中显示 `skipped_window_expired` 和 partial coverage，不拿前一槽数据补齐。
- provider 请求 timeout 默认 3 秒、限制在 0.1–12 秒；预算和共享 pacing 计算会在请求可能越过 deadline 时停止后续 provider 工作。SDK timeout 在调用期间临时设置并恢复。
- 分钟时间戳角色只有在 provider、US 市场、K_1M、角色、交易日和 64 位 capture-check SHA256 都匹配时才标为 boundary verified；不匹配时显式保持未验证。

## 验收证据

在 `urus-moomoo/` 下通过网络连接 guard 和临时测试数据运行：

```text
PYTHONPATH=/private/tmp/urus-predeploy-20261016/siteguard:/Users/waynewong/code/Urus/backend:/Users/waynewong/code/Urus/urus-moomoo/src \
  ../backend/.venv/bin/pytest -q \
  tests/test_config.py tests/test_independent_boundaries.py \
  tests/test_eod_scheduler_recovery.py tests/test_scheduler_hardening.py \
  tests/test_publisher.py tests/test_preflight.py \
  tests/test_intraday_equities_acceptance.py \
  tests/test_intraday_equity_budget.py tests/test_intraday_equity_windows.py \
  tests/test_intraday_equity_runtime.py tests/test_intraday_equity_shard_provider.py \
  tests/test_quota_detail_symbols.py \
  tests/test_equity_windows_independent_acceptance.py
118 passed in 0.70s
```

覆盖了 quota detail 身份、1+3+1 任务计划和边界、同 cutoff、Eoflora 当前 due 集合优先级、预算到期、同进程及重启后 shard 幂等、T+240 无 provider、单一 `(source_task_name,T)` 整槽 scheduled row、真实 P3 projection 可解析、finalizer partial 合约、时间戳证据 fail-closed、共享 pacing deadline，以及既有 Eoflora、恢复、发布和 scheduler 定向回归。独立 `Runtime + CollectorStore + P3` 验收文件由 Luna 所有，7 项纳入本次合计，本次仅运行没有修改。测试使用 fake provider 和临时 SQLite；不是实时额度、OpenD 耗时或线上时间边界测量。

## 未完成项与状态

- **仍不可部署：** 21 日去重输入、原始证据保留期限和容量保护没有在本切片实现或验收。当前报告不承诺股票历史的暖机长度，也没有新增自动清理既有数据的逻辑。
- **原始容量不可据预算推定：** 每个槽会先写 G1/G2/G3 三份中间 raw，finalizer 又把分钟线和 provider raw batches 放入最终整槽 raw，因此至少存在中间包与整槽包重复保存。设计文档中的 512 MiB 原始包预算是待测目标；仅凭两天保留设想不能证明它足够，本次没有测量实际日增长或稳态占用。
- **仍未验证：** 私有任务配置没有填入实际的 `minute_timestamp_verification` capture-check hash；因此运行时即使使用 `end` 角色，也会报告 `provider_boundary_verified=false`。在真实边界证据显式配置前，不应把时间边界当作正式信号输入。
- **容量未测：** 没有真实 provider 调用，不能声称当前全量 symbol pool 能在每个 45 秒预算内完成。真实请求频率、耗时和 quota 必须分开实测。
- 21 日保留、容量/暖机、真实时间边界配置及独立最终验收完成前，相关部署开关保持关闭。此记录只证明本地切片测试通过，不是部署授权。

依用户和 root 确认，本次单一日内采集链路以同一 `urus-moomoo` 调度进程内的 Eoflora 优先和窗口预算为边界。其他独立 API/provider 路径的跨容器互斥现象未由本切片修复，也不扩展为本次日内功能的锁服务或部署门槛。

# 日内股票引用存储契约（2026-10-16）

状态：collector 侧已按用户批准实现；root 已完成 backend resolver/P3 适配。refs、TTL、容量门控的 store/runtime 定向验收通过。本契约只用于现有 `collector.sqlite` 中的股票日内数据，不影响 Eoflora 或 legacy capture。

## 四张股票专属表

```sql
CREATE TABLE equity_data_watermarks (
  watermark INTEGER PRIMARY KEY AUTOINCREMENT,
  recorded_at TEXT NOT NULL,
  change_kind TEXT NOT NULL,
  trading_date TEXT
);
CREATE UNIQUE INDEX ux_equity_session_watermark_date
  ON equity_data_watermarks(trading_date)
  WHERE change_kind = 'trading_session';

CREATE TABLE equity_bar_revisions (
  watermark INTEGER PRIMARY KEY
    REFERENCES equity_data_watermarks(watermark),
  provider TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL CHECK (timeframe IN ('1m', '5m')),
  adjustment TEXT NOT NULL,
  bar_end_at TEXT NOT NULL,
  trading_date TEXT NOT NULL,
  bar_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_capture_id TEXT NOT NULL
);
CREATE INDEX ix_equity_bar_asof
  ON equity_bar_revisions
     (provider, symbol, timeframe, adjustment, bar_end_at, watermark);

CREATE TABLE equity_quote_batches (
  task_name TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  trading_date TEXT NOT NULL,
  watermark INTEGER NOT NULL
    REFERENCES equity_data_watermarks(watermark),
  captured_at TEXT NOT NULL,
  quotes_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  source_capture_id TEXT NOT NULL,
  PRIMARY KEY (task_name, scheduled_at)
);

CREATE TABLE equity_capture_refs (
  capture_id TEXT NOT NULL,
  capture_trading_date TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('bars', 'quotes')),
  timeframe TEXT NOT NULL,
  ref_json TEXT NOT NULL,
  PRIMARY KEY (capture_id, ref_id)
);
CREATE INDEX ix_equity_capture_refs_ttl
  ON equity_capture_refs(capture_trading_date, capture_id);
```

`provider + symbol + timeframe + adjustment + bar_end_at` 是 bar 的逻辑键；同值重采不增加行。`watermark` 为全局递增的实际修订顺序。只比较当前最新版本的 canonical 内容 hash，不设 hash 唯一约束，因此 `A→B→A` 会保留三次实际版本。`bar_json` 仅存稳定规范化业务字段，不含每轮都会变化的 `observed_at`、`source_capture_id`；后两者留在 revision 元数据列。

同槽报价以 `(task_name, scheduled_at)` 唯一；报价 rows 按 symbol 排序后计算 canonical SHA256。同槽重试相同内容返回同一个 ref；同槽出现不同报价内容时失败关闭，不覆盖已发布快照。

## Capture v2 中的 refs-v1 data

`capture_contract` 外层继续使用 `urus.moomoo.capture.v2`。内层 `data.schema_version` 为 `moomoo-intraday-equities-refs-v1`。raw shard 和 finalizer 不包含 `minute_bars` 行、`quotes` 行或 provider history/snapshot rows；只保存质量状态、覆盖率、轻量请求摘要及 `equity_data_refs`。

bar ref 示例：

```json
{
  "schema_version": "moomoo-intraday-equities-refs-v1",
  "ref_id": "sha256:...",
  "kind": "bars",
  "provider": "moomoo_openapi",
  "symbols": ["QQQ", "SPY"],
  "timeframe": "1m",
  "adjustment": "none",
  "from": "2026-10-15T13:30:00.000000Z",
  "through": "2026-10-15T13:35:00.000000Z",
  "capture_trading_date": "2026-10-15",
  "asof_revision_watermark": 812,
  "row_count": 10,
  "content_sha256": "...",
  "status": "available"
}
```

每个 5m ref 的 `trading_dates` 是 XNYS 日历计算的最近 21 个 session（此前 20 日加当日），`observed_trading_dates` 是其中实际存在聚合行的日期；`from`/`through` 用 UTC ISO，`history_status` 在观察覆盖不足 21 日前为 `warming`。ref 只指向已日常采集并聚合的数据；不会每个槽重拉多日，也不承诺首日已有暖机基线。

quote ref 示例：

```json
{
  "schema_version": "moomoo-intraday-equities-refs-v1",
  "ref_id": "sha256:...",
  "kind": "quotes",
  "provider": "moomoo_openapi",
  "task_name": "equity-intraday",
  "scheduled_at": "2026-10-15T13:35:00.000000Z",
  "symbols": ["QQQ", "SPY"],
  "timeframe": "snapshot",
  "from": "2026-10-15T13:35:02.000000Z",
  "through": "2026-10-15T13:35:02.000000Z",
  "capture_trading_date": "2026-10-15",
  "asof_revision_watermark": 813,
  "row_count": 2,
  "content_sha256": "...",
  "status": "available"
}
```

`content_sha256` 对按 `(symbol, bar_end_at)` 排序的规范化 bar rows（或按 `symbol` 排序的 quote rows）进行 `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 后取 SHA256。`ref_id` 是包含 ref 描述字段的 canonical JSON SHA256，用于 registry 幂等。
Quote rows 会持久化 UTC ISO `received_at`（优先使用对应快照批次的 `response_received_at`，缺失时用 capture 时间），并按 symbol、quote code 与规范化内容排序，使 resolver 不依赖已裁掉的 provider 批次来还原接收时间。

G1 ref 包含分钟 bars、5m 历史和全池 quote；G2/G3 包含各自分钟 bars 与 5m 历史。T+240 finalizer 去重合并这些 ref；P3 可从唯一 `(source_task_name, T)` 的 `scheduled_captures` 读取整槽。缺失或 digest 不符必须返回明确 unavailable，不能回退到当前版本或其他槽。

## 只读恢复 SQL

给定 bar ref 的 watermark/range：

```sql
WITH ranked AS (
  SELECT b.*,
         ROW_NUMBER() OVER (
           PARTITION BY provider, symbol, timeframe, adjustment, bar_end_at
           ORDER BY watermark DESC
         ) AS revision_rank
  FROM equity_bar_revisions AS b
  WHERE watermark <= :asof_revision_watermark
    AND provider = :provider
    AND timeframe = :timeframe
    AND adjustment = :adjustment
    AND symbol IN (:symbols)
    AND bar_end_at >= :from_utc
    AND bar_end_at <= :through_utc
)
SELECT watermark, provider, symbol, timeframe, adjustment, bar_end_at,
       trading_date, bar_json, content_sha256
FROM ranked
WHERE revision_rank = 1
ORDER BY symbol, bar_end_at;
```

读取后按 ref 的 canonical 规则重算 row count 和 SHA256；范围内缺行、schema 不符或 hash 不同都返回 unavailable。quote ref 通过 `task_name + scheduled_at + watermark` 取单一 batch，复算 hash：

```sql
SELECT watermark, captured_at, quotes_json, content_sha256
FROM equity_quote_batches
WHERE task_name = :task_name
  AND scheduled_at = :scheduled_at
  AND watermark <= :asof_revision_watermark;
```

## TTL、容量和清理边界

- 1m bar 与 quote batches 默认保留最近 2 个真实 XNYS 交易 session；5m bar 保留最近 21 个真实 XNYS session（包含当日，周末与休市日不计）。`equity_data_watermarks` 的 session 行记录实际采集覆盖，不能代替交易日历来移动 retention 窗口。仍被最近 2 个 session 的 refs 引用的具体历史 revision 继续保留到引用过期后，因此 1m revision 的滚动边界可短暂延长。
- `dry_run` 先返回各表候选行数/IDs、保留引用及主库/WAL/股票逻辑字节；随后 `apply` 重新生成 fresh plan 并核对 hash，只删除股票新表中的过期行、引用已过期 refs-v1 captures 的 registry 行和确切关联的 scheduled/scheduler ledger。raw capture 必须同时匹配 `kind='capture_intraday_equities'` 与 `$.data.schema_version='moomoo-intraday-equities-refs-v1'`。legacy equities、Eoflora、期权、日线和共享数据不在候选范围。
- 清理只由 `maintain_intraday_equities` maintenance operation 调用。部署任务使用 `schedule="pre-open"`、`delay_seconds=5400`，运行时间为实际交易所 `session_open - 90min`，随交易所日历处理 DST 与半日市，不绑定东京固定时刻。scheduler 在 `session_open - 30min` 起拒绝启动或补跑；runtime 再按实际交易所 session open 检查，防止直接 `run-once` 绕过调度窗口。
- maintenance 在执行前持久化 `equity_retention_pending_date` 与诊断原因。整个 dry-run 和 fresh apply 使用截止检查；SQLite progress handler 与逐行循环检查覆盖长查询和候选扫描。fresh apply 在同一事务中重新计划、执行删除，并写入 `equity_retention_last_applied_date`；任何一步越过 `session_open - 30min` 都会回滚删除与成功日期，留下 pending 状态。成功日期由该事务保证同一交易日最多处理一次，成功后清除 pending 标记。
- 股票 G1/G2/G3、非分片股票采集与 finalizer 不再同步执行清理。维护缺失或失败时，股票仍照常走容量门控，并在结果中报告 `maintenance_pending=true`；该状态不阻断股票采集，也不改变 Eoflora 的任务和数据。maintenance 不调用 provider、不写 capture raw，也不触发 publisher。
- 本地任务配置由部署环境持有且未纳入仓库；本轮没有创建或修改 `collection_tasks.local.toml`，也没有改变原有六项任务定义。将股票 task 和 maintenance task 追加到真实本地配置的动作仍待部署配置 owner 执行。
- 容量统计单独返回指标 150 MiB、1m/quote/raw 512 MiB、股票逻辑总量 1 GiB 的逻辑字节、（SQLite 支持 `dbstat` 时）股票新表及索引实际分配字节，以及共享主库/WAL 文件大小。逻辑字节不包含完整 SQLite 页和索引开销，不能视为物理 1 GiB 上限；主库/WAL 总量只用于观测，因为其中包含 Eoflora 与旧数据，不用于股票超限判定。达到逻辑预算时拒绝新股票 provider 请求和写入；接近预算先停低优先历史预热与 D 组。Eoflora 不读写这些表，也不因股票容量维护而被清理或阻塞。

本轮仅在临时 SQLite 测试库运行 dry-run/apply、deadline rollback 与容量场景；没有对生产库执行清理、连接 provider 或部署。逻辑字节是预算门控口径，`dbstat` 及主库/WAL 字节用于物理占用观测。

## Collector 收尾交接

- 范围：`urus-moomoo` 的 refs-v1 四表、bar/quote 版本与引用、1m→5m 聚合、容量门控、refs-v1 shard/finalizer，以及受限 TTL 清理。backend resolver/P3 和独立 acceptance 文件由其他 owner 负责。
- 最终目标测试：collector maintenance 与受影响的 refs/runtime 定向集 **62 passed**。Luna 最终离线 collector 全量 **157 passed in 5.88s**；独立验收分别为 maintenance **6/6**（含真实 DELETE 回滚与后续 Eoflora 调度）、equity window **7/7**、normalized refs **7/7**。backend/root 另报告同库读锁目标集 **66 passed**、最终完整 suite **551 passed**；本线程未重复运行全套测试或长容量测试。
- maintenance SQLite rollback 用例通过真实 `CollectorRuntime → EquityHistoryStore → SQLite` 执行：在首条 `DELETE` 的 trace callback 推进注入时钟至 09:01 EDT，断言清理相关表内容不变、成功日期未写、pending 诊断保留、scheduler completion 未写，且 16:00 Eoflora 槽随后正常完成。另验证中断后 progress handler 已移除。
- 定向命令：`../backend/.venv/bin/pytest -q tests/test_config.py tests/test_intraday_equity_windows.py tests/test_equity_maintenance_runtime.py tests/test_equity_maintenance_independent_acceptance.py tests/test_intraday_equity_reference_runtime.py tests/test_equity_history_refs.py tests/test_equity_normalized_independent_acceptance.py`，结果 **62 passed**；涉及的 Python 源码 `py_compile` 检查通过。
- 最终源码 SHA-256：

  | 文件 | SHA-256 |
  | --- | --- |
  | `urus-moomoo/src/urus_moomoo/storage/equity_history.py` | `50fe36b7eed26ea42e6b49b007ccdfccce12f6117d0b864b02f3e1e4ab847e0d` |
  | `urus-moomoo/src/urus_moomoo/storage/sqlite.py` | `2e5fb90a01c504631b5dff0501f2c67b250b6be034f7cc5db729c0bcb7e49287` |
  | `urus-moomoo/src/urus_moomoo/runtime.py` | `22666a18b37178be292292f8190300ff3735fe1255d0926316c824c6e5f20f46` |
  | `urus-moomoo/src/urus_moomoo/services/scheduler.py` | `a0957290c8628a06d0101580fdb16843ea8b5e28a0a91ed862c0ba24d9c74164` |
  | `urus-moomoo/src/urus_moomoo/config.py` | `be5d1e90ee70c570ed9f1c32d41dba706502a5f24c78763a268de25a3ad0dcf9` |

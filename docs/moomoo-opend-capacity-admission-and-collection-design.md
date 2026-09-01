# Moomoo OpenD 容量准入与采集设计

本文定义 Urus 对个人 Moomoo/OpenD 额度、主机并发和股票/期权采集的当前实现。目标是让额度不足时系统安全等待，让已缓存数据继续可用，并让每次 provider 请求都可解释。

## 1. 运行边界

一次 `Observation Run` 是股票日线、确定性策略、期权快照和收市后对照的唯一采集入口。股票和期权适配器都通过 `MoomooCollectionCoordinator` 进入 OpenD：

```text
Observation Run
    ├─ HistoryCapacityService → HistoryAdmission → daily history
    ├─ market snapshot
    └─ OptionsCollectionService → option metadata/chain/snapshot
```

调度器只在美股 session 完成后提交一次 `post_close_observation`。它不直接调用 OpenD，也不拆分出额外采集进程。用户通过 API 触发补采时仍使用相同的服务、锁和限频。

## 2. 日线额度

### 2.1 额度快照

`HistoryCapacityService.capture()` 从 OpenD 读取 `used`、`remain`、`total`、窗口标的、采集时间和过期时间，并持久化一份 `HistoryQuotaSnapshotModel`。额度读取失败时保存 `available=false` 和 warning。

容量快照有 TTL。过期或无法读取时，新增历史请求默认 fail-closed；已经存在的 canonical bars 不会因为额度接口失败而被删除。

### 2.2 Capacity Plan

`build_plan()` 对当前启用且要求日线的 Universe 标的进行排序：

- `cache_hit`：`daily_bars` 已达到最小样本数且包含目标交易日，成本为 0；
- `admitted`：在 OpenD 窗口或当前可用额度内获得准入；
- `pending_quota`：需要新额度但可用余量不足，或额度状态不可用。

计划预留 `reserve_ratio` 和 `reserve_absolute` 安全余量，并保存 Universe content hash、quota snapshot id、目标交易日、每个 symbol 的样本数和决定原因。计划是可解释的排序结果，不是永久 reservation。

### 2.3 请求前准入

`HistoryAdmission` 在真正发起历史 K 线请求前重新确认：

1. 目标标的仍属于当前 Observation Run 的 scope；
2. 当前 quota snapshot 没有过期，或者可以安全重新读取；
3. provider 窗口和安全余量允许一次新的 distinct symbol 请求；
4. 同一运行/同一 host 没有重复提交相同历史请求。

请求成功后才把相应 `HistoryCollectionStateModel` 推进到 `acquired`，并由 `DailyEvidenceRepository.persist_collected_history()` 写入并验证 canonical `daily_bars`。失败保持 `pending_quota`、`retry_wait` 或 `partial`，不标记为成功。

## 3. 主机级锁与限频

`MoomooCollectionCoordinator` 使用 `fcntl.flock` 持有 `/data/moomoo_collection.lock`（开发环境默认为 `backend/data/moomoo_collection.lock`）。锁文件中的 JSON 只记录各 rate class 的 `next_allowed_at`，OS 文件锁负责互斥，时间戳负责跨进程重启后的节流。

当前 rate class：

| 类别 | OpenD 用途 |
| --- | --- |
| `snapshot` | 股票/ETF 批量报价 |
| `history` | 完整日线 |
| `option_metadata` | 期权标的和到期日列表 |
| `option_chain` | 到期日对应的链路 |
| `option_snapshot` | 合约快照 |

每次请求前先申请 slot，再调用 provider。锁覆盖股票和期权，因此期权采集不能与另一个股票采集同时消耗窗口。适配器内部的同运行缓存只负责去重，不改变额度审计，也不改变持久化事实。

## 4. 期权队列

`OptionsCollectionService` 根据 `options_collection_symbol_list` 去重并按配置的 DTE、strike range 和 batch size 采集。`MoomooOptionsAdapter` 使用独立的 metadata、chain、snapshot interval，但共享 coordinator 的文件锁和 pacing state。

期权不消耗历史 distinct-symbol quota；它仍受 OpenD 自己的请求频率和每次批量规模约束。每个 symbol 的采集结果只包含页面和分析需要的公开结构数据：

- option spot、expiration、DTE、IV 和 Expected Move；
- call/put/net DEX、GEX、Gamma Wall、Gamma Zones；
- Max Pain、Spot Gamma Profile 和 strike GEX sign changes；
- provider、captured_at、quality warnings 和 `data_state`。

链路计算使用完整返回范围，页面可以只展示靠近现价的执行价行。收市后对照由 `build_post_close_option_alignment()` 另行使用官方 regular close 计算：靠近 Max Pain 或 DEX Wall 只标记为结构性候选，不给出因果结论。

## 5. 状态模型

日线访问和数据质量分开记录：

| 维度 | 值 | 含义 |
| --- | --- | --- |
| access state | `not_requested` | 尚未尝试 |
| access state | `admitted` | 通过请求前准入 |
| access state | `pending_quota` | 等待额度 |
| access state | `collecting` | 请求进行中 |
| access state | `retry_wait` | 可重试但尚未到窗口 |
| access state | `acquired` | 已写入可用历史 |
| access state | `disabled` | 配置不要求采集 |
| quality state | `unknown` / `stale` / `partial` / `ready` | canonical bars 的质量 |

期权使用 `CollectionStatus` 和 `data_state` 表示 `placeholder`、`unavailable`、`succeeded` 或 `failed`。期权失败不会擦除股票证据；Observation Run 会保存期权错误并在整体状态中体现 `mixed`。

## 6. 故障行为

| 场景 | 行为 |
| --- | --- |
| quota 读取失败 | 不发起新的历史请求，保留 warning 和等待状态 |
| provider 窗口已满 | 等待下一次 Observation Run，不用旧值冒充新采集 |
| OpenD 连接失败 | 当前 symbol 标记失败，其他可完成的 symbol 继续保存 |
| 进程中止 | OS 释放文件锁；下次运行根据持久化状态重新计算 |
| JSON pacing 状态损坏 | 在锁已持有时安全恢复为空状态并继续，记录可诊断 warning |
| 期权链部分缺失 | 保留 available/partial 和缺口，不计算缺失结构的正式对照 |
| 官方收盘价缺失 | 只保留 option spot reference，不产生 near 标记 |

## 7. 配置

核心环境变量：

```text
MOOMOO_ENABLED
MOOMOO_HOST / MOOMOO_PORT
MOOMOO_HISTORY_DAYS
MOOMOO_HISTORY_QUOTA_RESERVE_RATIO
MOOMOO_HISTORY_QUOTA_RESERVE_ABSOLUTE
MOOMOO_CAPACITY_SNAPSHOT_TTL_SECONDS
MOOMOO_HISTORY_REQUEST_INTERVAL_SECONDS
MOOMOO_SNAPSHOT_REQUEST_INTERVAL_SECONDS
MOOMOO_COLLECTION_LOCK_PATH
OPTIONS_TARGET_SYMBOLS
OPTIONS_WATCHLIST_SYMBOLS
OPTIONS_TARGET_DTES
OPTIONS_SNAPSHOT_INTERVAL_SECONDS
OPTIONS_CHAIN_INTERVAL_SECONDS
OPTIONS_METADATA_INTERVAL_SECONDS
```

部署到 Mac mini 时，锁路径、SDK home、数据库和调度器状态都必须位于持久化 `/data` 挂载内；OpenD host 不能填写容器内的 `127.0.0.1`，除非 OpenD 确实运行在同一容器网络命名空间。

## 8. 测试与验收

必须覆盖：

- quota normalization、TTL、reserve 和缺失额度 fail-closed；
- cache hit 不消费新历史额度，admitted 请求成功后才推进状态；
- 两个 coordinator/process 不能同时进入 OpenD；
- 不同 rate class 独立节流，重启读取 pacing timestamp；
- 期权 adapter 按 symbol/expiration 去重且不产生历史 quota cost；
- 期权失败与股票成功共存并正确反映 Observation Run 状态；
- alignment 只接受官方 close，并正确区分 Max Pain、DEX Wall、Option Spot fallback；
- 同一 Observation Run 重试不重复写入历史或生成第二条逻辑记录。

实现入口：`backend/app/services/history_quota.py`、`backend/app/services/market_data_collection.py`、`backend/app/services/options_collection.py`、`backend/app/integrations/moomoo.py`、`backend/app/integrations/moomoo_options.py`。

# Moomoo OpenD 容量准入与串行采集设计

> 状态：已实现
> 适用范围：Urus Universe、Moomoo OpenD 历史 K 线、股票/ETF 快照、期权链、前端容量提醒
> 产品假设：单用户、单主机、单数据库、单 OpenD 账户，不做水平扩展和高可用

## 1. 目标

用户增加股票和 ETF 后，Urus 必须同时满足：

1. 不突破 OpenD 历史 K 线滚动额度；
2. 不突破快照、历史 K 线和期权链调用频率；
3. 额度不足时保留 Universe 配置，不自动停用 symbol；
4. 前端明确标记等待额度或等待重试的 symbol；
5. 规范化 `daily_bars` 真正写入并校验后才取消标记；
6. 每日数据允许串行慢速完成，不要求实时。

非目标：

- 多租户；
- 多主机并行采集；
- 通用任务平台；
- 独立常驻 worker；
- 自动交易；
- 管理 Urus 以外的 OpenD 客户端。

## 2. 核心结论

OpenD 限制分成两类：

```text
历史 K 线不同标的额度
  → cache-first 容量规划 + 安全余量 + 请求前实时复核

快照、历史 K 线、期权链调用频率
  → 单机全局锁 + 串行调用 + endpoint 间隔
```

不使用数据库任务队列、额度预留、任务租约或逐请求数据库提交。它们服务于多 worker 竞争，而 Urus 的个人部署只需保证任意时刻有一个 OpenD 采集流程。

## 3. 关键业务语义

### 3.1 日线采集范围

```text
history_symbols = enabled && collection.daily_history
```

它独立于：

- `roles.equity_watchlist`；
- `roles.ai_candidate`；
- `instrument_symbols` 的旧指标范围。

新增股票或 ETF 默认可以开启日线和期权；是否属于指标推荐列表不决定是否采集历史 K 线。

### 3.2 Universe 与运行状态分离

Universe 版本保存用户意图，例如 enabled、daily_history、options。额度不足不能改写这些字段。

`history_collection_states` 保存可变运行状态。前端将二者投影在同一页面，但不会把运行状态写回 Universe 版本。

### 3.3 额度状态和数据质量分离

`access_state`：

- `not_requested`：尚未规划；
- `admitted`：容量允许，本轮将串行采集；
- `pending_quota`：达到安全余量，暂不调用 OpenD；
- `collecting`：已经通过请求前复核，正在采集；
- `retry_wait`：请求失败、数据未持久化或目标日期未达到；
- `acquired`：规范化日线已写入；
- `disabled`：Universe 已关闭每日历史采集。

`quality_state`：

- `unknown`；
- `stale`；
- `partial`；
- `ready`。

例如短历史可以是 `access_state=acquired`、`quality_state=partial`，不能重新解释成额度不足。

## 4. 总体流程

```text
定时任务 / 手动运行 / Observation / Daily Evidence 补数
                         │
                         ▼
           获取 host-wide moomoo 文件锁
                         │
                         ▼
                查询 OpenD 实际额度
                         │
                         ▼
       读取 daily_bars，生成确定性容量计划
                         │
                         ▼
       对允许的 symbol 逐只执行请求前复核
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
       达到安全余量             容量仍然允许
       pending_quota             串行请求历史 K 线
                                     │
                                     ▼
                        写入并验证 daily_bars
                                     │
                                     ▼
                              acquired / partial
                         │
                         ▼
         串行采集期权链与合约快照并持久化
                         │
                         ▼
                   释放文件锁
```

第二个并发流程会等待文件锁。拿到锁后重新读取额度和缓存，因此不会沿用等待期间的容量判断。

## 5. 持久化设计

只新增两张表。

### 5.1 `moomoo_history_quota_snapshots`

只保存每个 provider/quota kind 的最新快照，不保留无界审计历史。

主要字段：

| 字段 | 说明 |
|---|---|
| `id` | 确定性主键 `moomoo_openapi:history_candlestick` |
| `provider` | `moomoo_openapi` |
| `quota_kind` | `history_candlestick` |
| `available` | 是否成功读到额度 |
| `used_quota` | 已使用不同标的数 |
| `remain_quota` | 剩余额度 |
| `total_quota` | 总额度 |
| `detail_json` | SDK 原始明细及窗口 symbol |
| `quality_status` | `ok` 或 `unavailable` |
| `warning` | 查询失败说明 |
| `observed_at` | 读取时间 |
| `expires_at` | 前端新鲜度截止时间 |

唯一约束为 `(provider, quota_kind)`。每次查询覆盖同一行。

### 5.2 `history_collection_states`

按 provider 和 symbol 保存前端需要的运行状态。

主要字段：

| 字段 | 说明 |
|---|---|
| `provider`, `symbol` | 唯一业务键 |
| `access_state` | 容量和采集进度 |
| `quality_state` | 日线质量 |
| `reason_code`, `message` | 稳定原因和用户提示 |
| `desired_history` | 当前 Universe 是否仍需要日线 |
| `universe_version_id` | 最近应用的 Universe 版本 |
| `capacity_snapshot_id` | 最近容量判断引用 |
| `bar_count`, `latest_bar_date` | 规范化缓存摘要 |
| `required_through_date` | 本轮要求覆盖的交易日 |
| `minimum_bar_count` | 策略所需最少日线数 |
| `quota_cost` | 本轮预计是否占用新 symbol 槽位 |
| `first_deferred_at` | 首次等待额度时间 |
| `last_attempt_at`, `last_success_at` | 采集时间 |
| `updated_at` | 状态更新时间 |

不保存 reservation、lease owner、attempt counter 或 row version。

## 6. 容量计划

### 6.1 输入

- 候选 Universe items；
- OpenD 当前额度；
- 当前滚动窗口 symbol 明细；
- `daily_bars`；
- 最近完整交易日；
- `daily_min_history_bars`；
- 安全余量配置。

### 6.2 安全余量

```text
reserve = max(
  moomoo_history_quota_reserve_absolute,
  ceil(total_quota * moomoo_history_quota_reserve_ratio)
)

spendable = max(0, remain_quota - reserve)
```

如果 total 或 remain 无法读取，新增 symbol 必须 fail closed。已经存在于 OpenD 滚动窗口的 symbol 可视为零新增成本，但仍需通过实时额度读取确认窗口明细。

### 6.3 cache-first 判定

symbol 满足以下条件时为 `cache_hit`，不得请求 OpenD：

```text
bar_count >= daily_min_history_bars
AND latest_bar_date >= required_through_date
```

有数据但样本不足或日期落后为 stale，需要刷新。

### 6.4 分配顺序

需要新槽位的 symbol 按稳定优先级排序：

1. market benchmark；
2. equity watchlist；
3. AI candidate；
4. 其他 symbol；
5. 同级按 Universe 顺序和 symbol 稳定排序。

安全余量外的候选标记为 `pending_quota`，但 Universe 保存仍然成功。

### 6.5 预览不是授权令牌

保存前 Capacity Plan 只用于解释影响。Universe PUT 会重新计算，采集运行前还会再次读取额度。

不使用 plan ID 预留槽位，也不承诺预览中的 admitted 一定能够在未来某个运行中采集。最终权威判断始终发生在 OpenD 请求之前。

## 7. 运行前准入

每次可能调用 `request_history_kline` 时：

1. 先检查本轮和规范化缓存；
2. 确认 symbol 状态不是 disabled/pending；
3. 重新读取 OpenD quota；
4. 如果 symbol 已在滚动窗口，允许零成本刷新；
5. 否则要求 `remain > reserve`；
6. 通过后将状态设为 `collecting`；
7. 未通过则设为 `pending_quota`，不触碰历史接口。

不能读取 quota 时，新历史请求 fail closed。读取缓存不受影响。

由于整个流程持有 host-wide 文件锁，不存在两个 Urus worker 同时消费同一份 remain 的情况，因此无需额度 reservation。

## 8. 单机锁和调用频率

`MoomooCollectionCoordinator` 使用配置项：

```text
moomoo_collection_lock_path = data/moomoo_collection.lock
```

文件使用 `flock(LOCK_EX)`：

- 一个 workflow subprocess 持有时，其他 Urus 采集流程等待；
- 进程异常退出后，操作系统自动释放锁；
- 锁文件内仅保存每个 rate class 的 `next_allowed_at`；
- 进程重启后仍不会立即穿透上一请求窗口；
- 不在数据库事务中 sleep，也不逐请求 commit 限流状态。

当前 rate class：

| rate class | 最小间隔 | 用途 |
|---|---:|---|
| `moomoo_quote_history` | 0.51 秒以上 | 快照和历史 K 线共享保守节奏 |
| `moomoo_option_chain` | 3.05 秒以上 | 不超过 10 次/30 秒 |

配置默认值保留更宽松余量：历史/快照 0.55 秒，期权链 3.5 秒。

期权链的单次日期跨度不得超过 30 天。大于 30 天的目标范围必须切片后串行执行。

## 9. 写入成功与自动清标

OpenD 返回成功不能直接把状态设为 acquired。唯一事实来源是规范化 `daily_bars`。

同一数据库事务中：

1. `DailyEvidenceRepository.upsert_bars()` 写入规范化日线；
2. 重新读取该 symbol 的 bars；
3. 更新 `bar_count` 和 `latest_bar_date`；
4. 若最新日期早于 required target，设为 `retry_wait/stale`；
5. 否则设为 `acquired`；
6. bars 少于 minimum 时设 `quality_state=partial`，否则 `ready`；
7. commit 后前端下一次轮询自动取消等待额度标记。

Workflow 的 3A 临时 persistence payload 会在释放 Moomoo 锁前同步进 `daily_bars`，防止紧接着启动的另一个流程重复补数。最终 snapshot 持久化仍可幂等执行相同同步。

## 10. 期权串行采集

期权不占历史 K 线不同标的额度，因此不进入 Capacity Plan。

流程直接将配置的 `option_symbols` 交给 `MoomooOptionsAdapter`，adapter 内部：

1. 按 symbol 顺序处理；
2. expiration 查询按最多 30 天切片；
3. 每次 option-chain 调用通过 coordinator 等待至少 3.05 秒；
4. 合约快照按最多 400 个 code 分批；
5. 快照调用使用 quote/history rate class；
6. 部分 symbol 失败时返回明确 unavailable/partial，不创建持久化任务重试状态；
7. 下一次每日运行自然重试缺失数据。

这是个人应用的“队列”语义：有序列表串行处理、允许等待，而不是独立数据库任务系统。

## 11. API 与前端

### 11.1 API

- `POST /api/settings/universe/capacity-plan`：保存前预览；
- `PUT /api/settings/universe`：保存 Universe 并重新计算状态；
- `GET /api/settings/universe/history-status`：读取最新容量和逐 symbol 状态；
- `POST /api/settings/universe/history-capacity/refresh`：主动读取额度并重新规划。

Universe active response 增加：

- `capacity`；
- `collection_states`。

Universe 历史版本不混入当前运行状态。

### 11.2 前端

保存确认页展示：

- 总额度、已使用、剩余和安全余量；
- 本地缓存已就绪数量；
- 新增槽位数量；
- 当前允许采集数量；
- 等待额度数量和 symbol 清单。

Universe 表格展示：

- 等待日线额度；
- 本轮串行采集中；
- 日线稍后重试；
- 日线已就绪；
- 日线已取得但样本偏短。

页面存在未完成状态时每 30 秒轮询一次；页面不可见或卸载后停止。刷新失败继续显示最后一次权威状态，不打断编辑。

## 12. 故障处理

| 场景 | 行为 |
|---|---|
| OpenD quota 不可读 | 新历史请求 fail closed，缓存仍可用 |
| 历史请求失败 | `retry_wait/history_provider_error`，下次运行重试 |
| 返回零 bars | 不清标，保持 retry_wait |
| bars 日期未达到目标 | `retry_wait/history_target_not_reached` |
| bars 样本偏短 | `acquired/partial` |
| Workflow 崩溃 | OS 释放文件锁；仍为 collecting 的 symbol 下次运行重算 |
| 两个流程同时启动 | 第二个等待锁，随后重新读 quota 和 cache |
| 外部程序同时使用 OpenD | 不在 Urus 保证范围内，安全余量降低风险 |

## 13. 必须保持的测试

后端：

1. overflow symbol 保持 enabled 且标记 pending；
2. quota 不可读时 provider history 调用次数为零；
3. 每次历史请求前重新读取 live quota；
4. canonical bars 写入前不清标；
5. latest bar 未达目标时保持 retry_wait；
6. 短样本为 acquired/partial；
7. quota capture 始终只有一条最新记录；
8. coordinator 跨重启保留下一允许时间；
9. option-chain 最小间隔和 30 天切片；
10. 空库 Alembic upgrade head 与 ORM-first upgrade 都通过。

前端：

1. 保存前调用 Capacity Plan；
2. pending symbol badge 可见；
3. 状态轮询后 acquired 自动消失等待标记；
4. API 新字段缺失时保持向后兼容；
5. TypeScript build 通过。

## 14. 运维边界

该设计只保证单主机上的 Urus 进程。若未来出现以下任一需求，才重新评估持久化队列或分布式锁：

- 多主机部署；
- 多 OpenD 账户；
- 采集任务需要独立于 workflow 生命周期执行；
- 需要任务级 SLA、人工重放或完整审计历史；
- 单次采集已经长到不能接受同步完成。

在这些条件出现之前，不为假设中的 worker、租约恢复或水平扩展增加基础设施。

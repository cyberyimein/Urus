# 日内观察部署前生产只读检查

日期：2026-10-16，日本时间晚间；本次检查从 UTC 10:31 开始（美东开盘前）。用户本轮条件授权部署：先测旧功能，特别 Eoflora，核算额度，全部满足才部署。本记录不代表已部署。

## 当前运行与数据

- Urus API/UI：`urus-ui-group-links-20261007-CtTuFI`，8 CPU／6 GiB，宿主 7777。
- 独立采集器：`urus-moomoo-scheduler-20261004-nMm7M9`，4 CPU／2 GiB。
- Eoflora：`eoflora-current`，只读挂载 Urus 的 intraday_options、urus-moomoo 输出，宿主 8080。本次不替换 Eoflora。
- Urus `/api/health` JSON 为 production、status=ok、database=ok。
- `/api/intraday/overview`、`/api/systematic-risk/overview` 在旧版得到的是 SPA fallback，不能把 HTTP 200 当新 API 已上线。Eoflora `/health` 和 `/api/health` 也返回 HTML，不能当作业务健康端点。
- 持久化根目录 `/Users/mithridates/data/urus` 实际指向 `/Volumes/DOCK/data/urus`。collector.sqlite 约 10,212,335,616 字节，既有期权事实库约 897,925,120 字节；这不是新增股票功能的数据量，不授权按股票保留策略清理整个共享库。

## 既有 Eoflora 输入与消费

2026-10-15 EOD capture `a18b0c5a-d1e7-47ca-a6f5-a28a998c241f`：

- 21:01:12 UTC 保存，ready=true、complete=true。
- source 7,428 条、retained 7,416 条，保留率约 99.84%。
- IV 回填尝试 698、成功 686、失败 12；无缺失必需标的，无缺失双边到期日。失败记录经既有质量过滤，不等同整包失败。
- SPY、QQQ、IWM 各 390 根分钟线，共 1,170 根。
- 最后日内 capture（19:55 UTC）complete=true；收盘 finalizer 已保存。
- canonical fast/structure 最近记录 succeeded，但 fresh_quote_coverage 低于完整返回覆盖率；complete 不代表每条报价新鲜，不扩大健康结论。
- Eoflora `published/eod-close-v1/latest.json` run_date=2026-10-15、generated_at=2026-10-15T21:01:51Z、status=ok；昨日 `close_plus_60.json` status=eod_ready。
- 另有昨日 open_plus_30 与 close_minus_60 的既存 conflict 文件，原因是同逻辑锚点 selected attempt／content hash 改变。这是部署前已存在的来源身份冲突；没有修改、重跑或覆盖。最新 EOD 发布正常不能证明所有盘中锚点无问题，后续应独立核对。

## 实时额度

在现有采集 `collection.lock` 下非阻塞获取互斥锁，只调用一次 `get_history_kl_quota(get_detail=True)`，未改订阅、未请求新代码：used=46、remain=54、total=100。以 detail 的 code 字段为准，当前启用 Universe 的 46 个代码均在该窗口内，包括全部 15 个既有 market_symbols 和 Eoflora 的 SPY/QQQ/IWM。

- 默认安全缓冲 `max(ceil(100×20%),10)=20`。
- 若其他高优先级待用代码数为 0，当时可新增代码上限为 `54−20=34`；不是长期保证，实际请求前必须重新准入。
- 当前 46 股盘中请求理论新增历史代码额度为 0。请求频率、网络耗时和历史代码额度分开验收，分成三组不减少代码并集。
- 46 股每轮基础约 46 个分钟历史首请求＋1 个合批快照；若 78 个收盘槽均覆盖，约 3,666 个基础调用／日。逐请求 quota 探测、分页、重试、日历及 Eoflora 请求另计；不能只用 pacing 推算每窗容纳量。
- 线上 Universe 页面缓存的 quota 快照为 9 月旧数据，不用作本次准入依据。本次没有刷新或写回该缓存。

发现并本地修复额度明细解析缺陷：原递归把 name／公司名也当作代码，可能误判窗口复用。backend 新增 3 条测试先红后绿，连同旧额度测试共 13 条通过；collector 对应修复由执行 agent 完成并单独验收。未知字段应保守忽略，不以公司名放行代码。

## 真实分钟边界核验

使用已在额度窗口内的 SPY，只读请求昨日 regular session 的 1m 和 5m、AuType.NONE，各一次；为诊断成交量差异又各读一次，总 4 次历史请求，未写入生产 capture/数据库。

- 1m：390 根，09:31..16:00；5m：78 根，09:35..16:00。
- 按结束时间语义 `(T−5m,T]` 聚合，78/78 根的 O/H/L/C 全部与 provider 5m 一致。
- volume 并非逐股完全相等，样例 1,469,110 vs 1,469,111；1,042,332 vs 1,042,334。不能将其当成价格边界失败，也不能宣称所有字段精确相等。
- 验证支持当前 US regular、SPY 的 end 语义，不涵盖其他市场、盘前盘后或半日市。新股票通道必须显式设置 end 并绑定已验证范围，不能沿用默认 start 或无依据全面开启验证标记。
- 官方历史接口说明确认美股 time_key 为美东时间，但未给出足以代替此次实测的开／闭边界保证：[Futu 历史 K 线文档](https://openapi.futunn.com/futu-api-doc/en/quote/request-history-kline.html)。

## 部署门槛（当前未全部通过）

1. 1＋3＋1 分片、预算、共享锁／限频、Eoflora 跨窗让步、相同 cutoff 与 partial／重启幂等完成实现及独立验收。
2. 新股票通道时间边界配置完成；历史数据与实时输入的复权／来源一致。
3. 21 日去重输入、2 日原始证据及容量保护完成实现；不得把文档预算冒充已运行的保留机制，也不自动删除既有生产数据。
4. 事件消费者运行入口可监管：root 补显式 INTRADAY_EVENTS_ENABLED 时才启动 watch、API 健康后启动、崩溃失败和退出清理；先测试 2 红 1 绿，再实现 3 绿，默认 API-only 保持。最终完整回归仍需覆盖。
5. 最终源码冻结后独立 collector/backend 完整回归、前端构建、隔离 UI 验证与 migration 预演通过。部署须备份数据库、保留现有配置和回退容器，不运行会重建旧任务配置的通用脚本覆盖私有 TOML。

截至本记录，未停止／重启／替换任何运行容器，没有修改生产任务、数据库、环境或删除数据。

本地独立回归更新：backend 当前全量 526/526、frontend 构建 84 modules 通过；collector 旧功能定向 69/69、backend关键48/48、期权调度3/3通过。collector 新分时切片最终全量仍待冻结。详见 [独立测试报告](intraday-predeploy-test-report-20261016.md)。

## 补充：跨容器互斥实测与范围纠正（非本次日内阻断项）

API 有效环境使用 `/data/moomoo_collection.lock`，collector 默认使用 `/data/urus-moomoo/collection.lock`，原本就不是同一路径。两者锁状态格式虽兼容，统一路径也不足以修复：root 在 collector 内打开现有 `/data/urus-moomoo/collection.lock` 并持有 `fcntl.flock(LOCK_EX)` 3 秒，打印 `LOCK_HELD` 后，立即在 API 容器对完全相同的挂载路径尝试 `LOCK_EX|LOCK_NB`，返回 `CROSS_CONTAINER_LOCK_NOT_BLOCKED`。该探测未写文件内容、未请求行情、未改配置。随后以 5 秒持锁并记录时间戳复核：holder_locked_at=1792147696.868，API probe_acquired=true at=1792147697.020，探测耗时 0.162 秒且持锁进程仍在运行，holder_released_at=1792147701.871；排除探测发生在锁释放之后。

因此当前不能证明 Apple Container 独立 VM 间的 flock 互斥有效，不把同一宿主挂载等同于同一内核锁。Eoflora 与股票由同一个 collector 进程调度仍可串行，但 API 发起的 provider 请求可能与它重叠，跨容器额度／pacing 的承诺不成立。该问题属于其他采集入口的既存协调风险，不把它扩展成新日内链路的必修范围。

磁盘可用空间检查：外置数据卷约 795.67 GiB，启动盘备份目录所在卷约 57.13 GiB。当前容量不足不是部署阻断原因。

### 用户澄清后的实际部署范围

用户明确日内没有手动采集入口。新股票日内任务与 Eoflora 数据生产都在现有 urus-moomoo 容器、同一个调度进程；新日内网页 GET 只读，不调用 provider。root 此前将其他功能的手动采集路径与日内混谈，属于范围扩大，现已纠正。跨容器 flock 现象仍是事实记录，但不作为本次日内功能的部署阻断项；不新增采集容器、手动入口或锁服务。部署重点回到同一采集进程内的 Eoflora 优先、1＋3＋1 窗口、真实 quota 与存储预算。本功能不承诺解决其他功能独立主动采集时的全局并发协调。

分钟边界比较结果已冻结为 [JSON 报告](intraday-minute-boundary-check-20261016.json)，文件 SHA256 `fc2ffcc0d22414ea08aa93e3933936b9c765476c57df44f865e8239a130251c3`。这是实际比较结果的身份，不冒充未保留的完整 raw capture；尚未写入生产私有配置。


## 真实三组请求耗时（07:27 EDT 只读探测）

在现有 collector 的同一 collection.lock 下非阻塞探测；忙则退出。先确认全部 46 个代码已在实时 quota detail，再请求昨日 2026-10-15 regular session 的 K_1M／NONE（max_count=1000），组内 0.6 秒节流、单次 SDK 3 秒 timeout；仅 G1 一次全池快照。不写 raw、ledger、行情库或配置，不改变订阅；连接结束释放锁。

| 组 | 股票数 | history 请求 | 返回分钟行 | 耗时 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| G1 | 15 | 15 | 5,850 | 12.661 秒 | 完整，快照 46 行 |
| G2 | 16 | 16 | 6,240 | 11.818 秒 | 完整 |
| G3 | 15 | 15 | 5,850 | 11.291 秒 | 完整 |

没有 history 失败或分页需求；历史额度 used=46／remain=54，新增代码 0。该测量证明现有条件下三组明显低于 45 秒预算，不是开盘网络时延保证；实际运行仍须按 deadline 跳过超时请求，不挤占 Eoflora。快照与额度查询是此次 probe 的额外请求，非一日完整调度压力测试。

冻结证据：[三组请求报告](intraday-provider-group-timing-20261016.json)。后续 full local backend 回归已更新至 550 项通过、前端 84 modules 构建通过；以最新 [归一化验收](intraday-normalized-storage-acceptance-20261016.md) 为准。

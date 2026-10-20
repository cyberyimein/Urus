# 日内同库去重与引用存储：最终本地验收

日期：2026-10-16。协调验收 root；collector 开发为用户指定 chat；独立验收 luna-builder。

**历史结论（2026-10-16）：本地验收通过，当时遵守临近开盘禁令，未部署。后续更新：用户于收盘后重新明确授权，2026-10-17 JST 部署完成；当前状态见 [收盘后部署记录](intraday-deployment-20261017.md)。下文运行环境状态保留为当时的验收快照。**

## 最终实现

- 继续使用既有 urus-moomoo 调度进程和 collector.sqlite，不新增采集容器或手动采集入口，网页只读。
- 同一股票／来源／周期／复权／bar 时间按逻辑键去重；相同数据不重复写，真实修订追加 watermark。G1/G2/G3 和最终整槽仅保存引用与覆盖信息，不再复制分钟行或报价。读取验证当时的版本、行数和 hash；缺失或过期显式不可用，不借用最新数据。
- 股票仍使用 1＋3＋1：Eoflora 优先，T＋60／120／180 秒三组，T＋240 秒只做本地整槽合并，第五分钟没有股票 provider 请求。超时分片不引用旧槽数据；忙、失败待重试或预算不足时让步，不跨窗追赶。
- 1m／quote／原始引用证据保留 2 个真实 XNYS 交易日，指标输入保留 21 个会话，活动引用所需的具体版本保护到引用过期。事件沿用现有应用库，按 2 会话清理并保护活动 setup。legacy、Eoflora、期权和日线不进入新股票清理范围。
- MACD 用连续历史预热，支持 H／一阶／二阶变化；MA5、ATR 与 RVOL 使用同一份有效 5m 输入。RVOL 仅取此前 20 日同槽，至少 10 日有效样本；历史不足不造数、不输出正式 MACD 信号。不自动无界回补历史。
- 清理移到同一 collector 的本地 `maintain_intraday_equities` 任务，`pre-open`＋`delay_seconds=5400` 随交易所实际开盘时间运行。开盘前 30 分钟起禁止启动／补跑；执行中跨截止会中断查询、回滚删除与成功日期，保留 pending。股票窗口和 finalizer 不再同步清理，只做容量门控。
- 只读引用恢复不持有覆盖整个 hash 计算的长事务，避免 SQLite DELETE journal 模式下阻塞 Eof 提交；不改变生产 journal_mode。

## 最终测试与身份

| 项目 | 结果 |
| --- | --- |
| backend 严格离线全量 | **551 项通过**，exit 0 |
| collector 严格离线全量 | **157 项通过**，5.88 秒 |
| 独立窗口验收 | 7／7，包含缺组、超时、重启和旧槽不复用 |
| 独立 normalized 验收 | 7／7，包含真实 Scheduler→Runtime→SQLite→backend P3/P4 |
| 独立维护验收 | 6／6，包含真实 DELETE 回滚和后续 Eoflora 执行 |
| frontend vue-tsc＋Vite | 通过，84 modules；本轮后续未改前端 |
| git diff --check | 通过 |

真实回滚测试在首条 DELETE 的 SQLite trace callback 中把注入时钟推进到 09:01 EDT，验证股票表内容与计数回滚、成功日期未写、pending 保留、维护槽不完成；随后 Eoflora 仍由真实 scheduler/runtime 调度成功。该证据不同于仅返回 rolled_back 字段的 fake runner。

同库写锁测试先复现 `database is locked`，移除不必要的只读长事务后 66 项目标回归转绿，随后包含在 backend 551 项中。引用 hash 校验期间，第二 SQLite 连接可提交 `capture_intraday` raw。

关键源码 SHA256 与独立报告一致：

| 文件 | SHA256 |
| --- | --- |
| backend references.py | a64da9ec0bd011cf13d873a7d7d696a7432977f17252576b9486e3bb5c9c7c0a |
| collector runtime.py | 22666a18b37178be292292f8190300ff3735fe1255d0926316c824c6e5f20f46 |
| collector storage/equity_history.py | 50fe36b7eed26ea42e6b49b007ccdfccce12f6117d0b864b02f3e1e4ab847e0d |
| collector storage/sqlite.py | 2e5fb90a01c504631b5dff0501f2c67b250b6be034f7cc5db729c0bcb7e49287 |
| collector services/scheduler.py | a0957290c8628a06d0101580fdb16843ea8b5e28a0a91ed862c0ba24d9c74164 |
| collector config.py | be5d1e90ee70c570ed9f1c32d41dba706502a5f24c78763a268de25a3ad0dcf9 |

## 请求额度与性能

07:27 EDT 的真实只读 probe 确认当前 46 个代码都已在额度窗口：used=46、remain=54，总计100；新增代码0。保留20缓冲时，当时额外可新增最多34，后续启用前必须刷新，不能当作永久余额。

| 真实请求组 | 股票数 | 昨日 K_1M 返回行 | 耗时 |
| --- | ---: | ---: | ---: |
| G1＋一次全池快照 | 15 | 5,850 | 12.661 秒 |
| G2 | 16 | 6,240 | 11.818 秒 |
| G3 | 15 | 5,850 | 11.291 秒 |

全部完成，无 history 失败或分页需求；均在45秒请求预算内。46股每轮基础46个分钟请求加1个快照，78槽约3,666调用／日；quota探测、分页、重试及Eof请求另计。分组减少短时请求密度，不减少 distinct-code 额度。

另用100股、此前20个真实XNYS会话5m＋当前一槽测量本地处理：G1/G2/G3 为3.275／3.143／3.189秒，finalizer为9.350秒；RSS峰值506,806,272B（约483MiB）。该数不包含真实provider耗时，不把本机fake-provider测量当作生产开盘延迟保证。

## 容量证据与限制

| 隔离 workload | 实测 |
| --- | --- |
| 100股、23日5m＋2日1m，仅bars | 主库193,392,640B，约184MiB；不含quote/ref/raw |
| 100股完整两日156槽，含3 shards／槽、final、quote、refs、ledger | 主库约92.16MB（约88MiB），WAL峰约4.7MB |
| 100股此前20日5m＋当前完整一槽 | 主库117,178,368B，WAL峰2,715,112B；股票逻辑67,646,507B |

上述是三组不同fixture，不能声称已完成统一21＋2全窗口稳态实测。长测未完成的尝试已排除证据；没有为了得到全绿结果省略失败项。以两组独立量级相加作保守规划约273MiB，会重复计入bars和索引，也不等于实测统一落盘量。

指标150MiB／raw512MiB／股票逻辑总量1GiB为运行门控口径；SQLite表／索引实际页和共享主库/WAL另计。原约9.5GiB共享库主要是旧数据，不归入股票超限或清理范围。旧两日清理dry-run基线155.803秒，因此已改为离峰维护；未重新宣称优化后的全两日清理可在45秒内完成。

## 运行环境状态与未来启用前提

禁止部署前已经完成：API约2.15GB、collector约10.21GB的一致性备份，quick_check均ok；API备份副本在无端口隔离候选容器中完成0026→0027迁移，Universe、groups、昨日collection-plan及新overview均200 JSON，原/data只读。原始备份未覆盖；没有迁移生产库。

原有API和collector未停止或替换，生产私有TOML未修改，生产数据未清理。禁令之后停止全部远端操作，仅做本地开发和隔离测试。root已在本地生成配置候选并通过结构比较：原runtime与6个任务完全不变，仅追加股票和开盘前维护两项；候选未上传或应用。

先前镜像／归档是阶段性产物，不含最终全部修复，不可直接启用。后续必须等待用户重新明确部署指示，再按最终源码和届时的现网状态更新构建、额度、备份与回退检查；不得恢复使用已撤回的许可。

详见 [独立测试报告](intraday-predeploy-test-report-20261016.md)、[引用契约](intraday-equity-reference-contract-20261016.md)、[生产只读预检](intraday-predeploy-production-check-20261016.md)。日志：backend `backend-normalized-final-551.log` 位于 `/private/tmp/urus-predeploy-20261016/logs/`；collector最终日志位于 `/private/tmp/urus-predeploy-final-20261016/logs/collector-full-final.log`。

# 日内观察：收盘后部署记录

日期：2026-10-17 JST（美东 2026-10-16 收盘后）。用户最新“已经收盘了 可以继续”重新授权继续此前暂停的部署。此前开盘禁令期间未切换服务。

## 已部署结果

入口：`http://192.168.31.31:7777/intraday`。日内股票、事件消费和系统性风险入口启用，原有期权日内开关保留。通知系统仍不开发。

| 服务 | 新容器 | 镜像 | 启动 UTC |
| --- | --- | --- | --- |
| API | urus-api-intraday-20261017 | urus:intraday-20261017-r2 | 2026-10-16 21:54:26 |
| Urus collector | urus-moomoo-intraday-20261017 | urus-moomoo:intraday-20261017-final | 2026-10-16 21:55:00 |

API 镜像 digest：`199f4424d242202d06c10fdb108047690a2cd4344d55a00b13326092e0377887`。collector 镜像 digest：`3a5418f30295f01ae3e5928aa36964b71b9138d116c43bd18d87bdf55ace6b87`。先前 `urus:intraday-20261017-final` 不含最后的筛选修复，不能代替实际部署的 r2。

资源、网络、端口及数据挂载沿用原服务：API 8 CPU／6 GiB、7777→8000，collector 4 CPU／2 GiB；网络 urus-internal、MTU1280。原应用配置保留，只开启已验收的新功能开关。实际运行源码 hash 与最终构建一致，发布上下文 manifest 249／249 文件匹配。

继续只有一个 Urus 采集调度进程，和原 Eoflora 采集共用 `/data/urus-moomoo/collector.sqlite`。没有额外运行股票采集器，没有手动采集入口，没有执行 run-once／盘中补跑。Eoflora 应用容器 `eoflora-current` 未停止或替换，启动时间仍为 2026-09-17 08:15:13 UTC。

## 配置与原功能保护

新 collector 使用独立文件 `/data/deploy/intraday-release-20261017/collection_tasks.final.toml`，只追加 equity-intraday 与 equity-maintenance 两项；原 runtime 与原六项任务逐项结构相等。原 `/data/deploy/collection_tasks.local.toml` 未覆盖。

- 原配置 SHA256：`6f093ddd54204fcdb1bc3e0a47147ef68aaea07a65bb8dff5897c625ca0edf7c`。
- 新配置 SHA256：`a174a871e30d8d5abb6743a5a2fc776e8346bb2f3462641282ac5aab1bd6d1d7`。
- API 数据库升级到 `0027_intraday_events`，仅新增事件相关表／索引；collector 新增股票规范表。未执行旧数据清理、数据库恢复或降级。
- 切换前在旧 collector 内取得非阻塞采集锁，确认空闲后停止旧进程，再启动新进程，没有双采集器并行运行。
- 实际新 collector 的只读 preflight 通过：2026-10-16、10 groups、46 market symbols、19 options symbols；验证真实 API 健康及原发布计划契约。

## 测试及部署验证

部署预检发现前端已展示 S4–S8，但后端事件筛选仍只接受 S1–S3。先增加参数化回归测试，复现六项 400，再将白名单扩展到已实现的 S1–S8。筛选只返回对应策略，接受小写 s8，S9 仍返回结构化 400。独立 luna-builder 复验 6 项通过。

| 验证 | 结果 |
| --- | --- |
| backend 严格离线完整回归 | 557 项通过，exit 0；原 551＋新增筛选 6 项 |
| collector 完整离线回归 | 157 项通过；部署源码与此前最终验收身份一致 |
| frontend | 既有 vue-tsc＋Vite 构建通过；本轮镜像重新完成构建 |
| Git whitespace | git diff --check 通过 |
| 新鲜备份副本迁移 | 0026→0027 成功，生产 `/data` 只读挂载 |
| 副本旧功能 | health、universe、groups、今日 collection-plan 均 200 JSON |
| 副本新接口 | overview、S4／S8 events、systematic-risk 均 200 JSON |
| 副本页面 | 首页、observation、intraday 均 200 |
| 现网 API | health status=ok、environment=production、database=ok |
| 现网消费者 | supervisor、uvicorn 和 `app.intraday.event_consumer --watch` 正常运行；初次扫描 idle、0 slots、errors=[] |
| 股票新表 | 初始四表均 0 行，符合收盘后首次安装预期 |

独立 agent 的现网 HTTP 回归见 [收盘后独立报告](intraday-postclose-test-report-20261017.md)。本次未把“首次真实开盘槽成功”当作已完成证据：部署时市场已收盘。

独立 HTTP 验收最初遇到大响应提前断开，未将部分响应算作通过。root 随后从授权直连网络执行无 Range 的完整 GET：三轮 JS（357,244 B）／CSS（154,478 B）均完整、Content-Length 匹配且与本地构建 SHA 一致；日线 chart（16,729,943 B）和 strategies（867,657 B）也均为完整有效 JSON，共 8／8 成功。macmini localhost 另外一轮 JS／CSS 完整。随后严格使用初次验收的 `Connection: close` 请求头，确认 LAN 路径仍会截断（JS236,460／357,244 B、CSS131,072／154,478 B）；相同关闭连接请求在 macmini localhost 和 API 容器内均完整。默认 keep-alive 路径保持通过。一次独立临时容器以同一镜像、既有隔离副本及 asyncio＋h11 对照，LAN 关闭连接仍截断，完成后已停止。故未修改生产协议或业务代码；问题集中于 LAN＋关闭连接传输路径，具体网络层原因尚未确定。普通网页默认资源与大 JSON 完整验收通过，该短连接兼容问题作为后续网络排查限制保留，不能声称所有 HTTP 客户端均无问题。证据 `/private/tmp/urus-postclose-20261017/transfer-root-results.json`。

## Eoflora 与额度

切换前核对今天的真实采集，切换后原 capture 与 scheduler ledger 仍在：

| 数据 | capture_id | UTC |
| --- | --- | --- |
| 最后盘中采集 | b06465b1-35d6-4dde-8eae-6f2e0ebb4de4 | 19:55:09 |
| 收盘 finalizer | 0905a179-0285-423f-9b06-879fe3e45eac | 20:05:02 |
| 收盘 EOD | 9b7692e9-48a9-42dc-ac0c-af666392d707 | 21:00:41 |

EOD ready=true、complete=true，source 7488／retained 7481／filtered 7；IV 418 次中成功 411、失败 7，无必需标的缺失或双边数据缺失。收盘发布 status=ok，close_plus_60=eod_ready。部署前后 published/eod-close-v1/latest.json 字节完全一致，SHA256 `66f67fb8c4c1f237e48d1582aec4b736c94dd3c7f0ad6e50a82d2ef778d7eb4c`。

21:47 UTC 的 SDK 只读额度检查：已用 46、剩余 54；当前股票池 46 个代码均已在额度窗口，新增代码 0。保留 20 个缓冲时当时可再加 34；这是检查时余额，不是永久保证。46 个分钟请求＋每槽一次全池快照，完整 78 槽约 3666 次基础调用／日；分页、重试、额度查询、原 Eoflora 请求另计。分组控制瞬时密度，不减少 distinct-code 额度。已验收的 pacing、45 秒窗口预算和 Eoflora 优先级继续生效。

## 下一交易日与数据预热

下一交易日 2026-10-19，按实际 XNYS 开盘时间／纽约 DST 换算：

| 操作 | 美东 EDT | 东京 JST |
| --- | --- | --- |
| 本地维护 | 08:00 | 21:00 |
| 第一个 Eoflora 优先窗口 | 09:35 | 22:35 |
| 股票 G1／G2／G3 | 09:36／09:37／09:38 | 22:36／22:37／22:38 |
| 本地整槽合并 | 09:39 | 22:39 |

每个完整交易日股票为 78 槽、每槽 3 分片＋1 次本地合并。首分钟让给 Eoflora，最后一分钟不发股票 provider 请求；任一到期 Eof 失败待重试时股票让步。维护开盘前 90 分钟运行，开盘前 30 分钟硬截止。

首次真实采集前网页 no_data 正常；MACD 正式信号需要 100 根连续有效 5m 预热，RVOL 需要至少 10 个此前交易日同槽样本。历史不足明确不可用，不造值、不自动无界回补。CTA 代理在输入或复权基准不兼容时同样明确不可用，不能声称真实基金仓位预警。原始股票证据／报价保留 2 个交易日，5m 指标输入保留 21 个会话；容量口径与实测限制沿用 [存储预算](intraday-storage-budget-20261016.md) 和 [最终本地验收](intraday-normalized-storage-acceptance-20261016.md)。

## 备份与回退

均用 SQLite backup API，未复制不一致的运行中主库／WAL；两个新鲜备份 quick_check=ok：

- API：macmini `/Users/mithridates/UrusBackups/pre-intraday-20261017/urus.db`，2,175,614,976 B；42.157 秒。
- collector：macmini `/Users/mithridates/data/urus/deploy/pre-intraday-20261017/collector.sqlite`，10,562,121,728 B；142.757 秒。
- API 验收 clone 为同目录独立 preview.db，未覆盖原备份。数据盘检查剩余约 772 GiB。
- 原 API `urus-ui-group-links-20261007-CtTuFI` 与原 collector `urus-moomoo-scheduler-20261004-nMm7M9` 已停止但保留，原配置和镜像也保留；未删除容器或历史数据。
- collector 回退可停新进程后启动旧容器，读取原六任务配置。API 旧镜像不认识 0027，直接启动旧 supervisor 会在 migration 阶段失败；回退应以旧镜像直接运行 uvicorn，绕过旧迁移器，使用兼容的增量表结构及原环境。不要自动降级删表或用旧快照覆盖现网数据库。

本次无需回退。私密环境文件与 inspect 证据只保存于受限文件，报告不包含 token。

用户在部署期间进一步明确：旧采集器先保留备份，Urus 直接替换、不需要备份。旧 collector 已按此保留；Urus 已切换，收到补充后不再追加 Urus 备份。上文两个备份已在该补充到达前完成，未执行额外删除操作。

root 日志：`/private/tmp/urus-postclose-20261017/logs/`；最终构建清单：`artifacts/intraday-release-20261017/source-manifest.json`。首次真实交易时段的采集和策略样本效果仍需之后的真实市场数据验证，没有额外创建定时监控或通知任务。

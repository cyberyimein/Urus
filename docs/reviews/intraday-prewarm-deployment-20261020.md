# 个股历史预热部署记录

2026-10-20。用户明确授权“还有时间，可以部署”。切换时间为纽约 05:49–05:50 EDT，早于 08:00 盘前维护和开盘保护窗口；没有触发手动采集或历史补跑。

## 已部署服务

| 服务 | 容器 | 启动时间 UTC / JST |
| --- | --- | --- |
| Urus API | `urus-api-prewarm-20261020` | 09:49:02 / 18:49:02 |
| 原单一 collector | `urus-moomoo-prewarm-20261020` | 09:50:01 / 18:50:01 |

- API 镜像 `urus:intraday-prewarm-20261020`，digest `5b75b2fda6eb1e126373439b9c9c15b1cc0749831bead07fced3b85e717fbf87`。
- collector 镜像 `urus-moomoo:intraday-prewarm-20261020`，digest `0bee908035530e484d734bc0a905d6adfa6b05a928a2d7e4031820d514a539e9`。
- 原 API 与 collector 已停止并保留，没有并行 collector；原网络、环境、端口、资源与数据挂载沿用。API 未新增数据库备份。
- `eoflora-current` 未重启或替换，启动时间仍为 `2026-10-18T18:52:32Z`，digest 仍为 `c5d3c360fec01a0cb7ab2a13252e1a19299ae304b3fa4d299197a01304588775`。

## 发布与采集保护

冻结源码在 `artifacts/intraday-prewarm-20261020/`，manifest 共 258 文件；相对上一线上双图版本只变化 6 个文件：API plan schema/repository、collector plan loader/runtime/provider/history store。没有前端修改、新配置项或迁移；数据库版本仍为 `0027_intraday_events`。实际容器中 API 154 个 Python 文件、collector 27 个 Python 文件逐项 SHA256 匹配冻结版本。

原配置仍为 `/data/deploy/intraday-release-20261017/collection_tasks.final.toml`，SHA256 为 `a174a871e30d8d5abb6743a5a2fc776e8346bb2f3462641282ac5aab1bd6d1d7`。原六项任务及 runtime 与原 TOML 结构相等，仍为六项原任务加股票采集/维护共八项；分片仍为 15/16/15。

新镜像只读预检通过：原观察计划为 10/19、10 个 group、46 个 market symbols、19 个 options symbols。当前新日内池为 46 成员，其中 31 个 `equity` 进入预热范围，15 个非 equity 不新增预热请求。计划冻结只在临时库演示；部署后生产冻结计划数量仍为 0，未提前改写当天冻结状态。

切换 collector 前非阻塞取得共享采集锁，确认空闲后停旧容器，再启动新容器。新日志显示 8/8 tasks enabled，OpenD 连接建立。未手动请求行情、运行维护或改写历史健康状态。

## 旧采集器备份与数据核验

通过 SQLite backup API 创建一致性备份：

`/Users/mithridates/data/urus/deploy/intraday-prewarm-20261020/collector-before.sqlite`

大小 **10,932,375,552 bytes**，耗时 **52.04 秒**，`quick_check=ok`。没有用备份覆盖生产库。旧容器、旧镜像和旧任务配置继续保留；必要时应先停新 collector 再启动旧 collector，不自动回写旧数据库。

Eoflora 原 10/19 EOD capture `954b921a-1103-47a9-a007-e8713bd99108` 的 payload SHA256 在切换前后均为 `7e0fa4b38823dafa688060ba9660c104ce47089bee878777068879101328b684`，ready/complete 均为 true。

历史额度仍按 7 日内标的去重。本次 31 个个股来自已有 46 成员，不增加池内新代码；实际准入仍由维护时的实时 quota 和原预留机制决定。本次没有重新调用 provider 查询实时剩余额度，不能将早先 total=100/used=46/remain=54 的快照当作当前余额。按普通冷启动两日计算，31 个股票最多约 62 次单日 K_5M 请求（未计失败重试），不是 62 个历史标的额度；已有缓存会减少请求。

## 测试与部署后验收

- 部署前 collector 全套 194 项通过，backend 579 项完成验证；Luna 预热独立验收 3 项通过，详见 [实现验收](intraday-history-prewarm-acceptance-20261020.md)。
- Luna 部署后 **21 个 GET、56/56 检查通过、0 失败**，详见 [部署后独立报告](intraday-prewarm-postdeploy-test-report-20261020.md)。10/20、10/21 新日内计划均 configured，类型映射完整、equity=31，plan_id 经 canonical SHA256 复算一致。
- 原 10/19 收市计划 200；尚未收市的 10/20、10/21 原计划为预期结构化 409。API/database health 正常。overview 的 stale 和 systematic-risk 的 unavailable 保留真实状态。
- 公网日内页可访问，仍引用原双图 JS `index-CQFXFf8n.js` 和 CSS `index-DWN1bHP7.css`；LAN 完整响应长度分别为 371,156 和 159,915 bytes。
- 原事件健康记录仍为 error/stale，last_error 为 `无法只读 sidecar 调度槽：attempt to write a readonly database`，last_evaluated_at 仍为 `2026-10-19T20:31:23.238475+00:00`。本次未修复或清除该历史告警，不能因 HTTP 200 声称该项恢复。
- `git diff --check` 通过。私密配置、运行日志与原始 inspect 保存在 `/private/tmp/urus-deploy-prewarm-20261020/`，不写入报告或仓库。

## 本交易日生效时间

2026-10-20 东京 **21:00**（纽约 08:00）自动开始盘前维护，东京 **22:00**（纽约 09:00）硬截止。仅为 31 只个股检查/补齐 100 根连续常规时段历史 5m。缓存、额度和数据质量符合要求时，开盘后第一根完整 5m 被采集并汇总后即可接续 MACD H、ΔH、Δ²H；第一根 bar 于东京 22:35 完成，原股票分片 22:36/37/38，22:39 本地汇总，调度安排不变。

部署验证不能替代首轮真实维护结果。此次没有提前补跑；真实历史缺口、停牌或 quota 拒绝仍会保留预热状态和逐股原因。

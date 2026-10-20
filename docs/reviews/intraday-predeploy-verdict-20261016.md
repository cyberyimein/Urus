# 日内观察部署前验收结论

日期：2026-10-16。协调验收：root；开发：用户指定执行 chat；独立测试：luna-builder。

结论：同库去重、跨日指标和离峰维护的最终本地验收通过：backend 551 项、collector 157 项、前端构建均通过。未部署、未启用新采集。用户临近开盘已明确禁止部署，原条件部署许可已撤回；验收通过不构成启用授权。

## 最新授权状态

用户已在临近开盘时明确禁止部署；原条件部署授权已撤回。当前只继续本地实现和验收，不得因测试通过自动部署。新存储切片的最新结果见 [归一化收尾记录](intraday-normalized-storage-acceptance-20261016.md)，下方 119／526 为较早窗口切片的阶段结果。

## 实际链路与范围

Eoflora 的采集任务与新股票日内任务都在现有 urus-moomoo 容器、同一个调度进程。新日内网页只有读接口，没有手动采集入口，自动刷新不调用 OpenD。本次不新增采集容器、手动入口或锁服务。

root 此前将其他 API 手动采集路径的跨容器锁问题混入本次阻断范围，已按用户澄清纠正。该既存现象单独记录，不作为本次日内部署阻断项。

## 较早 1＋3＋1 阶段验收（非最新归一化全套结果）

| 验收项 | 结果 |
| --- | --- |
| backend 当前全量 | 526/526 |
| collector 最终全量 | 119/119 |
| luna 独立 Scheduler→Runtime→Store→真实 P3 投影集成 | 7/7，包含在最终全量中 |
| frontend vue-tsc＋Vite | 通过，84 modules |
| git diff --check | 通过 |

新窗口以已收盘 5 分钟 K 结束时间 T 为统一截止点：T＋60／120／180 分别执行三个分片，T＋240 只做本地整槽发布，无 quote/history/quota/option provider 调用。因此页面与事件约在 T＋4 分钟可见，不承诺第一组采完就更新。

覆盖 Eoflora 任一到期槽失败或待重试时股票让步（包括旧槽失败但新槽成功）、预算结束不再请求、不跨窗追赶、重复 tick／进程重启幂等、缺组或超时 partial 不复用旧槽，以及最终只发布一条 source task＋T 整槽记录。默认特性仍为 opt-in，未改生产私有 TOML。

root 核对 runtime.py、scheduler.py、provider moomoo.py、storage/sqlite.py、services/intraday_equities.py 的 SHA256，均匹配 luna 全量回归后的身份。详见 [独立测试](intraday-predeploy-test-report-20261016.md) 和 [开发切片交接](intraday-equity-window-slice-20261016.md)。

root 另补 backend quota 公司名误判修复和 opt-in 事件消费者 supervisor 的先红后绿测试；均包含在 backend 526 项中。事件消费者仅启用配置时在 API 健康后启动，默认行为保持 API-only。

## 生产只读检查

- 实时历史代码 quota：已用 46，剩余 54，总计 100。当前 46 个启用代码均在窗口内；重复分钟获取理论不新增代码额度。预留 20 后，当时额外可新增最多 34，实际准入仍须刷新。
- 昨日 Eoflora EOD ready／complete，7,416 条合格合约，SPY／QQQ／IWM 齐全；消费 close_plus_60 为 eod_ready，最新发布 run_date=2026-10-15、status=ok。两个既存盘中锚点身份 conflict 未修改或重跑，不能声称所有旧锚点均无异常。
- SPY 昨日 1 分钟 end 语义聚合，78 根 OHLC 与 provider 5 分钟全部一致；volume 有微小口径差异。比较结果及局限已冻结，尚未写入生产配置。
- API 健康、原容器运行；数据卷与备份卷空间足够。本轮未停止、重启或替换容器，未执行生产迁移、改任务配置或删除数据。

详见 [生产预检证据](intraday-predeploy-production-check-20261016.md)。

## 本地收尾与启用边界

1. 同库 bar revisions／quote batch／compact refs、backend 只读恢复、21 日指标历史和 2 日事件保留已实现并通过定向验收。独立 timeout-G2 缺陷已修复，窗口与 normalized 独立验收各 7 项通过。
2. 单槽 100 股、此前 20 日 5m 的本地处理：G1/G2/G3 为 3.275／3.143／3.189 秒，finalizer 为 9.350 秒；RSS 峰值约 483 MiB。实际原 provider 46 股三组耗时为 12.661／11.818／11.291 秒，历史 quota 46 used／54 remain，均为已有窗口代码。
3. 100 股完整两日 raw／quotes／refs fixture 约 88 MiB；23 日 5m＋2 日 1m 的 bars-only fixture 约 184 MiB。统一 21＋2 全窗口长测未完成，不称作完整稳态实测；独立测量细节和 WAL 见报告。股票逻辑预算与共享主库物理体积严格分开。
4. 清理的旧两日 dry-run 基线 155.803 秒，不能放在股票盘中窗口。已做 ref_id 去重和单次读取优化，并完成同一 collector 内的开盘前 90 分钟维护任务；晚到及执行中跨开盘前 30 分钟截止会停止并回滚、保持 pending，不影响 Eoflora。
5. 本地收尾完成：旧契约测试迁移、维护真实事务回滚、collector 157 项与backend 551 项全量、源码身份核对均通过。阶段API／前端已完成构建，生产备份副本迁移及旧接口只读预演通过；旧镜像不含全部最终修复，未来启用须按最终源码重新构建。
6. 用户禁止部署期间只做本地收尾；不停止或替换现有 API／collector，不改生产私有 TOML、不迁移／清理生产数据库。启用前须等待用户重新明确授权。

最新证据见 [归一化验收](intraday-normalized-storage-acceptance-20261016.md)、[独立测试](intraday-predeploy-test-report-20261016.md)、[生产只读预检](intraday-predeploy-production-check-20261016.md)。

# Urus 日内修复与 UI 收盘后部署

日期：2026-10-20 JST；美国最近完成会话为 2026-10-19。用户明确授权“现在收盘了 你可以部署”。本次沿用原环境、挂载、网络与资源，不创建额外采集器，不执行手动行情采集。

## 运行结果

| 服务 | 容器 | 镜像 | 启动时间 UTC / JST |
| --- | --- | --- | --- |
| API 与前端 | urus-api-intraday-20261020 | urus:intraday-20261020 | 00:16:17 / 09:16:17 |
| 原单一 Urus collector | urus-moomoo-intraday-20261020 | urus-moomoo:intraday-20261020 | 00:17:11 / 09:17:11 |

API digest：`2755f2349ebbb3ff0da8720a93ec6a63c4e17b8439ca6ef4a3eaf1c367ee8c22`。
Collector digest：`2d7759c719e013e17fe1ea38b8572b6a653eba977a75e0ea7a9f5750b2d1e459`。

原两个 20261017 容器停止并保留，旧 collector 未删除；没有双 collector 运行。Eoflora 应用 `eoflora-current` 没有停止或替换，启动时间仍为 `2026-10-18T18:52:32Z`。API 仍为 8 CPU/6 GiB、7777→8000；collector 4 CPU/2 GiB；沿用 urus-internal,mtu=1280 和同一持久化目录。

## 发布内容

- 报价非有限数 NaN/Inf 归一化及错误隔离，避免单个报价丢弃整组分钟线。
- 页面四个核心只读接口共用 5 分钟整点刷新，隐藏页面暂停；MA5 与蜡烛中心对齐。
- MACD 与 K 线共用时间横轴，指标预热空白保留，H/ΔH/Δ²H 中文解释和正负含义。
- 紧凑、可搜索/折叠的业务分组标的栏；报价和指标原因可展开。
- 盘中事件移到广度下方、标的池和图表上方，事件列表限制 280px 内部滚动。
- 独立日内池设置、下一 XNYS 会话生效、SPY/QQQ/IWM 固定；维护/分片/finalizer 使用同一日冻结计划，仍由原 collector 执行。
- 部署前补充修复：生产旧 capture 使用 `2026-10-20T05:00:00+09:00`，实际交易日是纽约 10/19。旧池引导按带时区时间转换至市场时区后核对日期，不再比较字符串日期前缀；真实格式回归测试已加入。

冻结上下文：`artifacts/intraday-release-20261020/`，manifest 258 文件。运行镜像内 API 169 个源码文件、collector 27 个文件分别逐项核对 SHA256，无差异。新前端 JS `index-DD69aq0o.js` 为 366,498 bytes，CSS `index-e9t7FUvF.css` 为 159,685 bytes；LAN 完整响应验收通过，公网 `/intraday` HTML 也已引用这两个新文件。

## 原功能、采集与额度保护

- 部署前严格离线完整回归：backend **578 passed**（57.52s，9 warnings）、collector **183 passed**（6.24s）。此前新 UI Playwright 验收均已通过。
- 新 API 镜像只读挂载真实数据预检：原 **46** 标的完整恢复，10/19、10/20 新日内计划都 configured，固定三基准同时包含于报价和分钟范围。
- 新 collector 镜像在临时库验证日计划冻结，正式生产库未因预检提前冻结当日/明日计划。原六项任务和 runtime 与原始 TOML 结构相等，仍为原六项加股票采集与维护共八项；配置 SHA256 `a174a871e30d8d5abb6743a5a2fc776e8346bb2f3462641282ac5aab1bd6d1d7`，未覆盖旧文件。
- collector 的真实 API preflight 通过：10/19、10 groups、46 market symbols、19 options symbols；新股票计划分片 15/16/15。
- 切换前在旧 collector 容器取得非阻塞采集锁，确认空闲后停止旧进程，再启动新进程。旧 collector 和配置仍可用于恢复。
- Eoflora 10/19 EOD capture `954b921a-1103-47a9-a007-e8713bd99108`，ready=true、complete=true；生产 payload 与切换前备份逐字节一致，SHA256 `7e0fa4b38823dafa688060ba9660c104ce47089bee878777068879101328b684`。
- 该 EOD 中已有历史额度快照为 total=100、used=46、remain=54。这是已有记录，不是重新请求 SDK 的实时余额。本次默认池仍为 46 代码，distinct-code 增量为 0；分组不减少 distinct-code 额度，后续新选成员仍受原额度准入和时间预算限制。
- 只新增 collector 的日计划表；API Alembic 仍为 0027，无新版本迁移，不降级或清理原数据。

## 备份与恢复

按用户要求，只新增 collector 一致性备份，不新增 Urus 主库备份。通过 SQLite backup API 创建：

`/Users/mithridates/data/urus/deploy/intraday-release-20261020/collector-before.sqlite`

大小 10,932,367,360 bytes，quick_check=ok，耗时 63.19s。未直接复制运行中 WAL 主库，未替换生产数据。旧容器、旧镜像及旧 TOML 保留。若必须恢复，应先停新 collector 再启动旧 collector；API 亦可停止新容器后启动旧 20261017 容器；不自动用旧备份覆盖生产数据库。

构建首次使用绝对上下文时 Apple Container 未正确传入 frontend 目录，构建失败且未切换服务；改为在冻结源码目录中使用相对 Dockerfile 和 `.` 上下文后完成构建。

## 独立部署后验收

Luna 执行 20 个 GET：health/database/version、原 Universe/groups/观察计划、新池与日内计划、overview/events/health/risk、首页/日内/设置页及完整静态 JS/CSS。`passed=true`，`failures=[]`，详见 `intraday-postdeploy-readonly-test-report-20261020.md`。

旧收市观察计划对下一尚未收盘会话 10/20 返回结构化 409，属于预期；10/19 原观察计划为 200。日内 overview 收盘后 stale，CTA 输入不足返回结构化 unavailable，均没有造值。

### 尚存告警

事件健康页仍保留 10/19 20:31:23 UTC 的历史 `attempt to write a readonly database`，status=error/stale。部署前后直接只读 sidecar 查询均成功，新 event_consumer --watch 进程正常，最新循环 idle/errors=[]。目前没有新槽，现有实现不会仅因成功空轮询就清除历史 last_error，因此**不能声称该健康告警已修复或已清除**。没有为消除红色提示而改写生产状态；下一实际新数据处理后需要验证它更新。此次不是收盘后新发生的读库失败。

## 下一交易日实际时刻

纽约 2026-10-20（EDT），东京同日（JST）：盘前本地维护 21:00；第一个股票 G1/G2/G3 为 22:36/22:37/22:38，22:39 为只做本地合并的 finalizer。仍保留首分钟给 Eoflora、末分钟不发股票 provider 请求。完整日 78 槽、312 个股票分片/合并任务。

本次没有启动手动采集或真实盘中补跑，不能将部署验收代替下一交易日真实窗口成功的证据；指标仍受合格历史样本与连续性限制。

私密环境、原始 inspect 与本轮本地执行日志位于受限 `/private/tmp/urus-deploy-20261020/`，报告不包含凭据。

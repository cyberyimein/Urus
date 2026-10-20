# 调度与采集最小优化实施（2026-10-04）

## 设计取舍

面向单用户个人服务，继续使用现有单进程调度与 SQLite。没有增加消息队列、守护服务、迁移或监控平台。以修复确认的数据断点和降低日常开销为目标；历史检查提供手动命令，不做自动补采或自动清理。

背景见 [采集检查](scheduler-collection-health-20261004.md)。该文档是修复前的生产快照，不代表新代码已部署。

## 已实现

1. **收盘结果落库。** 配置 canonical intraday 数据库时，runtime 先保存不可变原始包，再在现有库的单个事务内更新最终分钟线、保留 bar revisions、生成当天成功 run 所选 attempt 的 outcomes。按 run/symbol upsert，可重复执行；事务失败会抛出，scheduler 不标记完成。校验日期、请求标的覆盖与正数有限收盘价。未配置 intraday 库的纯原始采集模式保持兼容。
2. **历史恢复。** `recover-close --date` 默认预览；`--apply` 才写入。只使用指定日期已完成调度记录链接的最新收盘包，验证 kind、交易日期及内容 hash；不创建 OpenD runtime，也不重新抓取市场。可以恢复修复前已经 completed 的日期。
3. **减轻历史扫描。** publisher 启动时校验历史包，在内存中仅保留已校验 slot/capture 的身份和摘要。之后 tick 先查轻量元数据，只读取和 hash 新 capture；重启重新校验，迟到历史日期也能发现。利用现有 capture 不可变约束，不增加持久化缓存表。正常空闲 tick 不再反复处理约 49 MiB 的已知 payload。
4. **限制发布重试。** 每次空闲回调最多尝试一个日期；失败仅影响该日期，冷却 60 秒，后续日期可继续。不在 provider lock 内发布；本轮有到期任务时先采集、跳过空闲发布。临近下一个采集点时预留最多三次 API 请求超时加 poll 的预算。仍是同步 HTTP 超时机制，不承诺操作系统层面的硬实时截止。
5. **调度容错。** tick 的 OSError（含超时）、RuntimeError 和 SQLite 异常被记录并退避，退避最长 60 秒；下一 tick 成功即恢复正常间隔。保留程序错误、输入校验错误和进程停止信号的原有传播语义。没有吞掉所有异常。
6. **报价质量。** 在写入选中 attempt 时统计 `quote_states` 和 `fresh_quote_coverage`；原 `complete` 表示抓取完整性，保持不变。没有因低流动性报价过期而无限重采，没有改变既有报价年龄阈值。
7. **整日缺失检查。** `audit --start --end` 按已有交易所日历与当前启用任务检查 market/options/close 完成记录，发现原来未进入发布待办账本的整日缺失。范围由使用者明确指定，避免推测部署起始日或历史启用区间。

## 使用

在 sidecar 的已有 Python 环境中执行，私有配置沿用现有路径：

```sh
python -m urus_moomoo.cli recover-close --config /path/to/private.toml --date 2026-10-02
python -m urus_moomoo.cli recover-close --config /path/to/private.toml --date 2026-10-02 --apply
python -m urus_moomoo.cli audit --config /path/to/private.toml --start 2026-09-22 --end 2026-10-02
```

恢复后核对分钟线末端、outcomes 数量与原始包日期。正常 77 次采样、3 个标的均有 selected spot 的交易日应产生 231 条 outcomes。无原始包的日期不能靠本命令恢复，更不能用今天期权快照冒充历史快照。

## 验证

执行 sidecar 全部测试以及 backend 的 intraday store / scheduler 兼容测试：**65 passed**。有一条已有的 Starlette/httpx 弃用警告，不影响测试结果。`git diff --check` 通过。

新增覆盖：最终分钟线及收益、幂等恢复、下游失败全事务回滚、跨日期拒绝、运行时收盘落库、离线预览和恢复、日历超时后恢复、临近采样暂停发布、历史包只验证一次且重启复验、失败日期不阻塞次日、完整性与 stale/zero_quote 分开统计、周末不误报整日缺失。

未改动本轮前端或 backend 业务逻辑，因此未重复前端构建和整个性能测试套件。

## 状态与边界

采集器已于 2026-10-04 10:25 UTC（日本时间 19:25）部署，18 个交易日的生产历史恢复已完成；详细版本、备份及验收见 [部署与恢复记录](scheduler-collection-deployment-20261004.md)。此前 backend/frontend 性能补丁及其他现有工作区修改保留，本次部署范围为独立采集器。

质量摘要新增字段会出现在新采样记录中；旧数据不会自动重写。audit 检查采集完成日期，不声称覆盖全部消费者、发布结果或收益标签健康。按个人服务需求，先保留可运行的手工检查，不新增常驻监控、通知渠道或多进程发布器。

# urus-moomoo

独立的 Moomoo/OpenD 数据采集器与日程运行边界。

这个目录不依赖 Urus 或 Eoflora 的应用启动、数据库模型和 API 路由。上层系统只消费采集器输出的稳定数据契约；采集器可以单独升级、重启和回放。

## 私有运行配置

采集任务定义不进入 Git：采集哪些标的、哪些字段、哪些时间段、输出位置和启停策略，都放在本机的 `config/collection_tasks.local.toml`，或通过 `URUS_MOOMOO_TASK_CONFIG` 指定其他本地路径。该文件已被根目录 `.gitignore` 排除。

不要把真实任务配置、OpenD 地址、账号权限、原始数据、日志或运行数据库写入本目录的受版本控制文件。

## 边界

- `src/urus_moomoo/`：独立运行时代码与 provider 适配协议；
- `config/`：只保存说明文件，实际任务定义留在本机；
- `data/`、`logs/`：运行时目录，不提交；
- `pyproject.toml`：独立依赖边界，不复用 Urus 的 Python 包导入路径。

## 当前可运行切片

本轮已迁入并可独立安装的边界包括：

- `integrations/`：OpenD 市场快照、日 K、资金流、交易日、期权元数据/链/合约快照和盘中分钟 K/期权采集调用；
- `services/calendar.py`：以 Moomoo US `request_trading_days` 为交易日资格来源，exchange calendar 只提供开收市时间；
- `services/pacing.py`：主机级 `flock`、跨重启持久化 pacing 和独立 rate class；
- `services/quota.py`：quota snapshot、reserve、请求前 `HistoryAdmission`，缺额度时 fail-closed；
- `services/scheduler.py`：由本服务自身驱动的定时/收市后任务和盘中期权任务边界；
- `storage/sqlite.py` 与 `contracts.py`：原始数据 SQLite 落盘和带 schema version 的消费者契约。

Eoflora EOD 输出会在消费者边界清洗 provider 的零/缺失 IV：有效 Moomoo IV 保留；其余仅在有效 bid/ask 中价满足模型可解条件时，用带版本和参数来源的 Black–Scholes 近似反解 IV、Delta 和 Gamma；无法可靠反解的合约过滤并计入质量摘要。原始 provider 响应不被覆盖。

启动前必须提供被忽略的本地任务配置；缺失配置会直接失败。可用 `urus-moomoo validate --config <local.toml>` 校验，连接 OpenD 后用 `urus-moomoo run-once --config <local.toml>` 执行一次启用任务，或用 `urus-moomoo schedule --config <local.toml>` 启动独立调度器。调度器用 Moomoo 交易日判断是否开市，用 exchange calendar 计算收市时间；任务的 operation、标的、时刻、收市后延迟和采集参数只来自本地配置。

部署后可在采集容器内运行 `python -m urus_moomoo.cli preflight --config /data/deploy/collection_tasks.local.toml`。它从容器网络检查 Urus API 和数据库健康，调用正式发布前使用的观察组同步与采集计划接口，并校验最近已收市交易日的计划。它不连接 OpenD、不采集行情，也不创建 Observation Run；失败返回非零状态。`scripts/deploy_apple_moomoo.sh` 启动容器后会自动执行此检查，避免容器显示运行中却无法发布报告。

股票 `capture_intraday_equities` 的分钟 payload 保留原始 `bar_time`，并明确给出 UTC aware 的 `bar_start_at`／`bar_end_at` 和 `minute_timestamp_role`。默认 `start` 仅沿用现有采集器约定；Moomoo 真实 provider 的时间戳边界尚未验证。启用真实采集前必须确认 provider 语义并在任务参数中明确选择 `start` 或 `end`。fake SDK 验收不代表生产时间口径已验收；缺失或非法 OHLCV/turnover 不会标记为 ready。

## 尚未迁移

以下内容仍由 Urus backend 兼容桥负责，后续应迁到本服务而不让本包反向导入 backend：

- backend 的 ORM/业务数据库模型、canonical daily-bars 重算和旧 API 响应适配；
- 现有 intraday raw backup/maintenance 的压缩归档与跨日恢复；
- 生产部署文件与健康探针。

这些未迁移项不影响独立采集进程运行；单进程调度完成记录已落入本服务自己的 SQLite，重启不会重复执行已完成时槽。

### 收盘恢复与历史缺口检查

配置了 `runtime.intraday_sqlite_path` 时，收盘采集会在任务完成前将最终分钟线和每个成功盘中采样的收盘收益写入既有数据库。仅保存原始包的独立使用方式仍支持不配置此路径。

已有 completed 记录不会被调度器自动重跑；部署修复后，可从已有收盘原始包恢复指定日期。以下命令不连接 OpenD；先预览，再显式写入。重复恢复不会产生重复 outcomes。

```sh
python -m urus_moomoo.cli recover-close --config /path/to/private.toml --date 2026-10-02
python -m urus_moomoo.cli recover-close --config /path/to/private.toml --date 2026-10-02 --apply
python -m urus_moomoo.cli audit --config /path/to/private.toml --start 2026-09-22 --end 2026-10-02
```

`audit` 按交易所日历检查已启用的市场、期权快照和收盘任务是否整日缺失。范围应选择任务确实启用且已过采集截止时间的日期；它是原始采集完成记录的只读核对，不代表报告发布、收益标签等所有下游结果都完整。不会自动补抓历史行情。

盘中 `quality_summary` 新增 `quote_states` 和 `fresh_quote_coverage`，与原有 `complete` 分开：完整抓回原始报价不意味着全部报价新鲜。现有历史记录不会自动重写。

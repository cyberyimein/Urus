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

启动前必须提供被忽略的本地任务配置；缺失配置会直接失败。可用 `urus-moomoo validate --config <local.toml>` 校验，连接 OpenD 后用 `urus-moomoo run-once --config <local.toml>` 执行一次启用任务。真实任务、标的、字段、时间段和 OpenD 地址不进入 Git。

## 尚未迁移

以下内容仍由 Urus backend 兼容桥负责，后续应迁到本服务而不让本包反向导入 backend：

- backend 的 ORM/业务数据库模型、canonical daily-bars 重算和旧 API 响应适配；
- 现有 intraday raw backup/maintenance 的压缩归档与跨日恢复；
- 生产部署文件、健康探针和多进程 scheduler 状态的持久化运维包装。

这些未迁移项不影响本轮包级 provider、容量准入、调度和 SQLite 契约测试。

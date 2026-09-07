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

后续从 `backend/` 迁移采集器时，先保持 OpenD 请求、交易日历、限频、原始落盘和重试语义不变，再通过稳定输出契约接回 Urus。

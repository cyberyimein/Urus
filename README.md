# Urus

> 面向美股股票与 ETF 的可审计研究与决策辅助系统。

Urus 将收市后市场数据采集、日线证据、技术与相对强弱策略、期权结构分析以及可选的远程 AI 仲裁串成一条可追溯链路。系统不连接券商下单，不执行自动交易，也不构成投资建议。

## 当前产品边界

- `Universe` 是标的意图的版本化来源，保存启用状态、主题、报价/日线/期权采集开关和 AI 候选角色。
- `Observation Group` 从当前 Universe 生成实际关注列表；一次 `Observation Run` 冻结组版本、日线证据、确定性策略结果和期权快照。
- 期权结构与股票证据在同一个个股页面展示，不单独创建期权页面。期权快照包含 DEX、GEX、Gamma Wall、Max Pain、Expected Move 和 Spot Gamma Profile。
- 收市后对照只使用官方 `regular close`。当收盘价接近 Max Pain 或 DEX Wall 时，系统标记为结构性邻近/影响候选，不把邻近关系解释为因果证明。
- AI 只读取已经冻结的 Dataset、图表投影和确定性策略结果。AI 运行是用户主动发起的 `Remote Decision Run`，不参与收市数据采集。
- Moomoo OpenD 的历史额度使用 cache-first 计划、请求前复核、单机锁和 endpoint 限频；额度不足时保留 Universe 配置并显示等待状态。

数据不可用时保留 `placeholder`、`unavailable`、`partial` 等明确状态，不把占位结果伪装成真实数据。

## 项目结构

```text
Urus/
├── backend/
│   ├── app/api/              # FastAPI 路由
│   ├── app/core/             # 配置、数据库、错误和状态语义
│   ├── app/decision_harness/ # 日线证据、图表、策略和远程决策编译
│   ├── app/integrations/     # Moomoo、FRED、Yahoo、Anomalo Workflow 适配器
│   ├── app/models/           # 当前 SQLAlchemy 模型
│   ├── app/repositories/     # 当前数据访问层
│   ├── app/services/         # Observation、额度和采集服务
│   ├── alembic/              # schema 迁移历史
│   └── tests/
├── frontend/
│   ├── src/api/              # 集中式 API client
│   ├── src/components/       # 个股、期权、图表和远程决策组件
│   ├── src/views/            # 个股、组、观察运行、横截面和 Universe 页面
│   └── tests/
├── docs/                     # 当前架构、数据源、额度和部署说明
├── deploy/                   # 私有部署配置示例
└── docker/                   # 容器构建文件
```

## 本地启动

前置条件：Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js/npm，以及启用真实行情时可访问的 Moomoo OpenD。

```bash
cp .env.example .env

cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

默认前端是 `http://localhost:5173`，后端是 `http://127.0.0.1:8000`。Vite 会把 `/api` 转发到后端；后端地址不同于默认值时设置 `VITE_API_BASE_URL`。

## 收市观察任务

常驻调度器使用东京时区运行；启用 Moomoo OpenD 时，交易日资格由 Moomoo 的 US trading-days API 决定，收市时刻继续由本地交易所日历提供。任务会在收市后同步 Universe、创建 Observation Run，并在同一运行中完成股票日线与期权快照：

```bash
cd backend
uv run python scripts/schedule_market_data_collection.py \
  --api-base-url http://127.0.0.1:8000/api \
  --backend-managed-externally
```

需要立即验证当前链路时：

```bash
uv run python scripts/schedule_market_data_collection.py \
  --api-base-url http://127.0.0.1:8000/api \
  --backend-managed-externally \
  --once post_close_observation
```

脚本按交易日历和提前收市规则计算实际时点，并用状态文件防止同一时点重复执行。股票日线和期权请求共享 `MoomooCollectionCoordinator` 的主机级锁与限频器；日历查询本身使用缓存和独立限频，不会长期占用采集锁。

## API

API 前缀为 `/api`，完整 schema 以运行中的 OpenAPI 为准：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health`、`/api/version` | 健康与版本 |
| GET/PUT | `/api/settings` | 当前收市观察开关 |
| GET/PUT | `/api/settings/universe` | Universe 当前版本 |
| POST | `/api/daily-evidence/datasets` | 冻结日线 Dataset |
| GET | `/api/daily-evidence/datasets/{id}` | 读取 Dataset |
| GET | `/api/observation/groups` | 读取 Observation Group |
| POST | `/api/observation/groups/sync` | 从 Universe 同步组 |
| POST/GET | `/api/observation/runs` | 创建或读取 Observation Run |
| GET | `/api/observation/options` | 读取指定交易日的期权快照 |
| GET/POST | `/api/remote-decisions` | 远程 AI 仲裁的预检与提交 |

## 配置重点

- `MOOMOO_ENABLED`、`MOOMOO_HOST`、`MOOMOO_PORT`：OpenD 连接。
- `OPTIONS_TARGET_SYMBOLS`、`OPTIONS_WATCHLIST_SYMBOLS`：期权队列；期权数据按收市观察任务串行采集。
- `MOOMOO_HISTORY_*`、`MOOMOO_*INTERVAL_SECONDS`：日线额度和请求间隔控制。
- `MOOMOO_TRADING_CALENDAR_REQUEST_INTERVAL_SECONDS`：Moomoo 交易日历请求间隔，默认 `1.05` 秒。
- `OBSERVATION_UNIVERSE_SOURCE_URL`：可选的上游 Universe 地址；默认 fail-closed。
- `SCHEDULED_POST_CLOSE_ENABLED`：收市后常驻任务开关，也可通过 `/api/settings` 修改。
- `ANOMALO_WORKFLOW_*`：可选的远程 AI Workflow 凭据和超时。Token 只放环境/Secret Store，不进入数据库或前端。

真实数据源关闭时，测试和本地 UI 使用明确的 mock/placeholder 状态。不要提交 `.env`、私有部署文件、数据库、日志或构建产物。

## 测试与构建

```bash
cd backend
uv run pytest

cd ../frontend
npm test
npm run build
```

## Mac mini 部署

项目使用 Apple Container 构建 Linux ARM64 镜像。完整流程见 [`docs/macmini-container-deployment.md`](docs/macmini-container-deployment.md)；额度设计见 [`docs/moomoo-opend-capacity-admission-and-collection-design.md`](docs/moomoo-opend-capacity-admission-and-collection-design.md)。

当前系统没有登录认证，只适合可信 LAN；公网部署前必须增加认证、访问控制和密钥管理。

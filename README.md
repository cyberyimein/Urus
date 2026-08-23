# Urus

> 面向美股股票与 ETF 的可审计研究与决策辅助系统。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Urus 将市场数据采集、技术与相对强弱分析、期权结构分析、研究报告和可选的 AI 决策串成一条可追溯工作流。系统保存每次运行的输入快照、数据质量、模型输出和工具轨迹，便于复盘与验证。

Urus 是研究软件，不是交易系统：它不连接券商下单，不执行自动交易，也不构成投资建议。

## 主要能力

- **市场与个股研究**：通过 Moomoo OpenD 采集 ETF/股票快照和历史日线，计算趋势、波动、ATR、布林带、相对 QQQ 强弱等字段。
- **宏观上下文**：可选接入 FRED 和 Yahoo Finance 的日频宏观数据，并保留来源、时间和缺失状态。
- **期权结构**：对配置标的计算 DEX、GEX、Gamma Wall、Max Pain、预期波动、Spot Gamma Profile 和 Gamma Flip。
- **研究分支**：支持确定性的 CTA ETF 代理压力分析，也保留事件研究覆盖层的配置入口。
- **Stage 4B Urus Agent**：可选通过 OpenRouter 对冻结证据进行结构化分析；模型只能使用受约束的只读工具，输出会保存 schema、模型、成本、token 和 trace 信息。
- **研究前端**：Vue 3 界面提供首页、手动分析、研究报告、技术报告、决策摘要、trace、运行进度、Universe 和运行设置。
- **可重现持久化**：SQLAlchemy + Alembic 保存运行、快照、指标、期权、报告、数据集和 AI 审计记录；本地默认 SQLite，也可配置 PostgreSQL。

## 当前状态

项目处于积极开发阶段。真实数据源和 AI 均通过显式开关启用；关闭或不可用时，系统会保留 `disabled`、`unavailable`、`partial` 或 `is_mock` 等状态，不把占位结果伪装成实时事实。

当前默认配置：

- `MOOMOO_ENABLED=false`
- `FRED_ENABLED=false`
- `YAHOO_ENABLED=false`
- `URUS_AGENT_ENABLED=false`
- `WORKFLOW_RESEARCH_VARIANT=cta`

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11+、FastAPI、SQLAlchemy、Alembic、Pydantic Settings |
| 前端 | Vue 3、Vite、TypeScript、Pinia、Vue Router |
| 数据库 | SQLite（默认）或 SQLAlchemy 支持的 PostgreSQL |
| 外部数据 | Moomoo OpenD、FRED、Yahoo Finance；Anomalo 和 OpenRouter 为可选集成 |
| 部署 | Apple Container / OCI 镜像（可选） |

## 项目结构

```text
Urus/
├── backend/
│   ├── app/
│   │   ├── api/              # REST API
│   │   ├── integrations/     # Moomoo、FRED、Yahoo、Anomalo 等适配器
│   │   ├── models/           # SQLAlchemy 模型
│   │   ├── repositories/     # 数据访问
│   │   ├── urus_agent/       # Stage 4B、技能、工具和报告
│   │   └── workflows/        # 工作流编排
│   ├── alembic/              # 数据库迁移
│   ├── scripts/              # 采集、回填、调度和验证脚本
│   └── tests/
├── frontend/
│   ├── src/api/              # 集中式 API client
│   ├── src/components/       # 通用与研究报告组件
│   ├── src/views/            # 首页、研究、运行和设置页面
│   └── tests/
├── docs/                     # 架构、数据源、部署和阶段设计
├── deploy/                   # 私有部署配置示例
├── docker/                   # 容器构建文件
└── scripts/                  # 本地/Apple Container 构建与部署
```

## 本地启动

### 前置条件

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm
- 如果启用真实市场采集：可访问的 Moomoo OpenD

### 1. 创建本地配置

```bash
cp .env.example .env
```

`.env` 已被 Git 忽略。按需修改 `MOOMOO_HOST`、数据源开关、Universe 和 CORS；不要把真实 API key、账户信息或内网地址提交到仓库。

开发时 Urus 后端可以运行在本机，而 Moomoo OpenD 运行在同一 LAN 的另一台机器上。将示例中的 `MOOMOO_HOST=opend-host` 替换为实际可达的主机名或私有 IP，并保持 `MOOMOO_PORT=11111`；真实地址只放在本地 `.env` 或私有部署配置中。

### 2. 启动后端

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

后端默认地址为 `http://127.0.0.1:8000`，健康检查和 OpenAPI 文档分别位于：

- `GET http://127.0.0.1:8000/api/health`
- `http://127.0.0.1:8000/docs`

应用启动时会为新 checkout 创建缺失表；Alembic 仍是正式环境的 schema 迁移入口。

### 3. 启动前端

另开终端：

```bash
cd frontend
npm install
npm run dev
```

打开 Vite 输出的地址（默认 `http://localhost:5173`）。开发服务器会把 `/api` 请求代理到本地 FastAPI。后端不在默认地址时，可设置：

```bash
export VITE_API_BASE_URL=http://backend-host:8000/api
npm run dev
```

## 工作流

一次研究运行会保存阶段状态和冻结证据，典型顺序为：

```text
采集市场与宏观
      ↓
事件/CTA 研究覆盖层
      ↓
期权结构分析
      ↓
个股与主题分析
      ↓
可选的 Urus Agent 决策
      ↓
前端研究报告与审计记录
```

Urus 支持多类运行场景：

- `pre_market`：盘前研究。
- `pre_close`：收盘前研究，可按配置跳过 AI。
- `post_close_review`：收盘后复盘和经验评估。
- `manual_analysis`：前端发起的手动分析，基于冻结 snapshot 生成报告。

条件步骤的跳过是正常状态，不等于失败。数据源连接失败、数据不足和部分完成会在运行记录及报告中明确标识。

## 关键 API

API 前缀为 `/api`。完整 schema 以运行中的 OpenAPI 为准。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/version` | 应用与 API schema 版本 |
| POST | `/api/runs` | 创建一次标准研究运行 |
| POST | `/api/analysis/runs` | 创建一次手动分析 |
| GET | `/api/runs` | 查询运行列表 |
| GET | `/api/runs/{run_id}` | 查询运行详情 |
| GET | `/api/runs/{run_id}/progress` | 查询运行进度 |
| GET | `/api/runs/{run_id}/research-reports` | 查询运行关联报告 |
| GET | `/api/research-reports` | 查询历史研究报告 |
| GET | `/api/research-reports/{report_id}` | 查询报告摘要 |
| GET | `/api/research-reports/{report_id}/technical` | 查询技术报告 |
| GET | `/api/research-reports/{report_id}/decision` | 查询决策报告 |
| GET | `/api/research-reports/{report_id}/trace` | 查询 AI trace |
| GET | `/api/settings` | 查询运行时设置 |
| PUT | `/api/settings` | 保存运行时设置 |
| GET/PUT | `/api/settings/universe` | 查询或更新标的 Universe |
| GET | `/api/ai/decisions` | 查询 AI 决策审计 |

## 配置与数据源

所有配置都来自环境变量；安全示例位于 [`.env.example`](.env.example)。常用开关如下：

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | 数据库连接，默认本地 SQLite |
| `MOOMOO_ENABLED` | 启用 Moomoo OpenD 市场和历史数据 |
| `MOOMOO_HOST` / `MOOMOO_PORT` | OpenD 地址和端口 |
| `FRED_ENABLED` | 启用 FRED 日频宏观数据 |
| `YAHOO_ENABLED` | 启用 Yahoo Finance 日频数据 |
| `WORKFLOW_RESEARCH_VARIANT` | `cta` 或 `events` |
| `URUS_AGENT_ENABLED` | 启用 Stage 4B Urus Agent |
| `OPENROUTER_API_KEY` | OpenRouter 密钥，只能放在本地/部署环境变量中 |
| `URUS_AGENT_MODEL` | OpenRouter 模型标识 |
| `SCHEDULED_*` | 东京时区的盘前、收盘前和盘后调度开关 |

真实数据联调时，先确认远程或本机 OpenD 已启动且 `MOOMOO_HOST`/`MOOMOO_PORT` 可达。没有 OpenD 时不要把连接失败解释为市场数据缺失或模拟成功。

## 测试与构建

```bash
# 后端
cd backend
uv sync
uv run pytest

# 前端
cd ../frontend
npm install
npm test
npm run build
```

后端测试默认使用离线 mock/fixture；需要真实数据的验证脚本位于 `backend/scripts/`，运行前请确认数据源、额度和本地环境。

## 容器部署

项目提供 Apple Container 的 Linux ARM64 构建和部署脚本。部署配置必须使用私有文件，不要提交 `deploy/urus.container.env`：

```bash
cp deploy/urus.container.env.example deploy/urus.container.env
chmod 600 deploy/urus.container.env

IMAGE_TAG=urus-$(date +%Y%m%d-%H%M) \
  scripts/build_apple_container_image.sh

REMOTE=<ssh-host> \
ENV_FILE=deploy/urus.container.env \
  scripts/deploy_apple_container.sh \
  artifacts/container-images/urus-<tag>-linux-arm64.env
```

完整部署说明见 [`docs/macmini-container-deployment.md`](docs/macmini-container-deployment.md)。当前部署没有登录认证，只适合可信网络；公网部署前必须增加认证、访问控制和密钥管理。

## 安全与边界

- 不要提交 `.env`、`deploy/*.env`、数据库、运行数据、构建产物或日志。
- OpenRouter、Anomalo、Moomoo 等凭据只能通过环境变量提供。
- 应用当前没有用户认证，不应直接暴露到公网。
- 系统不提供下单、仓位限制或自动风控；AI 输出和市场数据都需要人工核验。
- 这是研究和决策辅助软件，不构成投资、税务或法律建议。

## 进一步阅读

- [`backend/README.md`](backend/README.md)：后端命令、调度和 API 补充说明。
- [`docs/implementation.md`](docs/implementation.md)：已落地实现和边界。
- [`docs/market-data-sources.md`](docs/market-data-sources.md)：数据源与质量策略。
- [`docs/stage4b-ai-decision.md`](docs/stage4b-ai-decision.md)：Stage 4B 决策流程。
- [`docs/decision-ai-learning-loop-design.md`](docs/decision-ai-learning-loop-design.md)：第二种决策 AI 的可验证策略闭环设计。
- [`docs/daily-k-decision-harness-development-design.md`](docs/daily-k-decision-harness-development-design.md)：日 K、策略、组决策、收市后观测和 Anomalo Workflow JSON 的开发规格。
- [`docs/urus-agent-design-requirements.md`](docs/urus-agent-design-requirements.md)：Agent 工具边界和输出契约。

## License

Urus 使用 [MIT License](LICENSE)。

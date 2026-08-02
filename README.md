# Urus

Urus 是一个股票分析与决策辅助系统的前后端分离框架。当前 `main` 只保留可离线运行的工作流骨架和 mock adapter；真实行情、宏观数据和技术指标在后续阶段分支中逐步接入。

## 目录

- `backend/`：FastAPI、SQLAlchemy、Alembic、七步骤工作流和 API 测试。
- `frontend/`：Vue 3、Vite、TypeScript、Pinia、Dashboard / Runs / Run Detail。
- `development-requirements.md`：项目开发约束和阶段要求。
- `docs/implementation.md`：框架实现说明。

## 启动

先复制 `.env.example` 为 `.env`，然后分别启动后端和前端：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173/`；FastAPI OpenAPI 位于 `http://127.0.0.1:8000/docs`。

## 测试与构建

```bash
cd backend && uv run pytest
cd frontend && npm test && npm run build
```

## 框架行为

`POST /api/runs` 使用 `pre_market` 或 `pre_close` 创建一次同步运行，按 `1a → 1b → 2 → 3a → 3b → 4 → 5` 保存状态。行情、事件、期权和决策均通过明确的 mock/disabled 边界运行，不访问外部网络；条件事件可通过 `simulate_macro_event` 和 `simulate_instrument_event` 验证。真实 provider 接入不属于 `main` 框架基线。

# Urus

Urus 是一个股票分析与决策辅助系统的前后端分离框架。`stage2` 在 `main` 骨架上接入 Moomoo 美股期权 LV1 快照，计算 DEX、GEX、Gamma Wall、Max Pain 和预期波动，并保留其余阶段的 mock 边界。

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

打开 `http://localhost:5173/options` 查看期权验证工作台；FastAPI OpenAPI 位于 `http://127.0.0.1:8000/docs`。

## 测试与构建

```bash
cd backend && uv run pytest
cd frontend && npm test && npm run build
```

## 框架行为

`POST /api/runs` 使用 `pre_market` 或 `pre_close` 创建一次同步运行，按 `1a → 1b → 2 → 3a → 3b → 4 → 5` 保存状态。启用 `MOOMOO_ENABLED=true` 后，第 2 步只调用期权链和行情快照接口，不订阅实时推送；开发环境强制只允许 QQQ、INTC。OptionCharts CSV 不是运行依赖，VEX/Vanna 本阶段不计算。

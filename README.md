# Urus

Urus 是一个股票分析与决策辅助系统的前后端分离框架。当前 `stage2` 已合入阶段 1A：大盘模块通过 Moomoo/OpenD 批量采集 ETF 快照并叠加 FRED/Yahoo 宏观上下文；期权模块通过 Moomoo 美股期权 LV1 快照计算 DEX、GEX、Gamma Wall、Max Pain 和预期波动。未实现模块继续明确标记为 mock、placeholder 或 unavailable。

阶段 1A 可按配置采集大盘和跨资产 ETF。阶段 2 固定覆盖 SPY、QQQ、SMH、IGV，并采集正式关注列表中的 15 个上市个股期权；私募标的 SPCX 保留为明确排除项。行权价结构图展示建模正负 Gamma 区间与符号切换位，并通过 Black–Scholes 现价网格生成 Spot Gamma Profile 与主 Gamma Flip。

## 目录

- `backend/`：FastAPI、SQLAlchemy、Alembic、七步骤工作流和 API 测试。
- `frontend/`：Vue 3、Vite、TypeScript、Pinia、Dashboard / Runs / Run Detail。
- `docs/implementation.md`：本轮实现说明、边界和后续接入点。

## 启动

先复制 `.env.example` 为 `.env`（不提交真实密钥；本轮不需要密钥）。分别启动后端和前端：

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

打开 Vite 输出的地址（默认 `http://localhost:5173`）。FastAPI OpenAPI 位于 `http://127.0.0.1:8000/docs`。

真实联调前确保 Moomoo OpenD 正在监听，并在根目录 `.env` 中设置 `MOOMOO_ENABLED=true`、正确的 `MOOMOO_HOST` 和 `MOOMOO_PORT`。如果 OpenD 未启动，采集步骤会保存连接失败，不会伪装成 mock 数据。

## 测试与构建

```bash
cd backend && uv run pytest
cd frontend && npm test && npm run build
```

后端启动时会为本地新 checkout 创建缺失表；正式的 schema 变更通过 Alembic 执行。数据库默认是 SQLite，可用 `DATABASE_URL` 切换到 SQLAlchemy 支持的 PostgreSQL URL。根目录 `.env` 使用用户提供的 `opend-host:11111`，启用 FRED 日频宏观源和每次运行必取的 Yahoo 源；如果 OpenD、FRED 或 Yahoo 尚未可用，阶段 1A 会把缺失和连接错误保存到运行记录和 read model，不会静默伪装成模拟数据。

## 阶段行为

`POST /api/runs` 使用 `pre_market` 或 `pre_close` 创建一次同步运行，按 `1a → 1b → 2 → 3a → 3b → 4 → 5` 保存状态。启用 `MOOMOO_ENABLED=true` 时，1A 获取 ETF 快照、QQQ 日线摘要和共享技术指标；第 2 步只调用期权链与行情快照接口，不订阅实时推送，并核对采集前后的订阅额度。OptionCharts CSV 不是运行依赖，VEX/Vanna 本阶段不计算。

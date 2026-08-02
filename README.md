# Urus

Urus 是一个股票分析与决策辅助系统的前后端分离框架。框架阶段已经完成；当前进入阶段 1A，通过 Moomoo/OpenD 批量采集 QQQ、SPY、IWM、行业和跨资产 ETF 快照，并叠加 FRED 日频宏观上下文与每次运行必取的 Yahoo VIX/收益率数据。网页事件、Anomalo、期权计算、个股采集和决策 AI 仍留到后续阶段单独讨论和验收。

当前开发白名单的个股部分仍围绕 `QQQ` 与 `INTC`；阶段 1A 会额外按配置批量采集大盘/跨资产 ETF。QQQ 卡片会标记 `Moomoo OpenD`，并叠加每次运行必取且优先使用的 Yahoo VIX/10Y/30Y、FRED 2Y 和交叉校验，以及 QQQ 日线波动率/ATR/布林带。INTC 当前为 `unavailable`，期权和决策为 `placeholder`，事件条件未命中时为 `skipped`；Moomoo 直接 VIX 指数按策略跳过，read model 会记录该策略状态。

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

阶段 1A 真实联调前，确保 Moomoo OpenD 在 `opend-host:11111` 监听，并在根目录 `.env` 中设置 `MOOMOO_ENABLED=true`、`MOOMOO_HOST=opend-host`、`MOOMOO_PORT=11111`。如果 OpenD 未启动，1A 会快速返回连接失败；不会把失败伪装成 mock 数据。

## 测试与构建

```bash
cd backend && uv run pytest
cd frontend && npm test && npm run build
```

后端启动时会为本地新 checkout 创建缺失表；正式的 schema 变更通过 Alembic 执行。数据库默认是 SQLite，可用 `DATABASE_URL` 切换到 SQLAlchemy 支持的 PostgreSQL URL。根目录 `.env` 使用用户提供的 `opend-host:11111`，启用 FRED 日频宏观源和每次运行必取的 Yahoo 源；如果 OpenD、FRED 或 Yahoo 尚未可用，阶段 1A 会把缺失和连接错误保存到运行记录和 read model，不会静默伪装成模拟数据。

## 框架阶段行为

`POST /api/runs` 使用 `pre_market` 或 `pre_close` 创建一次同步运行，按 `1a → 1b → 2 → 3a → 3b → 4 → 5` 保存状态。启用 `MOOMOO_ENABLED=true` 时，1A 通过一次 Moomoo OpenD 批量获取配置的 ETF 快照、QQQ 日线摘要和共享技术指标；Yahoo 每次获取 VIX/10Y/30Y 并优先选用，FRED 提供 2Y 和交叉校验。正常真实运行整体为 `mixed`，1A 为 `succeeded + live`，2/4 为 `placeholder`，3A 为 `unavailable`，1B/3B 按条件 `skipped`。请求可以带 `simulate_macro_event`、`simulate_instrument_event` 测试条件步骤；也可以在自动化测试中使用 `fail_step` 验证失败和错误 read model。当前不调用 Anomalo、新闻或事件网页。

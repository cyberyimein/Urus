# Urus backend

FastAPI + SQLAlchemy backend for the Urus framework. Stage 1A uses one batched Moomoo/OpenD snapshot for configured ETFs, QQQ daily history summary plus shared volatility/ATR/Bollinger indicators, and Yahoo/FRED daily macro context. Stage 3A reuses that adapter for a QQQ benchmark plus the full `INSTRUMENT_VALIDATION_SYMBOLS` universe (core ETFs and the public watchlist), persists daily bars and technical/relative-strength inputs, and exposes them in the frontend read model. Yahoo VIX/10Y/30Y values are requested and preferred on every run, while FRED supplies the official 2Y field and cross-checks. The remaining workflow steps are explicitly marked skipped, placeholder, or unavailable until their own stages.

## Commands

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest
```

The application also creates missing local tables on startup so a fresh checkout can be run directly. Alembic is the supported migration path for explicit schema management. Migration `0002_option_persistence` adds normalized SQLite tables for option batches, symbols, expirations, raw contracts, Spot Gamma Profile points, and Gamma Flips. Migration `0003_instrument_technical_persistence` adds 3A analysis batches, instrument snapshots, and daily bars. The frontend snapshot and normalized option/3A records are committed in one transaction.

## API

- `GET /api/health`
- `GET /api/version`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/snapshots/{snapshot_id}`
- `GET /api/snapshots/{snapshot_id}/frontend`
- `GET /api/watchlist`

OpenAPI is available at `/docs`.

## 定时采集真实数据（不调用 AI）

下面的常驻脚本使用东京时间固定执行三次采集，并在启动时检查后端；后端未运行时，脚本会自动启动它：

- `21:30`：`pre_market`（盘前）
- `04:00`：`pre_close`（尾盘前）
- `05:30`：`post_close_review`（盘后）

```bash
cd backend
uv run python scripts/schedule_market_data_collection.py
```

每次请求都会显式设置 `skip_ai_decision=true`。这个单次运行开关优先于后端的
`URUS_AGENT_ENABLED`，所以即使已有后端开启了 Agent，定时任务也不会调用 AI。若检测到一个不支持
该开关的旧后端，脚本会拒绝采集并提示重启，避免意外调用模型。

脚本默认跳过对应美东时间为周六或周日的时点（东京周六凌晨仍对应美东周五，因此会正常采集），
不自动识别美股节假日。可用 `--include-weekends` 覆盖周末过滤。启动后 180 分钟内错过的时点会补采，
同一时点通过状态文件防止重复执行。

运行日志、后端日志和防重复状态保存在 `backend/data/scheduled_collection/`。先手工验证一次可以运行：

```bash
cd backend
uv run python scripts/schedule_market_data_collection.py --once pre_market
```

建议把常驻命令交给 `launchd`、`tmux` 或其他进程守护工具；Caffeine 只负责阻止系统休眠，不负责在
脚本意外退出后重新启动进程。

Stage 1A/3A environment variables are `MOOMOO_ENABLED`, `MOOMOO_HOST`, `MOOMOO_PORT`, `MOOMOO_HISTORY_DAYS`, `MOOMOO_SDK_HOME`, `MOOMOO_MARKET_SYMBOLS`, `INSTRUMENT_VALIDATION_SYMBOLS`, `MARKET_TIMEZONE`, `FRED_ENABLED`, `FRED_BASE_URL`, `FRED_TIMEOUT_SECONDS`, `FRED_LOOKBACK_DAYS`, `YAHOO_ENABLED`, `YAHOO_BASE_URL`, `YAHOO_TIMEOUT_SECONDS`, and `YAHOO_LOOKBACK_DAYS`. The OpenD context uses one batched snapshot for the configured ETF universe and does not request US indexes; 3A adds QQQ as a benchmark and does not create a realtime subscription. Yahoo is requested on every run and its VIX/10Y/30Y values are selected when available; FRED supplies 2Y and retains cross-check values. The API exposes `data_state` independently from execution status so AI evidence cannot treat placeholder execution as collected data.

Stage 4B Urus Agent is disabled unless `URUS_AGENT_ENABLED=true` and `OPENROUTER_API_KEY` are configured. It runs as a backend Step 4 research task without a chat UI. Anomalo remains the scheduled-event/news investigator; Urus Agent reads frozen evidence, calls only read-only Urus data/math tools, and saves decisions, sessions, model turns, and tool traces in SQLite. Market analysis runs first, all non-empty theme invocations use bounded concurrency (`URUS_AGENT_THEME_MAX_CONCURRENCY`, default `6`), and SQLite/Trace writes remain serial. Option-chain metrics are reduced by deterministic code to equity entry/timing context and passed to synthesis; no separate option-strategy Agent is invoked. Open a run's `/runs/{run_id}/report` page for the three report tabs. See `docs/stage4b-ai-decision.md` and `docs/urus-agent-design-requirements.md`.

Stage 2 uses `OPTIONS_TARGET_SYMBOLS` for core ETFs and `OPTIONS_WATCHLIST_SYMBOLS` for listed single stocks. `OPTIONS_WATCHLIST_EXCLUDED_SYMBOLS` records non-queryable names such as private SPCX. Snapshot and option-chain pacing are configured through `OPTIONS_SNAPSHOT_INTERVAL_SECONDS` and `OPTIONS_CHAIN_INTERVAL_SECONDS`.

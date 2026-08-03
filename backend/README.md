# Urus backend

FastAPI + SQLAlchemy backend for the Urus framework. Stage 1A uses one batched Moomoo/OpenD snapshot for configured ETFs, QQQ daily history summary plus shared volatility/ATR/Bollinger indicators, and Yahoo/FRED daily macro context; Yahoo VIX/10Y/30Y values are requested and preferred on every run, while FRED supplies the official 2Y field and cross-checks. The remaining workflow steps are explicitly marked skipped, placeholder, or unavailable until their own stages.

## Commands

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest
```

The application also creates missing local tables on startup so a fresh checkout can be run directly. Alembic is the supported migration path for explicit schema management. Migration `0002_option_persistence` adds normalized SQLite tables for option batches, symbols, expirations, raw contracts, Spot Gamma Profile points, and Gamma Flips. The frontend snapshot and these option records are committed in one transaction.

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

Stage 1A environment variables are `MOOMOO_ENABLED`, `MOOMOO_HOST`, `MOOMOO_PORT`, `MOOMOO_HISTORY_DAYS`, `MOOMOO_SDK_HOME`, `MOOMOO_MARKET_SYMBOLS`, `MARKET_TIMEZONE`, `FRED_ENABLED`, `FRED_BASE_URL`, `FRED_TIMEOUT_SECONDS`, `FRED_LOOKBACK_DAYS`, `YAHOO_ENABLED`, `YAHOO_BASE_URL`, `YAHOO_TIMEOUT_SECONDS`, and `YAHOO_LOOKBACK_DAYS`. The OpenD context uses one batched snapshot for the configured ETF universe and does not request US indexes. Yahoo is requested on every run and its VIX/10Y/30Y values are selected when available; FRED supplies 2Y and retains cross-check values. The API exposes `data_state` independently from execution status so AI evidence cannot treat placeholder execution as collected data.

Stage 2 uses `OPTIONS_TARGET_SYMBOLS` for core ETFs and `OPTIONS_WATCHLIST_SYMBOLS` for listed single stocks. `OPTIONS_WATCHLIST_EXCLUDED_SYMBOLS` records non-queryable names such as private SPCX. Snapshot and option-chain pacing are configured through `OPTIONS_SNAPSHOT_INTERVAL_SECONDS` and `OPTIONS_CHAIN_INTERVAL_SECONDS`.

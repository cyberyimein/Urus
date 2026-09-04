# Urus backend

FastAPI + SQLAlchemy backend for the current Observation and Decision Harness. The backend owns the data-source adapters, canonical daily bars, option analytics, quota admission, deterministic strategies, and the optional Remote Decision seam.

## Commands

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest
```

The application creates missing local tables on startup for a fresh checkout. Alembic remains the supported path for explicit schema management. Existing migration files are kept as database history; runtime code uses the current models only.

## Collection boundary

`POST /api/observation/runs` is the single collection entry point. It freezes the active Universe groups, admits required daily-history symbols through `HistoryAdmission`, collects stock/ETF evidence, runs deterministic strategies, and collects the configured option symbols through `OptionsCollectionService`.

The scheduler invokes this endpoint once after the US session. It never starts a separate data worker and it never invokes AI. The stock and option adapters share `MoomooCollectionCoordinator`, which holds the host-wide lock and persists pacing timestamps for snapshot, history, option metadata, and option-chain calls.

The optional intraday option collector is a separate supervised process because
it runs every five minutes during the regular session. It still uses the same
host-wide coordinator and rate buckets. Set `INTRADAY_OPTIONS_ENABLED=true` to
collect SPY/QQQ/IWM facts into the dedicated
`/data/intraday_options/intraday_options-v2.db`; compressed raw capture payloads
are stored under `/data/intraday_options/raw`. These tables are intentionally
not part of `urus.db` or the main Alembic history.
Incomplete captures retry inside the four-minute temporal window without
mixing attempts. A post-close pass refreshes finalized one-minute bars, writes
supervised outcome labels, and creates an app-consistent daily backup.
SPY/QQQ/IWM take history-symbol capacity before the broader Urus observation
universe; normal history reserve policy is applied only to the remaining slots.

## API surface

- `GET/PUT /api/settings`
- `GET/PUT /api/settings/universe`
- `POST /api/daily-evidence/datasets`
- `GET /api/daily-evidence/datasets/{dataset_id}`
- `GET /api/observation/groups`
- `POST /api/observation/groups/sync`
- `POST/GET /api/observation/runs`
- `GET /api/observation/options`
- `GET/POST /api/remote-decisions`

OpenAPI is available at `/docs`.

## Data sources

Moomoo/OpenD supplies quote snapshots and completed daily bars. FRED and Yahoo are optional daily macro adapters. Option analytics are snapshot-only: DEX, GEX, Gamma Wall, Max Pain, Expected Move, IV/HV context, and Spot Gamma Profile are derived from the returned chain without subscribing to option streams.

The post-close alignment uses only the official regular close. A close near Max Pain or a DEX Wall is recorded as a deterministic structural candidate; it is not a causal claim. If the regular close is unavailable, the result stays indeterminate and the option spot is not promoted to a formal close.

## Runtime settings

The only persisted scheduler setting is `schedule.post_close_observation.enabled`. Removed settings are ignored when old rows are read, but new responses and writes contain only the current shape. Remote Workflow credentials are environment/Secret Store inputs and are never returned by `/api/settings`.

## Backfill

To populate the order-size capital-flow cache for the latest completed sessions:

```bash
cd backend
uv run python scripts/backfill_capital_flows.py
```

The script is optional and does not alter the Observation collection contract.

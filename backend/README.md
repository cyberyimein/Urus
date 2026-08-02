# Urus backend

FastAPI + SQLAlchemy backend for the Urus framework. The baseline is offline-first: workflow steps use deterministic adapters and store a small frontend read model in SQLite. Provider clients are added in later stage branches.

## Commands

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest
```

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

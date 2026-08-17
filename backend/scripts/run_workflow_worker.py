"""Execute one queued workflow in a recyclable child process."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from app.core.config import Settings
    from app.core.database import create_database
    from app.repositories.runtime_settings import RuntimeSettingsRepository, apply_payload
    from app.schemas.read_model import RunCreateRequest
    from app.services.run_service import RunService

    settings = Settings()
    request = RunCreateRequest.model_validate_json(args.request_json)
    engine, session_factory = create_database(settings.database_url)
    try:
        with session_factory() as session:
            persisted = RuntimeSettingsRepository(session).get()
            if persisted is not None:
                apply_payload(settings, persisted.payload)
            RunService(session, settings).create_run(request, queued_run_id=args.run_id)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

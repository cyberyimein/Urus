#!/usr/bin/env python3
"""Load an administrator-approved workflow-bindings.json into Urus.

The release/register step is the only place that uses Anomalo's management
token. This hand-off script reads the resulting, credential-free binding file
and writes only the published Workflow metadata to the Urus database.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Make the standalone script behave the same when invoked from the repository
# root or from ``backend/`` (``python scripts/load_workflow_bindings.py``).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings
from app.core.database import create_database
from app.repositories.remote_decision import RemoteDecisionRepository


DEFAULT_BINDINGS = ROOT / "workflow-bindings.json"
WORKFLOW_REF = re.compile(r"^[a-z][a-z0-9-]{0,63}@([1-9][0-9]*)$")
INTENTS = {
    "instrument_arbitration",
    "group_arbitration",
    "indicator_attention",
    "strategy_attention",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings-in", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///./urus.db"))
    args = parser.parse_args()

    payload = json.loads(args.bindings_in.read_text(encoding="utf-8"))
    bindings = payload.get("bindings") if isinstance(payload, dict) else None
    if not isinstance(bindings, list) or not bindings:
        raise SystemExit("binding file must contain a non-empty bindings array")

    engine, session_factory = create_database(args.database_url)
    loaded = 0
    try:
        with session_factory() as session:
            repository = RemoteDecisionRepository(session)
            for binding in bindings:
                _validate_binding(binding)
                repository.save_binding(dict(binding))
                loaded += 1
    finally:
        engine.dispose()
    print(f"loaded {loaded} workflow binding(s) into {args.database_url}")
    return 0


def _validate_binding(value: Any) -> None:
    if not isinstance(value, dict):
        raise SystemExit("each binding must be an object")
    intent = str(value.get("intent_type") or "")
    workflow_ref = str(value.get("workflow_ref") or "")
    if intent not in INTENTS:
        raise SystemExit(f"unsupported intent_type: {intent}")
    if not WORKFLOW_REF.fullmatch(workflow_ref):
        raise SystemExit(f"invalid workflow_ref: {workflow_ref}")
    for key in ("definition_hash", "compiled_hash", "capability_manifest_hash"):
        value_text = str(value.get(key) or "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", value_text):
            raise SystemExit(f"{key} must be a 64-character lowercase SHA-256 hash")
    if str(value.get("status") or "") == "active" and not value.get("verified_at"):
        raise SystemExit("active binding requires verified_at")


if __name__ == "__main__":
    raise SystemExit(main())

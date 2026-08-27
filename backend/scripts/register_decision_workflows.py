#!/usr/bin/env python3
"""Release the four Urus decision Workflows through Anomalo's management API.

This script intentionally keeps the admin token in process memory only. The
runtime service consumes the generated binding file and uses a separate
workflow:run token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFINITION_DIR = ROOT / "app" / "decision_harness" / "workflow_definitions"
DEFAULT_FILES = (
    "urus-instrument-arbitration-v3.json",
    "urus-group-arbitration-v3.json",
    "urus-indicator-review-v3.json",
    "urus-strategy-review-v3.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("ANOMALOHARIS_BASE_URL", ""))
    parser.add_argument("--admin-token", default=os.getenv("ANOMALOHARIS_ADMIN_TOKEN", ""))
    parser.add_argument("--definition-dir", type=Path, default=DEFINITION_DIR)
    parser.add_argument("--bindings-out", type=Path, default=ROOT / "workflow-bindings.json")
    parser.add_argument("--skip-publish", action="store_true", help="validate/import only")
    args = parser.parse_args()
    if not args.base_url or not args.admin_token:
        parser.error("--base-url and --admin-token (or corresponding environment variables) are required")

    # Keep the auth header at client scope.  httpx adds JSON content headers
    # when ``json=...`` is supplied, while the publish endpoint intentionally
    # accepts an empty POST body and Fastify rejects an empty JSON body.
    headers = {"X-AnomaloHaris-Admin-Token": args.admin_token}
    bindings: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=120.0) as client:
        manifest_response = client.get("/api/manage/workflow-capabilities?download=true")
        manifest_response.raise_for_status()
        manifest = manifest_response.json()
        manifest_hash = _hash(manifest)
        for filename in DEFAULT_FILES:
            definition = json.loads((args.definition_dir / filename).read_text(encoding="utf-8"))
            schema_dir = args.definition_dir.parent / "remote_schemas"
            definition = _inline_schema_refs(definition, schema_dir)
            # Definition refs are resolved by Anomalo; this value is only a
            # local audit hint and never lets Urus override a Preset Model.
            definition.setdefault("compatibility", {})["authored_against_manifest_hash"] = f"sha256:{manifest_hash}"
            validation_response = client.post("/api/manage/workflows/validate", json=definition)
            validation_response.raise_for_status()
            validation = (validation_response.json().get("validation") or {})
            if not validation.get("valid"):
                raise RuntimeError(f"{filename} failed validation: {json.dumps(validation.get('errors') or [], ensure_ascii=False)}")
            metadata = definition.get("metadata") or {}
            name = str(metadata.get("name") or "")
            version = int(metadata.get("version"))
            existing_response = client.get(f"/api/manage/workflows/{name}/versions/{version}")
            if existing_response.status_code not in (200, 404):
                existing_response.raise_for_status()
            existing = (
                existing_response.json().get("workflow")
                if existing_response.status_code == 200
                and isinstance(existing_response.json(), dict)
                else None
            )
            if existing is None:
                imported = client.post("/api/manage/workflows/import", json=definition)
                if imported.status_code == 409:
                    # Another release process may have created the exact Ref
                    # between the GET and POST. Re-read it and continue only
                    # when the server can prove that the Ref now exists.
                    existing_response = client.get(f"/api/manage/workflows/{name}/versions/{version}")
                    if existing_response.status_code != 200:
                        imported.raise_for_status()
                    existing = existing_response.json().get("workflow")
                else:
                    imported.raise_for_status()
                    existing = imported.json().get("workflow") if isinstance(imported.json(), dict) else None
            if not isinstance(existing, dict):
                raise RuntimeError(f"{filename} import returned no Workflow record")
            validation_definition_hash = _strip_hash(validation.get("definition_hash"))
            _assert_existing_definition_matches(
                filename=filename,
                name=name,
                version=version,
                existing=existing,
                validation_definition_hash=validation_definition_hash,
            )
            if existing.get("status") == "retired":
                raise RuntimeError(f"{filename} refers to retired Workflow {name}@{version}")
            if existing.get("status") != "published" and not args.skip_publish:
                # Do not send an empty JSON body. Fastify rejects POST requests
                # with Content-Type application/json when no body is present.
                publish = client.post(f"/api/manage/workflows/{name}/versions/{version}/publish")
                publish.raise_for_status()
                existing = publish.json().get("workflow") if isinstance(publish.json(), dict) else existing
            published_at = existing.get("published_at")
            if not published_at and not args.skip_publish:
                published_at = datetime.now(timezone.utc).isoformat()
            bindings.append(
                {
                    "intent_type": _intent_for_name(name),
                    "workflow_ref": f"{name}@{version}",
                    "status": "disabled" if args.skip_publish else ("active" if existing.get("status") == "published" else "disabled"),
                    "definition_hash": _strip_hash(existing.get("definition_hash")) or validation_definition_hash or _hash(definition),
                    "compiled_hash": _strip_hash(existing.get("compiled_hash")) or _strip_hash(validation.get("compiled_hash")) or "",
                    "capability_manifest_hash": _strip_hash(existing.get("capability_manifest_hash")) or _strip_hash(validation.get("capability_manifest_hash")) or manifest_hash,
                    "input_schema_version": "urus.remote_decision_input.v1",
                    "output_schema_version": "urus.remote_decision_artifact.v1",
                    "published_at": published_at,
                    "verified_at": published_at,
                    "definition_json": existing.get("definition") or definition,
                    # Keep the exact manifest used for validation in the
                    # release hand-off; it contains capabilities only and no
                    # runtime/admin credentials.
                    "manifest_json": manifest,
                }
            )
    args.bindings_out.write_text(json.dumps({"bindings": bindings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(bindings)} binding(s) to {args.bindings_out}")
    return 0


def _intent_for_name(name: str) -> str:
    return {
        "urus-instrument-arbitration": "instrument_arbitration",
        "urus-group-arbitration": "group_arbitration",
        "urus-indicator-review": "indicator_attention",
        "urus-strategy-review": "strategy_attention",
    }.get(name, name)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _strip_hash(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).removeprefix("sha256:")


def _assert_existing_definition_matches(
    *,
    filename: str,
    name: str,
    version: int,
    existing: dict[str, Any],
    validation_definition_hash: str | None,
) -> None:
    """Prevent an existing Ref from silently pointing at another definition."""

    stored_definition_hash = _strip_hash(existing.get("definition_hash"))
    existing_status = str(existing.get("status") or "")
    if existing_status in {"published", "draft"} and (not stored_definition_hash or not validation_definition_hash):
        raise RuntimeError(
            f"{filename} cannot verify existing {name}@{version}; "
            "both stored and validated definition hashes are required"
        )
    if not stored_definition_hash or not validation_definition_hash or stored_definition_hash == validation_definition_hash:
        return
    if existing_status == "published":
        raise RuntimeError(
            f"{filename} does not match published {name}@{version}; "
            "publish a new version instead of rebinding this Ref"
        )
    if existing_status == "draft":
        raise RuntimeError(
            f"{filename} conflicts with existing draft {name}@{version}; "
            "review or delete the draft before publishing"
        )
    raise RuntimeError(
        f"{filename} does not match existing {name}@{version}; "
        "refusing to bind a stale Workflow definition"
    )


def _inline_schema_refs(definition: dict[str, Any], schema_dir: Path) -> dict[str, Any]:
    """Expand local schema references before the management API validates."""

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("remote_"):
                path = schema_dir / f"{ref}.json"
                if path.is_file():
                    return json.loads(path.read_text(encoding="utf-8"))
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    return replace(definition)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field, field_validator

from app.buy_opportunities.contracts import OpportunityModel
from app.decision_harness.contracts import content_sha256


class ResearchManifest(OpportunityModel):
    schema_version: str = "urus.buy_opportunity.research_manifest.v1"
    run_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    pool_version: str = Field(min_length=1, max_length=128)
    knowledge_at: datetime
    source_mode: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    universe_symbols: list[str] = Field(default_factory=list)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    content_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("created_at", "knowledge_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest 时间戳必须包含时区")
        return value

    @field_validator("content_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("manifest content_sha256 必须是十六进制 SHA-256")
        return value.lower()

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        created_at: datetime,
        pool_version: str,
        knowledge_at: datetime,
        source_mode: str,
        parameters: dict[str, Any],
        universe_symbols: list[str],
        input_hashes: dict[str, str],
    ) -> "ResearchManifest":
        payload = {
            "schema_version": "urus.buy_opportunity.research_manifest.v1",
            "run_id": run_id,
            "created_at": created_at.isoformat(),
            "pool_version": pool_version,
            "knowledge_at": knowledge_at.isoformat(),
            "source_mode": source_mode,
            "parameters": parameters,
            "universe_symbols": sorted(universe_symbols),
            "input_hashes": dict(sorted(input_hashes.items())),
        }
        return cls(**payload, content_sha256=content_sha256(payload))


def write_jsonl_atomic(
    path: str | Path,
    *,
    manifest: ResearchManifest,
    records: Iterable[OpportunityModel | dict[str, Any]],
) -> None:
    """Write a manifest followed by immutable records without partial output."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "manifest", **manifest.model_dump(mode="json")}, ensure_ascii=False)]
    for record in records:
        payload = record.model_dump(mode="json") if isinstance(record, OpportunityModel) else dict(record)
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    content = "\n".join(lines) + "\n"
    if destination.exists():
        existing = destination.read_text(encoding="utf-8")
        if existing == content:
            return
        raise FileExistsError(f"研究 artifact 已存在且内容不同：{destination}")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError:
            existing = destination.read_text(encoding="utf-8")
            if existing != content:
                raise FileExistsError(f"研究 artifact 已存在且内容不同：{destination}")
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

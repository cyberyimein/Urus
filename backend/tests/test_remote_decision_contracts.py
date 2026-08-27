from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.remote_decision import (
    RemoteDecisionArtifact,
    RemoteDecisionIntent,
    RemoteDecisionPreflightRequest,
    RemoteDecisionSource,
)
from app.decision_harness.cross_section import _attach_attention_features
from app.decision_harness.contracts import content_sha256
from app.decision_harness.remote_workflow import _reference_is_allowed, _reference_key, validate_artifact
from app.repositories.remote_decision import _safe_json


def test_source_locator_is_strict_and_normalised() -> None:
    request = RemoteDecisionPreflightRequest(
        intent_type=RemoteDecisionIntent.INDICATOR_ATTENTION,
        source={
            "observation_run_id": "run-1",
            "lens_id": " rsi14 ",
        },
    )

    assert request.source.observation_run_id == "run-1"
    assert request.source.lens_id == "rsi14"


def test_source_locator_rejects_frontend_evidence_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RemoteDecisionSource(observation_run_id="run-1", rows=[])


def test_artifact_rejects_unknown_fields_and_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        RemoteDecisionArtifact(
            schema_version="urus.remote_decision_artifact.v1",
            intent_type=RemoteDecisionIntent.GROUP_ARBITRATION,
            scope={},
            input_sha256="a" * 64,
            completeness="complete",
            decision={},
            warnings=[],
            evidence_refs=[],
            confidence=1.2,
            generated_at=datetime.now(timezone.utc),
            unexpected="must fail",
        )


def test_artifact_requires_top_level_dataset_id() -> None:
    with pytest.raises(ValidationError):
        RemoteDecisionArtifact(
            schema_version="urus.remote_decision_artifact.v1",
            intent_type=RemoteDecisionIntent.INSTRUMENT_ARBITRATION,
            scope={"scope_type": "instrument", "scope_id": "INTC", "symbol": "INTC"},
            input_sha256="a" * 64,
            completeness="complete",
            decision={"decision": "no_action"},
            warnings=[],
            evidence_refs=[],
        )


def test_attention_features_use_midrank_and_null_small_samples() -> None:
    rows = [
        {"id": str(index), "group_id": "g", "valid": True, "value": value, "change": value / 10, "transition": None}
        for index, value in enumerate([1, 1, 2, 4])
    ]
    _attach_attention_features(rows, kind="indicator")
    assert rows[0]["attention_features"]["global_percentile"] == rows[1]["attention_features"]["global_percentile"]
    assert rows[0]["attention_features"]["global_percentile"] == 0.1667

    small = [{"id": str(index), "group_id": "g", "valid": True, "value": index, "change": 0, "transition": None} for index in range(3)]
    _attach_attention_features(small, kind="indicator")
    assert all(item["attention_features"]["global_percentile"] is None for item in small)


def test_strategy_cross_section_card_decision_id_is_checked_against_projection_rows() -> None:
    row = {
        "id": "theme:NVDA:trend_momentum",
        "group_id": "theme",
        "symbol": "NVDA",
        "decision_id": "decision-1",
        "valid": True,
    }
    input_payload = {
        "schema_version": "urus.remote_decision_input.v1",
        "intent": {
            "type": "strategy_attention",
            "trigger_mode": "user",
            "trigger_source": "strategy_cross_section",
        },
        "scope": {
            "scope_type": "observation_run",
            "scope_id": "run-1",
            "lens": {"type": "strategy", "id": "trend_momentum"},
        },
        "dataset": {"dataset_ids": [], "schema_version": "urus.cross_section_projection.v1"},
        "evidence": {"projection": {"rows": [row]}},
        "strategy_decisions": [
            {"decision_id": row["decision_id"], "strategy": {"name": "trend_momentum"}}
        ],
        "deterministic_synthesis": {},
        "quality": {"status": "ok"},
        "constraints": {
            "allowed_symbols": ["NVDA"],
            "allow_latest_data_lookup": False,
            "allow_symbol_expansion": False,
        },
        "rows": [row],
        "evidence_refs": [],
    }
    input_payload["input_sha256"] = content_sha256(input_payload)
    run = SimpleNamespace(
        intent_type="strategy_attention",
        input_json=input_payload,
        input_sha256=input_payload["input_sha256"],
        scope_type="observation_run",
        scope_id="run-1",
        dataset_id=None,
        lens_id="trend_momentum",
        lens_type="strategy",
        source_snapshot_id=None,
    )
    artifact, issue = validate_artifact(
        run,
        {
            "schema_version": "urus.remote_decision_artifact.v1",
            "intent_type": "strategy_attention",
            "scope": {
                "scope_type": "observation_run",
                "scope_id": "run-1",
                "lens": {"type": "strategy", "id": "trend_momentum"},
            },
            "dataset_id": None,
            "input_sha256": input_payload["input_sha256"],
            "completeness": "complete",
            "decision": {},
            "notable_cards": [
                {
                    "rank": 1,
                    "card_id": row["id"],
                    "group_id": row["group_id"],
                    "symbol": row["symbol"],
                    "strategy_decision_id": row["decision_id"],
                    "finding_type": "score_outlier",
                }
            ],
            "warnings": [],
            "evidence_refs": [],
        },
    )
    assert issue is None
    assert artifact is not None


def test_evidence_reference_identity_keeps_all_immutable_ids() -> None:
    allowed = {
        _reference_key(
            {
                "kind": "strategy_decision",
                "dataset_id": "dataset-1",
                "decision_id": "decision-1",
                "symbol": "NVDA",
                "path": "strategy_decisions[]",
            }
        )
    }
    assert _reference_is_allowed(
        {
            "kind": "strategy_decision",
            "dataset_id": "dataset-1",
            "decision_id": "decision-1",
            "symbol": "NVDA",
            "path": "strategy_decisions[0]",
        },
        allowed,
    )
    assert not _reference_is_allowed(
        {
            "kind": "strategy_decision",
            "dataset_id": "dataset-1",
            "decision_id": "decision-2",
            "symbol": "NVDA",
            "path": "strategy_decisions[]",
        },
        allowed,
    )


def test_workflow_event_redaction_covers_prefixed_credentials() -> None:
    safe = _safe_json(
        {
            "x-anomalo-service-token": "secret-a",
            "admin_token": "secret-b",
            "apiKey": "secret-c",
            "adminToken": "secret-camel-case",
            "nested": {"refresh-token": "secret-d"},
            "message": "Authorization: Bearer secret-e",
            "visible": "status=running",
        }
    )
    assert "x-anomalo-service-token" not in safe
    assert "admin_token" not in safe
    assert "apiKey" not in safe
    assert "adminToken" not in safe
    assert safe["nested"] == {}
    assert safe["message"] == "[redacted]"
    assert safe["visible"] == "status=running"

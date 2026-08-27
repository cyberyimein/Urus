from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.decision_harness.contracts import canonical_json, content_sha256
from app.decision_harness.cross_section import CrossSectionService
from app.models.remote_decision import DecisionWorkflowBindingModel, RemoteDecisionRunModel
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.repositories.observation import ObservationRepository
from app.repositories.remote_decision import RemoteDecisionRepository
from app.schemas.remote_decision import (
    RemoteDecisionArtifact,
    RemoteDecisionBindingSummary,
    RemoteDecisionIntent,
    RemoteDecisionIssue,
    RemoteDecisionPreflightResponse,
    RemoteDecisionSource,
)


INPUT_SCHEMA_VERSION = "urus.remote_decision_input.v1"
ARTIFACT_SCHEMA_VERSION = "urus.remote_decision_artifact.v1"

WORKFLOW_REFS: dict[str, str] = {
    RemoteDecisionIntent.INSTRUMENT_ARBITRATION.value: "urus-instrument-arbitration@2",
    RemoteDecisionIntent.GROUP_ARBITRATION.value: "urus-group-arbitration@2",
    RemoteDecisionIntent.INDICATOR_ATTENTION.value: "urus-indicator-review@2",
    RemoteDecisionIntent.STRATEGY_ATTENTION.value: "urus-strategy-review@2",
}


@dataclass
class CompiledDecision:
    intent_type: str
    source: dict[str, Any]
    input_payload: dict[str, Any]
    input_sha256: str
    preflight_fingerprint: str | None
    binding: DecisionWorkflowBindingModel | None
    source_summary: dict[str, Any]
    scope_type: str
    scope_id: str
    scope_version: str | None
    dataset_id: str | None
    lens_type: str | None
    lens_id: str | None
    lens_version: str | None
    source_dataset_id: str | None
    source_snapshot_id: str | None
    source_observation_run_id: str | None
    warnings: list[RemoteDecisionIssue] = field(default_factory=list)
    blockers: list[RemoteDecisionIssue] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return not self.blockers and self.binding is not None and self.preflight_fingerprint is not None


class RemoteDecisionCompiler:
    """Compile exact frozen Phase C evidence into a Workflow input envelope."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.remote_repository = RemoteDecisionRepository(session)
        self.daily_repository = DailyEvidenceRepository(session)
        self.observation_repository = ObservationRepository(session)

    def compile(self, intent_type: RemoteDecisionIntent | str, source: RemoteDecisionSource | dict[str, Any]) -> CompiledDecision:
        intent = intent_type.value if isinstance(intent_type, RemoteDecisionIntent) else str(intent_type)
        source_error: ValidationError | None = None
        if isinstance(source, RemoteDecisionSource):
            locator = source.model_dump(exclude_none=True)
        else:
            try:
                # Keep the compiler strict even when it is called directly by a
                # worker/CLI rather than through FastAPI's request model.
                locator = RemoteDecisionSource.model_validate(source).model_dump(exclude_none=True)
            except ValidationError as exc:
                locator = {}
                source_error = exc
        result = CompiledDecision(
            intent_type=intent,
            source=locator,
            input_payload={},
            input_sha256="",
            preflight_fingerprint=None,
            binding=self.remote_repository.get_binding(intent),
            source_summary={},
            scope_type="",
            scope_id="",
            scope_version=None,
            dataset_id=None,
            lens_type=None,
            lens_id=None,
            lens_version=None,
            source_dataset_id=None,
            source_snapshot_id=None,
            source_observation_run_id=None,
        )
        if source_error is not None:
            result.blockers.append(
                _issue(
                    "source_scope_mismatch",
                    "source locator 只能包含不可变对象身份，不能包含前端 evidence 或未知字段。",
                    {"errors": source_error.errors(include_url=False)[:8]},
                )
            )
            return result
        if intent not in WORKFLOW_REFS:
            result.blockers.append(_issue("intent_not_supported", "不支持的 AI 决策入口。"))
            return result
        if result.binding is None:
            result.blockers.append(
                _issue(
                    "workflow_binding_unavailable",
                    "尚未配置已验证的 Workflow Binding，当前 AI 入口不可用。",
                    {"workflow_ref": WORKFLOW_REFS[intent]},
                )
            )
        elif result.binding.workflow_ref != WORKFLOW_REFS[intent]:
            result.blockers.append(
                _issue(
                    "workflow_ref_mismatch",
                    "当前 Binding 的 Workflow Ref 与入口意图不匹配。",
                    {"expected": WORKFLOW_REFS[intent], "actual": result.binding.workflow_ref},
                )
            )
        elif result.binding.input_schema_version != INPUT_SCHEMA_VERSION:
            result.blockers.append(_issue("workflow_schema_mismatch", "Binding 输入 Schema 版本与 Urus 不一致。"))
        elif result.binding.output_schema_version != ARTIFACT_SCHEMA_VERSION:
            result.blockers.append(_issue("workflow_schema_mismatch", "Binding 输出 Schema 版本与 Urus 不一致。"))
        elif not all(
            _valid_hash(value)
            for value in (result.binding.definition_hash, result.binding.compiled_hash, result.binding.capability_manifest_hash)
        ):
            result.blockers.append(_issue("workflow_binding_unavailable", "Binding hash 无效。"))
        if not self.settings.anomalo_workflow_enabled:
            result.blockers.append(_issue("workflow_disabled", "Anomalo Workflow 运行开关未启用。"))
        elif (
            self.settings.app_env != "test"
            and not self.settings.anomalo_workflow_fake_adapter
            and (not self.settings.anomalo_base_url or not self.settings.anomalo_workflow_token)
        ):
            result.blockers.append(_issue("workflow_runtime_unconfigured", "Anomalo Workflow URL 或运行 token 尚未配置。"))

        try:
            if intent == RemoteDecisionIntent.INSTRUMENT_ARBITRATION.value:
                self._compile_instrument(result)
            elif intent == RemoteDecisionIntent.GROUP_ARBITRATION.value:
                self._compile_group(result)
            else:
                self._compile_cross_section(result)
        except AppError as exc:
            result.blockers.append(_issue(exc.code, exc.message, exc.details if isinstance(exc.details, dict) else {}))
        except (KeyError, TypeError, ValueError) as exc:
            result.blockers.append(_issue("source_not_frozen", f"冻结证据无法编译：{exc}"))

        if result.input_payload:
            result.input_sha256 = _hash_without_input_hash(result.input_payload)
            result.input_payload["input_sha256"] = result.input_sha256
            if result.binding is not None:
                result.preflight_fingerprint = _preflight_fingerprint(
                    result.intent_type,
                    result.source,
                    result.input_sha256,
                    result.binding.workflow_ref,
                    result.binding.compiled_hash,
                )
            encoded_size = len(canonical_json(result.input_payload).encode("utf-8"))
            if encoded_size > max(1, int(self.settings.remote_decision_max_input_bytes)):
                result.blockers.append(
                    _issue(
                        "input_too_large",
                        "AI Workflow 输入超过大小上限，请先缩小确定性投影。",
                        {"bytes": encoded_size, "max_bytes": self.settings.remote_decision_max_input_bytes},
                    )
                )
        return result

    def preflight_response(self, compiled: CompiledDecision) -> RemoteDecisionPreflightResponse:
        binding = None
        if compiled.binding is not None:
            binding = RemoteDecisionBindingSummary(
                intent_type=compiled.intent_type,
                workflow_ref=compiled.binding.workflow_ref,
                status=compiled.binding.status,
                definition_hash=compiled.binding.definition_hash,
                compiled_hash=compiled.binding.compiled_hash,
                capability_manifest_hash=compiled.binding.capability_manifest_hash,
                input_schema_version=compiled.binding.input_schema_version,
                output_schema_version=compiled.binding.output_schema_version,
            )
        return RemoteDecisionPreflightResponse(
            enabled=compiled.enabled,
            blockers=compiled.blockers,
            warnings=compiled.warnings,
            intent_type=RemoteDecisionIntent(compiled.intent_type),
            source=RemoteDecisionSource(**compiled.source),
            source_summary=compiled.source_summary,
            binding=binding,
            input_sha256=compiled.input_sha256 or None,
            preflight_fingerprint=compiled.preflight_fingerprint,
        )

    def _compile_instrument(self, result: CompiledDecision) -> None:
        dataset_id = result.source.get("dataset_id")
        symbol = str(result.source.get("symbol") or "").upper()
        if not dataset_id or not symbol:
            result.blockers.append(_issue("source_scope_mismatch", "个股仲裁需要 dataset_id 与 symbol。"))
            return
        dataset = self.daily_repository.dataset(str(dataset_id))
        if dataset is None:
            result.blockers.append(_issue("source_not_found", "找不到指定的 Daily Decision Dataset。", {"dataset_id": dataset_id}))
            return
        payload = dict(dataset.payload_json or {})
        scope = dict(payload.get("scope") or {})
        symbols = {str(item).upper() for item in scope.get("symbols") or []}
        if (
            dataset.scope_type != "instrument"
            or str(dataset.scope_id).upper() != symbol
            or scope.get("scope_type") != "instrument"
            or str(scope.get("scope_id") or "").upper() != symbol
            or symbols != {symbol}
        ):
            result.blockers.append(_issue("source_scope_mismatch", "dataset 不包含 locator 指定的 symbol。", {"symbol": symbol}))
            return
        if result.source.get("content_sha256") and str(result.source["content_sha256"]) != str(dataset.content_sha256):
            result.blockers.append(_issue("source_version_conflict", "locator 的 content_sha256 与 Daily Decision Dataset 不一致。"))
            return
        if payload.get("dataset_id") and str(payload.get("dataset_id")) != str(dataset.id):
            result.blockers.append(_issue("source_version_conflict", "Daily Decision Dataset payload 身份与数据库记录不一致。"))
            return
        if payload.get("content_sha256") and str(payload.get("content_sha256")) != str(dataset.content_sha256):
            result.blockers.append(_issue("source_version_conflict", "Daily Decision Dataset payload 的 content hash 无法核对。"))
            return
        if "local-demo" in str(payload.get("schema_version") or "").lower():
            result.blockers.append(_issue("local_demo_forbidden", "LOCAL DEMO 证据不能发起正式 AI Workflow。"))
            return
        if str(payload.get("status") or "") in {"unavailable", "error"}:
            result.blockers.append(_issue("no_valid_evidence", "当前 dataset 没有可用冻结证据。"))
            return
        chart = self.daily_repository.chart(str(dataset_id))
        strategy_decisions, synthesis = self._strategy_bundle(str(dataset_id))
        instrument = _instrument_evidence(
            payload,
            chart.payload_json if chart else None,
            {"strategy_decisions": strategy_decisions, "deterministic_synthesis": synthesis},
            symbol,
        )
        refs = [
            {"kind": "daily_dataset", "dataset_id": str(dataset_id), "symbol": symbol},
        ]
        if chart is not None:
            refs.append({"kind": "decision_chart", "dataset_id": str(dataset_id), "symbol": symbol})
        refs.extend(_collect_evidence_refs(instrument))
        result.input_payload = _input_envelope(
            intent_type=result.intent_type,
            scope={"scope_type": "instrument", "scope_id": symbol, "symbol": symbol, "trading_date": payload.get("trading_date")},
            dataset={
                "dataset_id": str(dataset.id),
                "schema_version": str(dataset.schema_version),
                "content_sha256": str(dataset.content_sha256),
                "trading_date": dataset.trading_date.isoformat(),
                "cutoff_time": dataset.cutoff_time.isoformat(),
                "market_timezone": dataset.market_timezone,
            },
            evidence=instrument,
            strategy_decisions=list(instrument.get("strategy_decisions") or []),
            deterministic_synthesis=synthesis,
            quality=dict(payload.get("quality") or dataset.quality_json or {}),
            allowed_symbols=[symbol],
            evidence_refs=refs,
        )
        result.scope_type = "instrument"
        result.scope_id = symbol
        result.scope_version = str(scope.get("scope_version")) if scope.get("scope_version") is not None else None
        result.dataset_id = str(dataset_id)
        result.source_dataset_id = str(dataset_id)
        result.source_summary = {
            **_summary_from_payload(payload, symbol_count=1, group_count=0),
            "scope_id": symbol,
            "dataset_id": str(dataset.id),
            "content_sha256": str(dataset.content_sha256),
            "cutoff_time": dataset.cutoff_time.isoformat(),
            **_strategy_counts(strategy_decisions),
        }
        result.warnings.extend(_quality_warnings(payload))

    def _compile_group(self, result: CompiledDecision) -> None:
        run_id = result.source.get("observation_run_id")
        snapshot_id = result.source.get("snapshot_id")
        if not run_id or not snapshot_id:
            result.blockers.append(_issue("source_scope_mismatch", "组仲裁需要 observation_run_id 与 snapshot_id。"))
            return
        run = self.observation_repository.get_run(str(run_id))
        if run is None:
            result.blockers.append(_issue("source_not_found", "找不到指定的 Observation Run。", {"run_id": run_id}))
            return
        if run.status not in {"succeeded", "mixed"}:
            result.blockers.append(_issue("source_not_frozen", "Observation Run 尚未生成可用冻结快照。", {"status": run.status}))
            return
        item = next((item for item in list((run.payload_json or {}).get("group_snapshots") or []) if str(item.get("snapshot_id")) == str(snapshot_id)), None)
        if not item or item.get("status") != "succeeded":
            result.blockers.append(_issue("source_scope_mismatch", "snapshot 不属于 locator 指定的 Observation Run。"))
            return
        snapshot = self.observation_repository.get_snapshot(str(snapshot_id))
        if snapshot is None:
            result.blockers.append(_issue("source_not_found", "找不到指定的 Group Daily Snapshot。", {"snapshot_id": snapshot_id}))
            return
        if str(item.get("group_version_id")) != str(snapshot.group_version_id) or str(item.get("dataset_id")) != str(snapshot.dataset_id):
            result.blockers.append(_issue("source_version_conflict", "Run 引用的 snapshot 身份与数据库记录不一致。"))
            return
        for key, expected in (
            ("dataset_id", snapshot.dataset_id),
            ("group_version_id", snapshot.group_version_id),
            ("content_sha256", snapshot.content_sha256),
        ):
            if result.source.get(key) and str(result.source[key]) != str(expected):
                result.blockers.append(_issue("source_version_conflict", f"locator 的 {key} 与冻结组快照不一致。"))
                return
        payload = dict(snapshot.payload_json or {})
        payload_group = payload.get("group") if isinstance(payload.get("group"), dict) else {}
        if (
            str(payload.get("dataset_id") or snapshot.dataset_id) != str(snapshot.dataset_id)
            or str(payload_group.get("group_id") or snapshot.group_id) != str(snapshot.group_id)
            or str(payload_group.get("version_id") or snapshot.group_version_id) != str(snapshot.group_version_id)
        ):
            result.blockers.append(_issue("source_version_conflict", "冻结组快照 payload 的身份与数据库记录不一致。"))
            return
        if item.get("content_sha256") and str(item.get("content_sha256")) != str(snapshot.content_sha256):
            result.blockers.append(_issue("source_version_conflict", "Run 引用的 snapshot content hash 与数据库记录不一致。"))
            return
        if payload.get("content_sha256") and str(payload.get("content_sha256")) != str(snapshot.content_sha256):
            result.blockers.append(_issue("source_version_conflict", "冻结组快照 payload 的 content hash 无法核对。"))
            return
        valid_count = int((payload.get("quality") or {}).get("valid_symbol_count") or 0)
        if valid_count <= 0:
            result.blockers.append(_issue("no_valid_evidence", "该组冻结快照没有可用 symbol 证据。"))
        refs = [{"kind": "group_snapshot", "snapshot_id": str(snapshot_id), "dataset_id": snapshot.dataset_id, "group_id": snapshot.group_id}]
        refs.extend(_collect_evidence_refs(payload))
        strategy_decisions = list(payload.get("strategy_decisions") or [])
        symbols = list(payload.get("symbols") or [])
        result.input_payload = _input_envelope(
            intent_type=result.intent_type,
            scope={"scope_type": "group", "scope_id": snapshot.group_id, "group_version_id": snapshot.group_version_id, "snapshot_id": snapshot.id, "dataset_id": snapshot.dataset_id, "trading_date": snapshot.trading_date.isoformat()},
            dataset={
                "dataset_id": snapshot.dataset_id,
                "schema_version": snapshot.snapshot_schema_version,
                "content_sha256": snapshot.content_sha256,
                "trading_date": snapshot.trading_date.isoformat(),
                "group_version_id": snapshot.group_version_id,
            },
            evidence={"group_snapshot": payload, "observation_run": {"run_id": run.id, "content_sha256": run.content_sha256}},
            strategy_decisions=strategy_decisions,
            deterministic_synthesis={},
            quality=dict(payload.get("quality") or {}),
            allowed_symbols=[str(item.get("symbol")) for item in symbols if isinstance(item, dict) and item.get("symbol")],
            rows=symbols,
            evidence_refs=refs,
        )
        result.scope_type = "group"
        result.scope_id = snapshot.group_id
        result.scope_version = str(snapshot.group_version)
        result.dataset_id = snapshot.dataset_id
        result.source_dataset_id = snapshot.dataset_id
        result.source_snapshot_id = snapshot.id
        result.source_observation_run_id = run.id
        result.source_summary = {
            "trading_date": snapshot.trading_date.isoformat(),
            "symbol_count": len(payload.get("symbols") or []),
            "group_count": 1,
            "quality_status": (payload.get("quality") or {}).get("status", "unknown"),
            "scope_id": snapshot.group_id,
            "observation_run_id": run.id,
            "snapshot_id": snapshot.id,
            "group_version_id": snapshot.group_version_id,
            "dataset_id": snapshot.dataset_id,
            "content_sha256": snapshot.content_sha256,
            **_strategy_counts(strategy_decisions),
        }
        result.warnings.extend(_quality_warnings(payload))

    def _compile_cross_section(self, result: CompiledDecision) -> None:
        run_id = result.source.get("observation_run_id")
        lens_id = result.source.get("lens_id")
        if not run_id or not lens_id:
            result.blockers.append(_issue("source_scope_mismatch", "横截面 AI 需要 observation_run_id 与 lens_id。"))
            return
        run = self.observation_repository.get_run(str(run_id))
        if run is None:
            result.blockers.append(_issue("source_not_found", "找不到指定的 Observation Run。", {"run_id": run_id}))
            return
        if run.status not in {"succeeded", "mixed"}:
            result.blockers.append(_issue("source_not_frozen", "Observation Run 尚未生成可用横截面。", {"status": run.status}))
            return
        service = CrossSectionService(self.session)
        try:
            projection = (
                service.indicator_projection(str(run_id), str(lens_id))
                if result.intent_type == RemoteDecisionIntent.INDICATOR_ATTENTION.value
                else service.strategy_projection(str(run_id), str(lens_id))
            )
        except AppError:
            raise
        rows = list(projection.get("rows") or [])
        if result.source.get("content_sha256") and str(result.source["content_sha256"]) != str(projection.get("content_sha256") or ""):
            result.blockers.append(_issue("source_version_conflict", "locator 的 content_sha256 与横截面 projection 不一致。"))
            return
        if len(rows) > int(self.settings.remote_decision_max_cross_section_rows):
            result.blockers.append(_issue("input_too_large", "横截面卡片数量超过 AI 输入上限。", {"rows": len(rows), "max_rows": self.settings.remote_decision_max_cross_section_rows}))
        if not any(bool(row.get("valid")) for row in rows):
            result.blockers.append(_issue("no_valid_evidence", "横截面没有有效卡片。"))
        lens_payload = dict(projection.get("lens") or {})
        projection_quality = dict(projection.get("quality") or {})
        strategy_decisions = [
            {
                "decision_id": row.get("decision_id"),
                "scope": {"group_id": row.get("group_id"), "symbol": row.get("symbol")},
                "strategy": {
                    "name": lens_payload.get("id"),
                    "version": row.get("strategy_version"),
                    "implementation_sha256": row.get("implementation_sha256"),
                },
            }
            for row in rows
            if row.get("decision_id")
        ]
        result.input_payload = _input_envelope(
            intent_type=result.intent_type,
            scope={"scope_type": "observation_run", "scope_id": run.id, "trading_date": run.trading_date.isoformat(), "lens": lens_payload},
            dataset={
                "dataset_ids": list(projection_quality.get("dataset_ids") or []),
                "schema_version": "urus.cross_section_projection.v1",
                "content_sha256": projection.get("content_sha256"),
                "trading_date": run.trading_date.isoformat(),
                "cutoff_time": run.cutoff_time.isoformat(),
            },
            evidence={"projection": projection},
            strategy_decisions=strategy_decisions,
            deterministic_synthesis={},
            quality=projection_quality,
            allowed_symbols=sorted({str(row.get("symbol")) for row in rows if row.get("symbol")}),
            rows=rows,
            evidence_refs=_projection_refs(projection),
        )
        lens = dict(projection.get("lens") or {})
        if result.source.get("lens_type") and str(result.source["lens_type"]) != str(lens.get("type") or ""):
            result.blockers.append(_issue("source_version_conflict", "locator 的 lens_type 与横截面 projection 不一致。"))
            return
        if result.source.get("lens_version") and str(result.source["lens_version"]) != str(lens.get("version") or ""):
            result.blockers.append(_issue("source_version_conflict", "locator 的 lens_version 与横截面 projection 不一致。"))
            return
        result.scope_type = "observation_run"
        result.scope_id = run.id
        result.scope_version = str(lens.get("version")) if lens.get("version") is not None else None
        result.dataset_id = None
        result.lens_type = str(lens.get("type") or "")
        result.lens_id = str(lens.get("id") or lens_id)
        result.lens_version = str(lens.get("version")) if lens.get("version") is not None else None
        result.source_observation_run_id = run.id
        result.source_summary = {
            "trading_date": projection.get("trading_date"),
            "symbol_count": len(rows),
            "group_count": len(projection.get("groups") or []),
            "quality_status": (projection.get("quality") or {}).get("status", "unknown"),
            "lens_id": result.lens_id,
            "lens_type": result.lens_type,
            "lens_version": result.lens_version,
            "observation_run_id": run.id,
            "content_sha256": projection.get("content_sha256"),
            "valid_row_count": sum(bool(row.get("valid")) for row in rows),
            "missing_row_count": sum(not bool(row.get("valid")) for row in rows),
            **_strategy_counts(strategy_decisions),
        }
        result.warnings.extend(_quality_warnings(projection.get("quality") or {}))

    def _strategy_bundle(self, dataset_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from app.repositories.strategy_decisions import StrategyDecisionRepository

        return StrategyDecisionRepository(self.session).bundle(dataset_id)


def validate_artifact(
    run: RemoteDecisionRunModel,
    raw_output: dict[str, Any],
) -> tuple[RemoteDecisionArtifact | None, RemoteDecisionIssue | None]:
    try:
        artifact = RemoteDecisionArtifact.model_validate(raw_output)
    except ValidationError as exc:
        return None, _issue("output_schema_invalid", "Anomalo 输出不符合严格 Artifact Schema。", {"errors": exc.errors(include_url=False)[:8]})
    if artifact.schema_version != ARTIFACT_SCHEMA_VERSION:
        return None, _issue("output_schema_invalid", "Artifact schema_version 不匹配。")
    if artifact.intent_type.value != run.intent_type:
        return None, _issue("result_scope_mismatch", "Artifact intent_type 与本地 Run 不一致。")
    recalculated_input_hash = _hash_without_input_hash(dict(run.input_json or {}))
    if recalculated_input_hash != run.input_sha256:
        return None, _issue("result_input_hash_mismatch", "本地冻结输入已发生变化，不能验收 Artifact。")
    if artifact.input_sha256 != run.input_sha256:
        return None, _issue("result_input_hash_mismatch", "Artifact input_sha256 与冻结输入不一致。")
    scope = dict(artifact.scope or {})
    if str(scope.get("scope_type") or "") != str(run.scope_type):
        return None, _issue("result_scope_mismatch", "Artifact scope_type 与冻结输入不一致。")
    expected_scope = str(run.scope_id)
    actual_scope = str(
        scope.get("scope_id")
        or scope.get("id")
        or scope.get("symbol")
        or scope.get("group_id")
        or scope.get("observation_run_id")
        or ""
    )
    scope_matches = actual_scope.upper() == expected_scope.upper() if run.scope_type == "instrument" else actual_scope == expected_scope
    if not actual_scope or not scope_matches:
        return None, _issue("result_scope_mismatch", "Artifact scope 超出本次冻结输入。")
    if artifact.dataset_id != run.dataset_id:
        return None, _issue("result_scope_mismatch", "Artifact dataset_id 与冻结输入不一致。")
    input_scope = dict((run.input_json or {}).get("scope") or {})
    if run.scope_type == "instrument" and str(scope.get("symbol") or "").upper() != expected_scope.upper():
        return None, _issue("result_scope_mismatch", "Artifact symbol 与冻结输入不一致。")
    if run.scope_type == "group":
        expected_snapshot = str(run.source_snapshot_id or input_scope.get("snapshot_id") or "")
        if expected_snapshot and str(scope.get("snapshot_id") or "") != expected_snapshot:
            return None, _issue("result_scope_mismatch", "Artifact snapshot_id 与冻结组快照不一致。")
        expected_group_version_id = str(input_scope.get("group_version_id") or "")
        if expected_group_version_id and str(scope.get("group_version_id") or "") != expected_group_version_id:
            return None, _issue("result_scope_mismatch", "Artifact group_version_id 与冻结输入不一致。")
    if run.lens_id:
        raw_lens = (artifact.scope or {}).get("lens")
        actual_lens = str((artifact.scope or {}).get("lens_id") or (raw_lens.get("id") if isinstance(raw_lens, dict) else "") or "")
        if actual_lens != run.lens_id:
            return None, _issue("result_scope_mismatch", "Artifact lens_id 与冻结横截面不一致。")
        input_lens = input_scope.get("lens")
        expected_lens_type = str(
            run.lens_type
            or (input_lens.get("type") if isinstance(input_lens, dict) else "")
            or ""
        )
        actual_lens_type = str((raw_lens or {}).get("type") if isinstance(raw_lens, dict) else scope.get("lens_type") or "")
        if expected_lens_type and actual_lens_type != expected_lens_type:
            return None, _issue("result_scope_mismatch", "Artifact lens_type 与冻结横截面不一致。")
    if run.source_snapshot_id:
        actual_snapshot = str((artifact.scope or {}).get("snapshot_id") or "")
        if actual_snapshot != run.source_snapshot_id:
            return None, _issue("result_scope_mismatch", "Artifact snapshot_id 与冻结组快照不一致。")
    allowed_ref_keys = {_reference_key(item) for item in list(run.input_json.get("evidence_refs") or []) if isinstance(item, dict)}
    for ref in artifact.evidence_refs:
        if not _reference_is_allowed(ref, allowed_ref_keys):
            return None, _issue("result_scope_mismatch", "Artifact Evidence Reference 不属于本次冻结输入。")
    input_rows = [item for item in list(run.input_json.get("rows") or []) if isinstance(item, dict)]
    row_by_id = {str(item.get("id")): item for item in input_rows if item.get("id")}
    rows = set(row_by_id)
    if run.intent_type in {RemoteDecisionIntent.INDICATOR_ATTENTION.value, RemoteDecisionIntent.STRATEGY_ATTENTION.value}:
        seen: set[str] = set()
        allowed_finding_types = (
            {"extreme_value", "abrupt_change", "state_transition", "internal_divergence", "quality_anomaly"}
            if run.intent_type == RemoteDecisionIntent.INDICATOR_ATTENTION.value
            else {"stage_transition", "score_outlier", "near_confirmation", "new_invalidation", "cross_group_divergence", "quality_anomaly"}
        )
        known_strategy_ids = _known_strategy_decision_ids(run)
        for card in artifact.notable_cards:
            unknown = set(card) - {
                "rank", "card_id", "group_id", "symbol", "strategy_decision_id", "finding_type",
                "current_stage", "previous_stage", "severity", "why_notable", "suggested_drilldown",
                "observed_value", "comparison_value", "evidence_refs",
            }
            if unknown:
                return None, _issue("output_schema_invalid", "Artifact notable card 含有未声明字段。", {"fields": sorted(unknown)})
            if not isinstance(card.get("rank"), int) or isinstance(card.get("rank"), bool) or not card.get("card_id"):
                return None, _issue("output_schema_invalid", "Artifact notable card 缺少 rank 或 card_id。")
            if not card.get("group_id") or not card.get("symbol"):
                return None, _issue("output_schema_invalid", "Artifact notable card 必须包含 group_id 与 symbol。")
            if run.intent_type == RemoteDecisionIntent.INDICATOR_ATTENTION.value and card.get("strategy_decision_id"):
                return None, _issue("output_schema_invalid", "指标横截面结果不能携带 strategy_decision_id。")
            if run.intent_type == RemoteDecisionIntent.STRATEGY_ATTENTION.value and not card.get("strategy_decision_id"):
                return None, _issue("output_schema_invalid", "策略横截面 notable card 必须包含 strategy_decision_id。")
            for text_field in ("why_notable", "suggested_drilldown"):
                if card.get(text_field) is not None and (not isinstance(card[text_field], str) or len(card[text_field]) > 1000):
                    return None, _issue("output_schema_invalid", f"Artifact {text_field} 超过 1000 字符或类型无效。")
            finding_type = card.get("finding_type")
            if finding_type is None:
                return None, _issue("output_schema_invalid", "Artifact notable card 缺少 finding_type。")
            if finding_type not in allowed_finding_types:
                return None, _issue("output_schema_invalid", "Artifact finding_type 未声明。", {"finding_type": finding_type})
            if "evidence_refs" in card and not isinstance(card.get("evidence_refs"), list):
                return None, _issue("output_schema_invalid", "Artifact card evidence_refs 类型无效。")
            decision_id = card.get("strategy_decision_id")
            if decision_id:
                if str(decision_id) not in known_strategy_ids:
                    return None, _issue("result_scope_mismatch", "Artifact strategy_decision_id 不属于本次冻结输入。", {"strategy_decision_id": decision_id})
            card_id = str(card.get("card_id") or "")
            if not card_id or card_id not in rows:
                return None, _issue("result_scope_mismatch", "Artifact card_id 不属于本次横截面输入。", {"card_id": card_id})
            input_row = row_by_id[card_id]
            for field in ("group_id", "symbol"):
                if card.get(field) is not None and str(card.get(field)) != str(input_row.get(field)):
                    return None, _issue("result_scope_mismatch", f"Artifact card {field} 与冻结输入不一致。", {field: card.get(field)})
            if decision_id and input_row.get("decision_id") and str(decision_id) != str(input_row.get("decision_id")):
                return None, _issue("result_scope_mismatch", "Artifact strategy_decision_id 与 card_id 对应的输入不一致。")
            if card_id in seen:
                return None, _issue("result_scope_mismatch", "Artifact card_id 重复。", {"card_id": card_id})
            seen.add(card_id)
            for ref in list(card.get("evidence_refs") or []):
                if not isinstance(ref, dict):
                    return None, _issue("output_schema_invalid", "Artifact card evidence_refs 必须是对象列表。")
                if not _reference_is_allowed(ref, allowed_ref_keys):
                    return None, _issue("result_scope_mismatch", "Card Evidence Reference 不属于本次冻结输入。")
    else:
        known_strategy_ids = _known_strategy_decision_ids(run)
        for strategy_id in _decision_strategy_ids(artifact.decision):
            if strategy_id not in known_strategy_ids:
                return None, _issue("result_scope_mismatch", "Artifact strategy ID 不属于本次冻结输入。", {"strategy_decision_id": strategy_id})
    ranks = [item.get("rank") for item in artifact.notable_cards if isinstance(item.get("rank"), int)]
    if ranks and ranks != list(range(1, len(ranks) + 1)):
        return None, _issue("output_schema_invalid", "Artifact rank 必须从 1 连续递增。")
    return artifact, None


def _reference_key(ref: dict[str, Any]) -> tuple[str, str]:
    """Build an identity key without discarding immutable reference fields.

    ``path`` is the only presentation/projection field that a Workflow may
    narrow (for example ``bars`` to ``bars[-1]``).  Every other field remains
    part of the identity, including IDs such as ``decision_id`` and
    ``snapshot_id``; otherwise a result could cite a different object in the
    same group/symbol and still pass validation.
    """

    identity = {key: value for key, value in ref.items() if key != "path"}
    return canonical_json(identity), str(ref.get("path") or "")


def _reference_is_allowed(ref: dict[str, Any], allowed: set[tuple[str, str]]) -> bool:
    if not allowed:
        return not ref
    key = _reference_key(ref)
    if key in allowed:
        return True
    # Workflow output may add a narrower JSON path while preserving the same
    # immutable object identity; permit that, but never a new object ID.
    return any(key[0] == candidate[0] for candidate in allowed)


def _input_envelope(
    *,
    intent_type: str,
    scope: dict[str, Any],
    dataset: dict[str, Any],
    evidence: dict[str, Any],
    strategy_decisions: list[dict[str, Any]],
    deterministic_synthesis: dict[str, Any],
    quality: dict[str, Any],
    allowed_symbols: list[str],
    rows: list[dict[str, Any]] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the shared narrow input contract used by all four Workflows."""

    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "intent": {
            "type": intent_type,
            "trigger_mode": "user",
            "trigger_source": _intent_trigger_source(intent_type),
        },
        "scope": scope,
        "dataset": dataset,
        "evidence": evidence,
        # These arrays are part of the signed input. Sort by canonical JSON so
        # database iteration order or provider response order cannot change the
        # hash for an otherwise identical frozen evidence package.
        "strategy_decisions": _stable_objects(strategy_decisions),
        "deterministic_synthesis": deterministic_synthesis,
        "quality": quality,
        "constraints": {
            "allowed_symbols": sorted(set(allowed_symbols)),
            "allow_latest_data_lookup": False,
            "allow_symbol_expansion": False,
        },
        # ``rows`` and top-level refs make the cross-section card identity
        # explicit while the immutable projection remains under evidence.
        "rows": _stable_objects(rows or []),
        "evidence_refs": _stable_objects(evidence_refs or [], unique=True),
    }


def _intent_trigger_source(intent_type: str) -> str:
    return {
        RemoteDecisionIntent.INSTRUMENT_ARBITRATION.value: "instrument_page",
        RemoteDecisionIntent.GROUP_ARBITRATION.value: "group_page",
        RemoteDecisionIntent.INDICATOR_ATTENTION.value: "indicator_cross_section",
        RemoteDecisionIntent.STRATEGY_ATTENTION.value: "strategy_cross_section",
    }.get(intent_type, "unknown")


def _known_strategy_decision_ids(run: RemoteDecisionRunModel) -> set[str]:
    """Collect decision IDs from either an instrument/group package or rows."""

    known: set[str] = {
        str(item.get("decision_id"))
        for item in list((run.input_json or {}).get("rows") or [])
        if isinstance(item, dict) and item.get("decision_id")
    }
    known.update(
        str(item.get("decision_id"))
        for item in list((run.input_json or {}).get("strategy_decisions") or [])
        if isinstance(item, dict) and item.get("decision_id")
    )
    evidence = (run.input_json or {}).get("evidence") or {}
    candidates: list[Any] = []
    if isinstance(evidence, dict):
        candidates.extend(evidence.get("strategy_decisions") or [])
        group_snapshot = evidence.get("group_snapshot")
        if isinstance(group_snapshot, dict):
            candidates.extend(group_snapshot.get("strategy_decisions") or [])
        projection = evidence.get("projection")
        if isinstance(projection, dict):
            candidates.extend(projection.get("rows") or [])
    known.update(
        str(item.get("decision_id"))
        for item in candidates
        if isinstance(item, dict) and item.get("decision_id")
    )
    return known


def _decision_strategy_ids(value: Any) -> set[str]:
    """Read only declared strategy-id fields from an intent-specific decision."""

    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "strategy_decision_id" and isinstance(item, str):
                result.add(item)
            elif key.endswith("strategy_ids") and isinstance(item, list):
                result.update(str(entry) for entry in item if isinstance(entry, (str, int)))
            elif isinstance(item, (dict, list)):
                result.update(_decision_strategy_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_decision_strategy_ids(item))
    return result


def _hash_without_input_hash(payload: dict[str, Any]) -> str:
    return content_sha256({key: value for key, value in payload.items() if key != "input_sha256"})


def _valid_hash(value: str | None) -> bool:
    return bool(re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", str(value or "")))


def _preflight_fingerprint(intent: str, source: dict[str, Any], input_hash: str, workflow_ref: str, compiled_hash: str) -> str:
    value = "\0".join(("urus-remote-decision-v1", intent, canonical_json(source), input_hash, workflow_ref, compiled_hash))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _issue(code: str, message: str, details: dict[str, Any] | None = None) -> RemoteDecisionIssue:
    return RemoteDecisionIssue(code=code, message=message, details=details or {})


def _summary_from_payload(payload: dict[str, Any], *, symbol_count: int, group_count: int) -> dict[str, Any]:
    quality = payload.get("quality") or {}
    return {"trading_date": payload.get("trading_date"), "symbol_count": symbol_count, "group_count": group_count, "quality_status": quality.get("status") or payload.get("status") or "unknown"}


def _strategy_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [str(item.get("status") or "ok") for item in decisions if isinstance(item, dict)]
    return {
        "strategy_decision_count": len(statuses),
        "strategy_error_count": sum(status == "error" for status in statuses),
        "strategy_not_applicable_count": sum(status == "not_applicable" for status in statuses),
    }


def _quality_warnings(value: dict[str, Any]) -> list[RemoteDecisionIssue]:
    quality = value.get("quality") if isinstance(value.get("quality"), dict) else value
    warnings = quality.get("warnings") if isinstance(quality, dict) else []
    return [_issue("quality_warning", str(item)) for item in list(warnings or [])[:50]]


def _instrument_evidence(dataset: dict[str, Any], chart: dict[str, Any] | None, strategy: dict[str, Any], symbol: str) -> dict[str, Any]:
    manifests = [item for item in list(dataset.get("bar_manifest") or []) if str(item.get("symbol") or "").upper() == symbol]
    chart_instrument = ((chart or {}).get("instruments") or {}).get(symbol) if isinstance(chart, dict) else None
    decisions = [item for item in list(strategy.get("strategy_decisions") or []) if str((item.get("scope") or {}).get("symbol") or item.get("symbol") or "").upper() == symbol]
    return {"dataset": {**dataset, "bar_manifest": manifests}, "chart": chart_instrument or {}, "strategy_decisions": decisions, "deterministic_synthesis": strategy.get("deterministic_synthesis") or {}}


def _projection_refs(projection: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in list(projection.get("rows") or []):
        for ref in list(row.get("evidence_refs") or []):
            if isinstance(ref, dict):
                refs.append(dict(ref))
    unique: dict[str, dict[str, Any]] = {}
    for ref in refs:
        key = canonical_json(ref)
        unique[key] = ref
    return list(unique.values())[:500]


def _collect_evidence_refs(value: Any) -> list[dict[str, Any]]:
    """Collect references embedded in a frozen evidence payload.

    The top-level ``evidence_refs`` array is the allowlist used when accepting
    an Artifact. Nested Strategy Decision and chart references therefore need
    to be promoted into it as well; otherwise a valid Workflow citation would
    be rejected even though it points at the exact frozen object.
    """

    refs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nested = value.get("evidence_refs")
        if isinstance(nested, list):
            refs.extend(dict(item) for item in nested if isinstance(item, dict))
        for key, item in value.items():
            if key != "evidence_refs":
                refs.extend(_collect_evidence_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_collect_evidence_refs(item))
    return refs


def _stable_objects(values: list[dict[str, Any]], *, unique: bool = False) -> list[dict[str, Any]]:
    objects = [dict(value) for value in values if isinstance(value, dict)]
    if unique:
        by_digest = {canonical_json(value): value for value in objects}
        objects = list(by_digest.values())
    return sorted(objects, key=canonical_json)

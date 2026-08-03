from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.core.time import utc_now
from app.events.contracts import EventDiscoveryOutput, discovery_response_format
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.events.prompts import render_schedule_prompt
from app.integrations.anomalo import AnomaloAdapter, AnomaloRequest, AnomaloResponse
from app.repositories.events import EventRepository


@dataclass(frozen=True)
class ScheduleInitializationResult:
    initialization_id: str
    status: str
    discovered_count: int
    missing_count: int
    api_call_count: int
    categories: list[dict[str, Any]]


class EventScheduleInitializer:
    """Explicit, idempotent full-calendar warm-up for 1B and 3B.

    Daily workflows remain incremental. This service is intentionally called by
    a command or an administrative job, so a slow web investigation does not
    block a normal run or a page request.
    """

    def __init__(
        self,
        repository: EventRepository,
        adapter: AnomaloAdapter,
        *,
        agent: str,
        horizon_days: int = 120,
        batch_size: int = 1,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.agent = agent
        self.horizon_days = horizon_days
        if batch_size < 1:
            raise ValueError("Schedule initialization batch_size must be at least 1.")
        self.batch_size = batch_size

    def initialize(
        self,
        *,
        categories: Iterable[str] = ("macro", "instrument"),
        instrument_symbols: Iterable[str] = (),
        force: bool = False,
        batch_size: int | None = None,
        now: datetime | None = None,
        initialization_id: str | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> ScheduleInitializationResult:
        timestamp = now or utc_now()
        category_list = list(dict.fromkeys(categories))
        unknown = sorted(set(category_list) - {"macro", "instrument"})
        if unknown:
            raise ValueError(f"Unsupported scheduled-event categories: {', '.join(unknown)}")
        symbols = list(dict.fromkeys(str(symbol).upper() for symbol in instrument_symbols if symbol))
        definitions = [
            spec for spec in DEFAULT_EVENT_DEFINITIONS if spec.category in category_list
        ]
        self.repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=timestamp)
        all_targets = self._targets(definitions, symbols)
        pending_targets = (
            all_targets
            if force
            else [
                target
                for target in all_targets
                if not self.repository.has_future_event(
                    target["definition_key"],
                    subject=target["subject"],
                    now=timestamp,
                )
            ]
        )
        batch_id = initialization_id or str(uuid4())
        effective_batch_size = self.batch_size if batch_size is None else batch_size
        if effective_batch_size < 1:
            raise ValueError("Schedule initialization batch_size must be at least 1.")
        batch = self.repository.create_schedule_initialization(
            initialization_id=batch_id,
            horizon_days=self.horizon_days,
            categories=category_list,
            definitions=[spec.key for spec in definitions],
            targets=all_targets,
            started_at=timestamp,
        )
        self._emit(
            progress,
            {
                "event": "started",
                "initialization_id": batch_id,
                "categories": category_list,
                "target_count": len(pending_targets),
                "batch_size": effective_batch_size,
            },
        )

        category_results: list[dict[str, Any]] = []
        discovered_total = 0
        missing_total = 0
        api_calls = 0
        for category in category_list:
            category_targets = [
                target for target in pending_targets if self._target_category(target, definitions) == category
            ]
            category_definitions = [
                spec
                for spec in definitions
                if any(target["definition_key"] == spec.key for target in category_targets)
            ]
            if not category_targets:
                self._emit(
                    progress,
                    {
                        "event": "category_skipped",
                        "initialization_id": batch_id,
                        "category": category,
                        "reason": "all targets already have future events",
                    },
                )
                category_results.append(
                    {
                        "category": category,
                        "status": "skipped",
                        "target_count": 0,
                        "discovered_count": 0,
                        "missing_definitions": [],
                        "warnings": [],
                    }
                )
                self._save_progress(
                    batch,
                    category_results,
                    discovered_total,
                    missing_total,
                    api_calls,
                )
                continue

            category_attempts: list[dict[str, Any]] = []
            for offset in range(0, len(category_targets), effective_batch_size):
                target_chunk = category_targets[offset : offset + effective_batch_size]
                chunk_definitions = [
                    spec
                    for spec in category_definitions
                    if any(target["definition_key"] == spec.key for target in target_chunk)
                ]
                api_calls += 1
                self._emit(
                    progress,
                    {
                        "event": "request_started",
                        "initialization_id": batch_id,
                        "category": category,
                        "request_number": api_calls,
                        "target_count": len(target_chunk),
                        "targets": target_chunk,
                    },
                )
                result = self._discover_category(
                    batch_id=batch_id,
                    category=category,
                    definitions=chunk_definitions,
                    targets=target_chunk,
                    now=timestamp,
                    chunk_index=len(category_attempts) + 1,
                )
                self._emit(
                    progress,
                    {
                        "event": "request_finished",
                        "initialization_id": batch_id,
                        "category": category,
                        "request_number": api_calls,
                        "status": result.get("status"),
                        "discovered_count": result.get("discovered_count", 0),
                        "error": result.get("error"),
                    },
                )
                category_attempts.append(result)
                discovered_total += int(result.get("discovered_count", 0))
                missing_total += len(result.get("missing_definitions", []))
                category_summary = self._category_summary(
                    category,
                    category_targets,
                    category_attempts,
                )
                self._replace_category_result(category_results, category_summary)
                self._save_progress(
                    batch,
                    category_results,
                    discovered_total,
                    missing_total,
                    api_calls,
                )

        failed = [item for item in category_results if item["status"] == "failed"]
        partial = [item for item in category_results if item["status"] == "partial"]
        succeeded = [
            item for item in category_results if item["status"] in {"succeeded", "skipped"}
        ]
        if partial or (failed and succeeded):
            status = "partial"
        elif failed:
            status = "failed"
        else:
            status = "succeeded"
        self.repository.update_schedule_initialization(
            batch,
            status=status,
            discovered_count=discovered_total,
            missing_count=missing_total,
            api_call_count=api_calls,
            metadata_payload={
                "categories": category_results,
                "force": force,
                "batch_size": effective_batch_size,
            },
            error_message=(
                "；".join(
                    error
                    for item in (*failed, *partial)
                    for error in item.get("errors", [])
                )
                if failed or partial
                else None
            ),
            completed_at=utc_now(),
        )
        self._emit(
            progress,
            {
                "event": "finished",
                "initialization_id": batch_id,
                "status": status,
                "discovered_count": discovered_total,
                "missing_count": missing_total,
                "api_call_count": api_calls,
            },
        )
        return ScheduleInitializationResult(
            initialization_id=batch_id,
            status=status,
            discovered_count=discovered_total,
            missing_count=missing_total,
            api_call_count=api_calls,
            categories=category_results,
        )

    @staticmethod
    def _targets(definitions: list[Any], symbols: list[str]) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        for definition in definitions:
            subjects = ["market"] if definition.category == "macro" else symbols
            targets.extend(
                {
                    "definition_key": definition.key,
                    "subject_type": definition.subject_type,
                    "subject": subject,
                }
                for subject in subjects
            )
        return targets

    @staticmethod
    def _target_category(target: dict[str, str], definitions: list[Any]) -> str:
        for definition in definitions:
            if definition.key == target["definition_key"]:
                return definition.category
        raise ValueError(f"Unknown initialization target: {target['definition_key']}")

    def _discover_category(
        self,
        *,
        batch_id: str,
        category: str,
        definitions: list[Any],
        targets: list[dict[str, str]],
        now: datetime,
        chunk_index: int,
    ) -> dict[str, Any]:
        request = AnomaloRequest(
            session_id=f"urus-schedule-initialize-{batch_id}-{category}-{chunk_index}",
            agent=self.agent,
            response_format=discovery_response_format(),
            message=render_schedule_prompt(
                SimpleNamespace(event_horizon_days=self.horizon_days),
                category=category,
                definitions=definitions,
                targets=targets,
                now=now,
            ),
        )
        started = utc_now()
        try:
            response = self.adapter.investigate(request)
        except Exception as exc:
            response = AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code="adapter_error",
                error_message=str(exc),
            )
        self.repository.save_agent_run(
            run_id=None,
            event_id=None,
            operation="initialize_schedule",
            agent=self.agent,
            session_id=request.session_id,
            status=("disabled" if response.disabled else "failed" if response.error_code else "succeeded"),
            request_payload={
                "session_id": request.session_id,
                "agent": request.agent,
                "message": request.message,
                "response_format": request.response_format,
            },
            response_payload={
                "output": response.output,
                "output_format": response.output_format,
                "final_text": response.final_text,
                "agent": response.agent,
                "events": response.events,
            },
            error_code=response.error_code,
            error_message=response.error_message,
            started_at=started,
            completed_at=utc_now(),
        )
        result_base = {
            "category": category,
            "target_count": len(targets),
            "discovered_count": 0,
            "missing_definitions": [],
            "warnings": [],
        }
        if response.disabled or response.error_code or response.output is None:
            return {
                **result_base,
                "status": "failed",
                "error": response.error_message or "Schedule API returned no output.",
            }
        try:
            output = EventDiscoveryOutput.model_validate(response.output)
        except Exception as exc:
            return {
                **result_base,
                "status": "failed",
                "error": f"Schedule output failed strict validation: {exc}",
            }
        if not output.events and not output.missing_definitions:
            return {
                **result_base,
                "status": "failed",
                "error": "Schedule API returned empty events and empty missing_definitions.",
            }

        known_definitions = {spec.key for spec in definitions}
        requested = {
            (target["definition_key"], target["subject"].upper()) for target in targets
        }
        stored = 0
        stored_targets: set[tuple[str, str]] = set()
        warnings = list(output.notes)
        for candidate in output.events:
            key = (candidate.definition_key, candidate.subject.upper())
            if candidate.category != category or candidate.definition_key not in known_definitions:
                warnings.append(f"Ignored unconfigured definition: {candidate.definition_key}")
                continue
            if key not in requested:
                warnings.append(
                    f"Ignored target outside initialization request: {candidate.definition_key}/{candidate.subject}"
                )
                continue
            self.repository.upsert_candidate(candidate, now=now)
            stored += 1
            stored_targets.add(key)
        if output.events and stored == 0 and not output.missing_definitions:
            return {
                **result_base,
                "status": "failed",
                "warnings": warnings,
                "error": "Schedule output contained no requested targets.",
            }
        declared_missing = set(output.missing_definitions)
        covered_targets = stored_targets | {
            target for target in requested if target[0] in declared_missing
        }
        uncovered_targets = sorted(requested - covered_targets)
        if uncovered_targets:
            uncovered_labels = [f"{definition_key}/{subject}" for definition_key, subject in uncovered_targets]
            return {
                **result_base,
                "status": "partial" if stored else "failed",
                "discovered_count": stored,
                "missing_definitions": output.missing_definitions,
                "warnings": warnings,
                "uncovered_targets": uncovered_labels,
                "error": (
                    "Schedule output did not cover requested targets: "
                    + ", ".join(uncovered_labels)
                ),
            }
        return {
            **result_base,
            "status": "succeeded",
            "discovered_count": stored,
            "missing_definitions": output.missing_definitions,
            "warnings": warnings,
        }

    @staticmethod
    def _category_summary(
        category: str,
        targets: list[dict[str, str]],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        statuses = {item.get("status") for item in attempts}
        if statuses == {"succeeded"}:
            status = "succeeded"
        elif statuses == {"failed"}:
            status = "failed"
        else:
            status = "partial"
        errors = [str(item["error"]) for item in attempts if item.get("error")]
        return {
            "category": category,
            "status": status,
            "target_count": len(targets),
            "discovered_count": sum(int(item.get("discovered_count", 0)) for item in attempts),
            "missing_definitions": list(
                dict.fromkeys(
                    definition
                    for item in attempts
                    for definition in item.get("missing_definitions", [])
                )
            ),
            "warnings": [
                warning
                for item in attempts
                for warning in item.get("warnings", [])
            ],
            "errors": errors,
            "batch_count": len(attempts),
        }

    @staticmethod
    def _replace_category_result(
        category_results: list[dict[str, Any]], summary: dict[str, Any]
    ) -> None:
        for index, current in enumerate(category_results):
            if current.get("category") == summary["category"]:
                category_results[index] = summary
                return
        category_results.append(summary)

    def _save_progress(
        self,
        batch: Any,
        category_results: list[dict[str, Any]],
        discovered_count: int,
        missing_count: int,
        api_call_count: int,
    ) -> None:
        self.repository.update_schedule_initialization(
            batch,
            status="running",
            discovered_count=discovered_count,
            missing_count=missing_count,
            api_call_count=api_call_count,
            metadata_payload={"categories": category_results},
        )

    @staticmethod
    def _emit(
        progress: Callable[[dict[str, Any]], None] | None,
        payload: dict[str, Any],
    ) -> None:
        if progress is not None:
            progress(payload)

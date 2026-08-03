from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.events.contracts import (
    EventDiscoveryOutput,
    EventResultOutput,
    discovery_response_format,
    result_response_format,
)
from app.events.definitions import DEFAULT_EVENT_DEFINITIONS
from app.events.prompts import event_result_rules, render_result_prompt, render_schedule_prompt
from app.integrations.anomalo import AnomaloRequest, AnomaloResponse
from app.models import StepStatus
from app.repositories.events import EventRepository
from app.workflows.base import StepResult


class ScheduledEventCoordinator:
    """Run the enabled scheduled-event lifecycle for one workflow step.

    This service intentionally has no breaking/news path. The breaking Agent
    is configured for a later rollout but is never called by validation.
    """

    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def execute(self, context: Any, category: str) -> StepResult:
        base_payload: dict[str, Any] = {
            "is_mock": True,
            "category": category,
            "mode": "scheduled",
            "agent": context.scheduled_event_agent,
            "breaking_agent_enabled": context.breaking_events_enabled,
            "breaking_agent": context.breaking_event_agent,
        }
        if not context.expected_events_enabled:
            return StepResult(
                status=StepStatus.SKIPPED,
                summary="跳过：预期事件验证开关未启用；突发事件 Agent 仍未启动。",
                payload={
                    **base_payload,
                    "status": StepStatus.SKIPPED.value,
                    "reason": "expected_events_enabled=false",
                    "events": [],
                    "counts": {},
                },
                data_state="skipped",
            )
        now = utc_now()
        self.repository.ensure_definitions(DEFAULT_EVENT_DEFINITIONS, now=now)

        definitions = [
            spec for spec in DEFAULT_EVENT_DEFINITIONS if spec.category == category
        ]
        if not definitions:
            return self._unavailable(base_payload, f"未配置 {category} 事件定义。")

        # The scheduled-event workflow has two independent phases. Schedule
        # coverage never controls whether overdue result collection runs.
        schedule_step = self._run_schedule_step(
            context,
            category=category,
            definitions=definitions,
            now=now,
        )
        result_step = self._run_result_step(context, category=category, now=now)

        reactions = 0
        if context.run_type == "post_close_review":
            reactions = self._record_market_reactions(context, category, now)

        events = [EventRepository.event_payload(event) for event in self.repository.list_events(category)]
        counts: dict[str, int] = {}
        for event in events:
            status = str(event.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        warnings = [
            *self._payload_strings(schedule_step.payload.get("warnings")),
            *self._payload_strings(result_step.payload.get("warnings")),
            *self._payload_strings(result_step.payload.get("errors")),
        ]
        status = self._aggregate_status(schedule_step, result_step)
        data_state, is_mock = self._aggregate_data_state(schedule_step, result_step)
        summary = (
            f"{category} 规律事件：日历步骤 {schedule_step.status.value}，"
            f"结果步骤 {result_step.status.value}。"
        )
        errors = [
            message
            for message in (schedule_step.error_message, result_step.error_message)
            if message
        ]
        payload = {
            **base_payload,
            "is_mock": is_mock,
            "status": status.value,
            "summary": summary,
            "schedule_step": self._phase_payload(schedule_step),
            "result_step": self._phase_payload(result_step),
            "events": events,
            "counts": counts,
            # Compatibility fields remain at the top level for the existing
            # read model; their authoritative values live in the two phases.
            "discovered_count": schedule_step.payload.get("discovered_count", 0),
            "due_result_count": result_step.payload.get("due_count", 0),
            "result_api_call_count": result_step.payload.get("api_call_count", 0),
            "schedule_api_called": schedule_step.payload.get("api_called", False),
            "missing_future_definitions": schedule_step.payload.get(
                "missing_future_definitions", []
            ),
            "missing_future_targets": schedule_step.payload.get(
                "missing_future_targets", []
            ),
            "market_reaction_count": reactions,
            "missing_definitions": schedule_step.payload.get("missing_definitions", []),
            "warnings": warnings,
            "next_check_at": self._next_check(events),
        }
        return StepResult(
            status=status,
            summary=summary,
            payload=payload,
            error_message="；".join(errors) if errors else None,
            data_state=data_state,
        )

    def _run_schedule_step(
        self,
        context: Any,
        *,
        category: str,
        definitions: list[Any],
        now: datetime,
    ) -> StepResult:
        missing_targets = self._missing_schedule_targets(
            context,
            category=category,
            definitions=definitions,
            now=now,
        )
        missing_keys = list(
            dict.fromkeys(target["definition_key"] for target in missing_targets)
        )
        missing_key_set = set(missing_keys)
        missing_future_definitions = [
            spec for spec in definitions if spec.key in missing_key_set
        ]
        base_payload = {
            "operation": "discover_schedule",
            "api_called": False,
            "missing_future_definitions": missing_keys,
            "missing_future_targets": missing_targets,
            "discovered_count": 0,
            "missing_definitions": [],
            "warnings": [],
            "is_mock": False,
        }
        if not missing_targets:
            return StepResult(
                status=StepStatus.SKIPPED,
                summary="数据库已覆盖所有规律事件的未来日历，未调用日历 API。",
                payload=base_payload,
                data_state="live",
            )
        if context.anomalo_adapter is None:
            reason = "日历步骤需要 Anomalo adapter，但当前未配置。"
            return StepResult(
                status=StepStatus.UNAVAILABLE,
                summary=reason,
                payload={**base_payload, "reason": reason},
                error_message=reason,
                data_state="unavailable",
            )

        request = AnomaloRequest(
            session_id=f"urus-{context.run_id}-events-{category}-discovery",
            agent=context.scheduled_event_agent,
            response_format=discovery_response_format(),
            message=render_schedule_prompt(
                context,
                category=category,
                definitions=missing_future_definitions,
                targets=missing_targets,
                now=now,
            ),
        )
        discovery = self._investigate(
            context,
            request=request,
            operation="discover_schedule",
            event_id=None,
        )
        called_payload = {
            **base_payload,
            "api_called": True,
            "is_mock": discovery.is_mock,
        }
        if discovery.disabled:
            reason = "日历 API 当前未启用。"
            return StepResult(
                status=StepStatus.UNAVAILABLE,
                summary=reason,
                payload={**called_payload, "reason": reason},
                error_message=reason,
                data_state="unavailable",
            )
        if discovery.error_code or discovery.output is None:
            reason = discovery.error_message or "日历 API 没有返回结构化 output。"
            return StepResult(
                status=StepStatus.FAILED,
                summary="规律事件日历调查失败。",
                payload={
                    **called_payload,
                    "reason": reason,
                    "error_code": discovery.error_code,
                },
                error_message=reason,
                data_state="unavailable",
            )
        try:
            discovery_output = self._parse(discovery.output, EventDiscoveryOutput)
        except ValueError as exc:
            reason = f"日历 API JSON 无效：{exc}"
            return StepResult(
                status=StepStatus.FAILED,
                summary="规律事件日历返回不符合严格 JSON Schema。",
                payload={**called_payload, "reason": reason},
                error_message=reason,
                data_state="unavailable",
            )
        if not discovery_output.events and not discovery_output.missing_definitions:
            reason = (
                "日历 API 返回空 events，但没有声明 missing_definitions；"
                "无法证明未来日历调查已经完成。"
            )
            return StepResult(
                status=StepStatus.FAILED,
                summary="规律事件日历返回不可用的空结果。",
                payload={**called_payload, "reason": reason},
                error_message=reason,
                data_state="unavailable",
            )

        known_definition_keys = {spec.key for spec in definitions}
        requested_targets = {
            (target["definition_key"], target["subject"].upper())
            for target in missing_targets
        }
        stored = []
        warnings = list(discovery_output.notes)
        for candidate in discovery_output.events:
            if candidate.category != category or candidate.definition_key not in known_definition_keys:
                warnings.append(f"忽略未配置事件定义：{candidate.definition_key}")
                continue
            if (candidate.definition_key, candidate.subject.upper()) not in requested_targets:
                warnings.append(
                    f"忽略未请求事件主体：{candidate.definition_key}/{candidate.subject}"
                )
                continue
            stored.append(self.repository.upsert_candidate(candidate, now=now))
        return StepResult(
            status=StepStatus.SUCCEEDED,
            summary=f"日历 API 已补充 {len(stored)} 条未来事件。",
            payload={
                **called_payload,
                "discovered_count": len(stored),
                "missing_definitions": discovery_output.missing_definitions,
                "warnings": warnings,
            },
            data_state="mock" if discovery.is_mock else "live",
        )

    def _run_result_step(
        self, context: Any, *, category: str, now: datetime
    ) -> StepResult:
        due_events = self.repository.due_events(category, now=now)
        base_payload = {
            "operation": "collect_result",
            "due_count": len(due_events),
            "api_call_count": 0,
            "completed_count": 0,
            "errors": [],
            "warnings": [],
            "is_mock": False,
        }
        if not due_events:
            return StepResult(
                status=StepStatus.SKIPPED,
                summary="今天以前没有待补结果的规律事件，未调用结果 API。",
                payload=base_payload,
                data_state="live",
            )
        if context.anomalo_adapter is None:
            reason = "结果步骤需要 Anomalo adapter，但当前未配置。"
            return StepResult(
                status=StepStatus.UNAVAILABLE,
                summary=reason,
                payload={**base_payload, "errors": [reason]},
                error_message=reason,
                data_state="unavailable",
            )

        errors: list[str] = []
        warnings: list[str] = []
        mock_flags: list[bool] = []
        completed_count = 0
        for event in due_events:
            error, is_mock, event_warnings = self._collect_result(context, event)
            mock_flags.append(is_mock)
            warnings.extend(event_warnings)
            if error:
                errors.append(error)
            else:
                completed_count += 1
        is_mock = bool(mock_flags) and all(mock_flags)
        if errors:
            status = StepStatus.FAILED
            data_state = "mixed" if completed_count else "unavailable"
            summary = (
                f"结果 API 已调用 {len(due_events)} 次：完成 {completed_count} 条，"
                f"失败 {len(errors)} 条。"
            )
        else:
            status = StepStatus.SUCCEEDED
            data_state = "mock" if is_mock else "live"
            summary = f"结果 API 已调用 {len(due_events)} 次并完成结果补全。"
        return StepResult(
            status=status,
            summary=summary,
            payload={
                **base_payload,
                "api_call_count": len(due_events),
                "completed_count": completed_count,
                "errors": errors,
                "warnings": warnings,
                "is_mock": is_mock,
            },
            error_message="；".join(errors) if errors else None,
            data_state=data_state,
        )

    def _collect_result(
        self, context: Any, event: Any
    ) -> tuple[str | None, bool, list[str]]:
        request = AnomaloRequest(
            session_id=f"urus-event-{event.id}-result",
            agent=context.scheduled_event_agent,
            response_format=result_response_format(),
            message=render_result_prompt(event),
        )
        response = self._investigate(
            context,
            request=request,
            operation="collect_result",
            event_id=event.id,
        )
        if response.disabled:
            return f"事件 {event.event_key} 的结果 API 当前未启用。", response.is_mock, []
        if response.error_code or response.output is None:
            return (
                response.error_message or f"事件 {event.event_key} 没有返回结果。",
                response.is_mock,
                [],
            )
        try:
            result = self._parse(response.output, EventResultOutput)
        except ValueError as exc:
            return f"事件 {event.event_key} 结果 JSON 无效：{exc}", response.is_mock, []
        if result.event_key != event.event_key:
            return (
                f"事件结果 key 不匹配：期望 {event.event_key}，收到 {result.event_key}",
                response.is_mock,
                [],
            )
        semantic_error = self._result_semantic_error(event, result)
        if semantic_error:
            return semantic_error, response.is_mock, []
        warnings = self._result_completeness_warnings(event, result)
        self.repository.save_result(event, result)
        return None, response.is_mock, warnings

    @staticmethod
    def _result_semantic_error(event: Any, result: EventResultOutput) -> str | None:
        if result.result_status == "not_released" and result.next_check_at is None:
            return (
                f"事件结果 {result.event_key} 尚未发布但没有 next_check_at；"
                "拒绝入库以避免每次工作流重复查询。"
            )
        if (
            result.result_status == "partial"
            and result.needs_follow_up
            and result.next_check_at is None
        ):
            return (
                f"事件结果 {result.event_key} 需要后续调查但没有 "
                "next_check_at；拒绝入库。"
            )
        if result.result_status not in {"confirmed", "revised"}:
            return None

        rules = event_result_rules(event.event_type)
        actual_fact_names = {
            fact.name.strip().lower()
            for fact in result.facts
            if fact.actual is not None
            and (not isinstance(fact.actual, str) or fact.actual.strip())
        }
        required = {
            str(name).strip().lower()
            for name in rules.get("required_actual_facts", [])
            if name
        }
        missing = sorted(required - actual_fact_names)
        if missing:
            return (
                f"事件结果 {result.event_key} 缺少必须的实际值："
                f"{', '.join(missing)}；拒绝入库。"
            )

        required_any = {
            str(name).strip().lower()
            for name in rules.get("required_actual_any_of", [])
            if name
        }
        if required_any and not actual_fact_names.intersection(required_any):
            return (
                f"事件结果 {result.event_key} 至少需要一个实际值："
                f"{', '.join(sorted(required_any))}；拒绝入库。"
            )

        if rules.get("require_sources") and not result.sources:
            return f"事件结果 {result.event_key} 没有结果来源；拒绝入库。"
        return None

    @staticmethod
    def _result_completeness_warnings(
        event: Any, result: EventResultOutput
    ) -> list[str]:
        if result.result_status not in {"confirmed", "revised"}:
            return []
        expected = {
            str(name).strip().lower()
            for name in event_result_rules(event.event_type).get("expected_facts", [])
            if name
        }
        actual_names = {
            fact.name.strip().lower()
            for fact in result.facts
            if fact.actual is not None
            and (not isinstance(fact.actual, str) or fact.actual.strip())
        }
        missing = sorted(expected - actual_names)
        if not missing:
            return []
        return [
            f"事件结果 {result.event_key} 缺少可选期望实际值：{', '.join(missing)}。"
        ]

    @staticmethod
    def _aggregate_status(schedule_step: StepResult, result_step: StepResult) -> StepStatus:
        phases = (schedule_step.status, result_step.status)
        if StepStatus.FAILED in phases:
            return StepStatus.FAILED
        if StepStatus.UNAVAILABLE in phases:
            if any(status == StepStatus.SUCCEEDED for status in phases):
                return StepStatus.FAILED
            return StepStatus.UNAVAILABLE
        return StepStatus.SUCCEEDED

    @staticmethod
    def _aggregate_data_state(
        schedule_step: StepResult, result_step: StepResult
    ) -> tuple[str, bool]:
        api_modes: list[bool] = []
        if schedule_step.payload.get("api_called"):
            api_modes.append(bool(schedule_step.payload.get("is_mock")))
        if result_step.payload.get("api_call_count"):
            api_modes.append(bool(result_step.payload.get("is_mock")))
        is_mock = bool(api_modes) and all(api_modes)
        has_failure = any(
            step.status in {StepStatus.FAILED, StepStatus.UNAVAILABLE}
            for step in (schedule_step, result_step)
        )
        has_success = any(
            step.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
            for step in (schedule_step, result_step)
        )
        if has_failure:
            return ("mixed" if has_success else "unavailable"), is_mock
        if api_modes and any(api_modes) and not all(api_modes):
            return "mixed", is_mock
        return ("mock" if is_mock else "live"), is_mock

    @staticmethod
    def _phase_payload(result: StepResult) -> dict[str, Any]:
        payload = dict(result.payload)
        payload["status"] = result.status.value
        payload["summary"] = result.summary
        payload["data_state"] = result.data_state
        payload["error_message"] = result.error_message
        return payload

    @staticmethod
    def _payload_strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item]

    def _investigate(
        self,
        context: Any,
        *,
        request: AnomaloRequest,
        operation: str,
        event_id: str | None,
    ) -> AnomaloResponse:
        started = utc_now()
        try:
            response = context.anomalo_adapter.investigate(request)
        except Exception as exc:
            response = AnomaloResponse(
                final_text=None,
                is_mock=False,
                error_code="adapter_error",
                error_message=str(exc),
            )
        self.repository.save_agent_run(
            run_id=context.run_id,
            event_id=event_id,
            operation=operation,
            agent=request.agent or "unknown",
            session_id=request.session_id,
            status="disabled"
            if response.disabled
            else "failed"
            if response.error_code
            else "succeeded",
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
        return response

    @staticmethod
    def _parse(value: Any, model: Any) -> Any:
        try:
            if isinstance(value, str):
                return model.model_validate_json(value)
            return model.model_validate(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    def _missing_schedule_targets(
        self,
        context: Any,
        *,
        category: str,
        definitions: list[Any],
        now: datetime,
    ) -> list[dict[str, str]]:
        if category == "macro":
            subjects = ["market"]
        else:
            configured = getattr(context, "event_instrument_symbols", [])
            subjects = list(dict.fromkeys(str(symbol).upper() for symbol in configured if symbol))

        targets: list[dict[str, str]] = []
        for definition in definitions:
            for subject in subjects:
                if self.repository.has_future_event(
                    definition.key,
                    subject=subject,
                    now=now,
                ):
                    continue
                targets.append(
                    {
                        "definition_key": definition.key,
                        "subject_type": definition.subject_type,
                        "subject": subject,
                    }
                )
        return targets

    def _record_market_reactions(self, context: Any, category: str, now: datetime) -> int:
        count = 0
        market_payload = context.results.get("1a")
        instrument_payload = context.results.get("3a")
        market = market_payload.payload if market_payload else {}
        instruments = instrument_payload.payload.get("instruments", []) if instrument_payload else []
        by_symbol = {
            str(item.get("symbol")): item
            for item in instruments
            if isinstance(item, dict)
        }
        for event in self.repository.list_events(category):
            if event.status not in {"confirmed", "revised"}:
                continue
            if any(
                reaction.window == "post_close" and reaction.status == "measured"
                for reaction in event.market_reactions
            ):
                continue
            card = (
                market
                if event.subject_type == "market"
                else by_symbol.get(event.subject.upper(), {})
            )
            if not isinstance(card, dict):
                continue
            current = card.get("regular_price") or card.get("last_price")
            previous = card.get("previous_close")
            payload = {
                "symbol": event.subject if event.subject_type == "symbol" else "QQQ",
                "current_price": current,
                "previous_close": previous,
                "change_percent": card.get("change_percent"),
                "source": card.get("source") or card.get("provider"),
                "run_type": context.run_type,
            }
            self.repository.save_market_reaction(
                event_id=event.id,
                run_id=context.run_id,
                window="post_close",
                status="measured" if current is not None else "unavailable",
                payload=payload,
                measured_at=now,
            )
            count += 1
        return count

    @staticmethod
    def _next_check(events: list[dict[str, Any]]) -> str | None:
        values = [event.get("next_check_at") for event in events if event.get("next_check_at")]
        return min(values) if values else None

    @staticmethod
    def _unavailable(payload: dict[str, Any], reason: str) -> StepResult:
        return StepResult(
            status=StepStatus.UNAVAILABLE,
            summary=f"预期事件不可用：{reason}",
            payload={
                **payload,
                "status": StepStatus.UNAVAILABLE.value,
                "reason": reason,
                "events": [],
                "counts": {},
            },
            data_state="unavailable",
        )

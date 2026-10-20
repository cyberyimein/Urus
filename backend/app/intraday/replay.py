"""Explicit, offline replay of timestamped P2 capture observations."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from app.decision_harness.contracts import content_sha256
from app.intraday.equities import build_intraday_projection
from app.intraday.events import _normalized_spec, evaluate


REPLAY_SCHEMA_VERSION = "urus.intraday.replay.v1"
_PROVENANCE_VALUES = {"original_observed", "reconstructed"}


def replay_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay valid captures in observed order using the live P3/P4/P5 cores.

    Each task receives its own event state and monotonic source-slot cursor.
    A duplicate or late slot is reported without mutating that task's state.
    The function is deterministic and does not inspect wall-clock time.
    """

    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise TypeError("observations must be a sequence of objects")
    if spec is not None and not isinstance(spec, Mapping):
        raise TypeError("spec must be an object")
    normalized_rule_spec = _normalized_spec(spec)
    rule_spec_hash = content_sha256(normalized_rule_spec)
    rule_spec_snapshot = {**normalized_rule_spec, "sha256": rule_spec_hash}
    projection_parameters = {
        "market_calendar": normalized_rule_spec["market_calendar"],
        "market_timezone": normalized_rule_spec["market_timezone"],
    }

    prepared: list[tuple[datetime | None, int, Mapping[str, Any] | None, str | None]] = []
    for index, raw_observation in enumerate(observations):
        if not isinstance(raw_observation, Mapping):
            prepared.append((None, index, None, "invalid_observation"))
            continue

        provenance = raw_observation.get("provenance")
        if not isinstance(provenance, str) or provenance not in _PROVENANCE_VALUES:
            raise ValueError("provenance must be original_observed or reconstructed")

        observed_at = _parse_aware_utc(raw_observation.get("observed_at"))
        if observed_at is None:
            prepared.append((None, index, raw_observation, "invalid_observed_at"))
        else:
            prepared.append((observed_at, index, raw_observation, None))

    prepared.sort(
        key=lambda item: (
            item[0] if item[0] is not None else datetime.min.replace(tzinfo=UTC),
            item[1],
        )
    )

    states_by_task: dict[str, dict[str, Any]] = {}
    slot_cursor_by_task: dict[str, datetime] = {}
    provenance_by_task: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    counts_by_provenance = {"original_observed": 0, "reconstructed": 0}
    observations_processed = 0

    for observed_at, index, observation, initial_reason in prepared:
        if observation is None:
            skipped.append(_skip(index, "invalid_observation", "observation must be an object."))
            continue
        if initial_reason is not None:
            skipped.append(
                _skip(index, initial_reason, "observed_at must be an aware ISO timestamp.", observation)
            )
            continue

        assert observed_at is not None
        provenance = str(observation["provenance"])
        capture = observation.get("capture")
        if not isinstance(capture, Mapping):
            skipped.append(_skip(index, "invalid_capture", "capture must be an object.", observation))
            continue
        capture = dict(capture)
        task_name = str(capture.get("task_name") or "").strip()
        capture_id = str(capture.get("capture_id") or "")
        data = capture.get("data")
        if not task_name or not isinstance(data, Mapping):
            skipped.append(_skip(index, "invalid_capture", "capture requires task_name and object data.", observation))
            continue

        try:
            expected_hash = content_sha256(data)
        except (TypeError, ValueError, OverflowError):
            skipped.append(_skip(index, "invalid_capture_hash", "capture data cannot be canonically hashed.", observation))
            continue
        if str(capture.get("content_sha256") or "") != expected_hash:
            skipped.append(_skip(index, "invalid_capture_hash", "capture content hash does not match data.", observation))
            continue

        scheduled_at = _parse_aware_utc(capture.get("scheduled_at"))
        captured_at = _parse_aware_utc(capture.get("captured_at"))
        data_scheduled_at = _parse_aware_utc(data.get("scheduled_at"))
        if (
            scheduled_at is None
            or captured_at is None
            or data_scheduled_at is None
            or data_scheduled_at != scheduled_at
        ):
            skipped.append(_skip(index, "invalid_capture_time", "capture timestamps are invalid or inconsistent.", observation))
            continue

        if scheduled_at > observed_at:
            skipped.append(_skip(index, "future_slot", "capture slot is later than observed_at.", observation))
            continue
        if captured_at > observed_at:
            skipped.append(_skip(index, "known_after_observation", "capture was recorded after observed_at.", observation))
            continue
        if captured_at < scheduled_at:
            skipped.append(_skip(index, "invalid_capture_time", "capture was recorded before its scheduled slot.", observation))
            continue

        task_provenance = provenance_by_task.get(task_name)
        if task_provenance is not None and task_provenance != provenance:
            skipped.append(
                _skip(
                    index,
                    "mixed_provenance_for_task",
                    "a task replay cannot mix original observations with reconstructed captures.",
                    observation,
                )
            )
            continue

        cursor = slot_cursor_by_task.get(task_name)
        if cursor is not None and scheduled_at <= cursor:
            reason_code = "duplicate_slot" if scheduled_at == cursor else "late_old_slot"
            message = (
                "task slot was already processed."
                if scheduled_at == cursor
                else "late older task slot cannot replace the task's replay state."
            )
            skipped.append(_skip(index, reason_code, message, observation))
            continue

        try:
            projection = build_intraday_projection(
                capture,
                now=observed_at,
                market_timezone=projection_parameters["market_timezone"],
                market_calendar=projection_parameters["market_calendar"],
            )
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            skipped.append(
                _skip(index, "invalid_capture", str(exc) or "capture failed P3 validation.", observation)
            )
            continue

        try:
            evaluated = evaluate(
                projection,
                states_by_task.get(task_name),
                normalized_rule_spec,
                evaluated_at=observed_at,
            )
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            skipped.append(
                _skip(index, "invalid_evaluation", str(exc) or "capture failed event evaluation.", observation)
            )
            continue

        states_by_task[task_name] = evaluated["state"]
        slot_cursor_by_task[task_name] = scheduled_at
        provenance_by_task.setdefault(task_name, provenance)
        observations_processed += 1
        counts_by_provenance[provenance] += 1
        transitions = evaluated.get("transitions", [])
        transition_items = [
            transition for transition in transitions if isinstance(transition, Mapping)
        ] if isinstance(transitions, list) else []
        evaluations.append(
            {
                "input_index": index,
                "task_name": task_name,
                "capture_id": capture_id,
                "content_sha256": str(capture.get("content_sha256") or ""),
                "scheduled_at": scheduled_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "provenance": provenance,
                "projection_status": str(projection.get("status") or "unavailable"),
                "event_count": len(transition_items),
                "capability_summary": _capability_summary(evaluated.get("capabilities")),
            }
        )
        for transition in transition_items:
            events.append(
                {
                    **dict(transition),
                    "observed_at": observed_at.isoformat(),
                    "provenance": provenance,
                }
            )

    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "rule_spec_snapshot": rule_spec_snapshot,
        "rule_spec_sha256": rule_spec_hash,
        "projection_parameters": projection_parameters,
        "observations_processed": observations_processed,
        "skipped": skipped,
        "events": events,
        "evaluations": evaluations,
        "states_by_task": states_by_task,
        "counts_by_provenance": counts_by_provenance,
    }


def _capability_summary(value: Any) -> dict[str, Any]:
    capabilities = (
        [item for item in value.values() if isinstance(item, Mapping)]
        if isinstance(value, Mapping)
        else []
    )
    available_count = sum(item.get("available") is True for item in capabilities)
    reason_counts: dict[str, int] = {}
    for capability in capabilities:
        if capability.get("available") is True:
            continue
        reason_code = str(capability.get("reason_code") or "unavailable")
        reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
    return {
        "available_count": available_count,
        "unavailable_count": len(capabilities) - available_count,
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay local P2 intraday captures without database access")
    parser.add_argument("--input", required=True, help="JSON list of timestamped capture observations")
    parser.add_argument("--output", required=True, help="new JSON report path; existing paths are refused")
    args = parser.parse_args(argv)

    try:
        observations = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(observations, list):
            raise ValueError("input JSON must be a list of observations")
        report = replay_observations(observations)
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default)
        with Path(args.output).open("x", encoding="utf-8") as output_file:
            output_file.write(encoded)
            output_file.write("\n")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"intraday replay failed: {exc}", file=sys.stderr)
        return 2
    return 0


def _skip(
    index: int,
    reason_code: str,
    message: str,
    observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"index": index, "reason_code": reason_code, "message": message}
    if observation is not None:
        capture = observation.get("capture")
        if isinstance(capture, Mapping):
            if capture.get("task_name"):
                record["task_name"] = str(capture["task_name"])
            if capture.get("capture_id"):
                record["capture_id"] = str(capture["capture_id"])
        observed_at = _parse_aware_utc(observation.get("observed_at"))
        if observed_at is not None:
            record["observed_at"] = observed_at.isoformat()
    return record


def _parse_aware_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    raise SystemExit(main())

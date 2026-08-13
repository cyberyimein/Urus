"""Import a completed Stage 4B replay into the configured SQLite database.

The importer is intentionally append-only: it copies only AI session, run,
tool-call, model-turn, and trace rows. Existing rows or a missing workflow
run abort the import before any target transaction is committed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings


AI_TABLES = (
    "ai_decision_sessions",
    "ai_decision_runs",
    "ai_model_turns",
    "ai_tool_calls",
    "ai_trace_nodes",
)


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(f"Only SQLite targets are supported: {database_url}")
    raw = database_url.removeprefix(prefix)
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ValueError(f"Missing table: {table}")
    return [str(row[1]) for row in rows]


def _rows(
    connection: sqlite3.Connection,
    table: str,
    where: str = "",
    parameters: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    columns = _columns(connection, table)
    statement = f"SELECT {', '.join(columns)} FROM {table}"
    if where:
        statement += f" WHERE {where}"
    return [tuple(row) for row in connection.execute(statement, parameters).fetchall()]


def _insert_rows(
    connection: sqlite3.Connection,
    table: str,
    rows: list[tuple[object, ...]],
    source_columns: list[str],
) -> None:
    if not rows:
        return
    target_columns = _columns(connection, table)
    source_index = {name: index for index, name in enumerate(source_columns)}
    missing = [name for name in target_columns if name not in source_index]
    if missing:
        raise ValueError(f"Source is missing {table} column(s): {missing}")
    columns = target_columns
    quoted = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    values = [
        tuple(row[source_index[column]] for column in columns)
        for row in rows
    ]
    connection.executemany(
        f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
        values,
    )


def import_session(source_path: Path, target_path: Path, session_id: str | None) -> dict[str, object]:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if not target_path.exists():
        raise FileNotFoundError(target_path)

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    target.execute("PRAGMA foreign_keys = ON")
    try:
        session_where = "id = ?" if session_id else "1 = 1"
        session_parameters = (session_id,) if session_id else ()
        session_rows = _rows(source, "ai_decision_sessions", session_where, session_parameters)
        if not session_rows:
            raise ValueError("No matching AI decision session in source database")
        session_columns = _columns(source, "ai_decision_sessions")
        session_index = {name: index for index, name in enumerate(session_columns)}
        session_ids = [str(row[session_index["id"]]) for row in session_rows]
        workflow_run_ids = [str(row[session_index["workflow_run_id"]]) for row in session_rows]

        run_rows = _rows(
            source,
            "ai_decision_runs",
            f"decision_session_id IN ({', '.join('?' for _ in session_ids)})",
            tuple(session_ids),
        )
        run_columns = _columns(source, "ai_decision_runs")
        run_index = {name: index for index, name in enumerate(run_columns)}
        run_ids = [str(row[run_index["id"]]) for row in run_rows]

        def linked_rows(table: str) -> list[tuple[object, ...]]:
            if not run_ids:
                return []
            return _rows(
                source,
                table,
                f"decision_run_id IN ({', '.join('?' for _ in run_ids)})",
                tuple(run_ids),
            )

        model_turn_rows = linked_rows("ai_model_turns")
        tool_call_rows = linked_rows("ai_tool_calls")
        trace_rows = _rows(
            source,
            "ai_trace_nodes",
            f"decision_session_id IN ({', '.join('?' for _ in session_ids)})",
            tuple(session_ids),
        )

        target_run_ids = {
            str(row[0])
            for row in target.execute(
                f"SELECT id FROM runs WHERE id IN ({', '.join('?' for _ in workflow_run_ids)})",
                tuple(workflow_run_ids),
            ).fetchall()
        }
        missing_workflow_runs = sorted(set(workflow_run_ids) - target_run_ids)
        if missing_workflow_runs:
            raise ValueError(f"Missing workflow run(s) in target database: {missing_workflow_runs}")

        for table, rows in (
            ("ai_decision_sessions", session_rows),
            ("ai_decision_runs", run_rows),
            ("ai_model_turns", model_turn_rows),
            ("ai_tool_calls", tool_call_rows),
            ("ai_trace_nodes", trace_rows),
        ):
            if not rows:
                continue
            primary_key = "id"
            source_columns = _columns(source, table)
            key_index = source_columns.index(primary_key)
            ids = [row[key_index] for row in rows]
            existing = {
                row[0]
                for row in target.execute(
                    f"SELECT {primary_key} FROM {table} WHERE {primary_key} IN ({', '.join('?' for _ in ids)})",
                    tuple(ids),
                ).fetchall()
            }
            if existing:
                raise ValueError(f"Target already contains {table} row(s): {sorted(existing)}")

        target.execute("BEGIN")
        _insert_rows(target, "ai_decision_sessions", session_rows, _columns(source, "ai_decision_sessions"))
        _insert_rows(target, "ai_decision_runs", run_rows, _columns(source, "ai_decision_runs"))
        _insert_rows(target, "ai_model_turns", model_turn_rows, _columns(source, "ai_model_turns"))
        _insert_rows(target, "ai_tool_calls", tool_call_rows, _columns(source, "ai_tool_calls"))
        _insert_rows(target, "ai_trace_nodes", trace_rows, _columns(source, "ai_trace_nodes"))
        target.commit()
        return {
            "status": "succeeded",
            "source": str(source_path),
            "target": str(target_path),
            "session_ids": session_ids,
            "workflow_run_ids": workflow_run_ids,
            "counts": {
                "ai_decision_sessions": len(session_rows),
                "ai_decision_runs": len(run_rows),
                "ai_model_turns": len(model_turn_rows),
                "ai_tool_calls": len(tool_call_rows),
                "ai_trace_nodes": len(trace_rows),
            },
        }
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Target SQLite path; defaults to the configured DATABASE_URL.",
    )
    args = parser.parse_args(argv)
    target = args.target or _sqlite_path(get_settings().database_url)
    try:
        result = import_session(args.source, target, args.session_id)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

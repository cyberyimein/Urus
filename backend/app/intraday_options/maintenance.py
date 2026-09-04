from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
from typing import Any

from sqlalchemy.engine import make_url


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_daily_backup(
    database_url: str,
    raw_payload_dir: str | Path,
    backup_dir: str | Path,
    trading_date: date,
    *,
    retention_days: int = 14,
) -> Path:
    """Create an app-consistent SQLite backup plus the day's immutable raw payloads."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or url.database in {None, ":memory:"}:
        raise ValueError("intraday backup currently requires a file-backed SQLite database")
    source_db = Path(url.database).expanduser().resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    root = Path(backup_dir).expanduser().resolve()
    # Keep one local, app-consistent copy. Time Machine versions this directory;
    # retaining a full SQLite copy per day on the startup disk would grow
    # quadratically as the research database grows.
    day_dir = root / "latest"
    day_dir.mkdir(parents=True, exist_ok=True)

    database_backup = day_dir / "intraday_options.db"
    temporary_db = database_backup.with_suffix(".db.tmp")
    source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    destination = sqlite3.connect(temporary_db)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    temporary_db.replace(database_backup)

    with sqlite3.connect(f"file:{database_backup}?mode=ro", uri=True) as check_db:
        integrity_check = str(check_db.execute("PRAGMA integrity_check").fetchone()[0])
        table_names = [
            str(row[0])
            for row in check_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        table_counts = {
            table: int(check_db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in table_names
        }
    if integrity_check != "ok":
        raise RuntimeError(f"backup SQLite integrity check failed: {integrity_check}")

    raw_day = Path(raw_payload_dir).expanduser().resolve() / trading_date.isoformat()
    raw_archive = day_dir / "raw.tar"
    temporary_archive = raw_archive.with_suffix(".tar.tmp")
    with tarfile.open(temporary_archive, "w") as archive:
        if raw_day.is_dir():
            for item in sorted(raw_day.glob("*.json.gz")):
                archive.add(item, arcname=item.name, recursive=False)
    temporary_archive.replace(raw_archive)

    manifest: dict[str, Any] = {
        "schema_version": "intraday-options-backup-v1",
        "trading_date": trading_date.isoformat(),
        "created_at": datetime.now().astimezone().isoformat(),
        "source_database": str(source_db),
        "database_integrity_check": integrity_check,
        "table_counts": table_counts,
        "files": {},
    }
    for item in (database_backup, raw_archive):
        manifest["files"][item.name] = {
            "bytes": item.stat().st_size,
            "sha256": _sha256(item),
        }
    manifest_path = day_dir / "manifest.json"
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)

    (root / "retention-policy.json").write_text(
        json.dumps({
            "local_full_copies": 1,
            "time_machine_manages_history": True,
            "configured_retention_days": retention_days,
        }),
        encoding="utf-8",
    )
    return manifest_path

from datetime import datetime


def utc_now() -> datetime:
    """Return the current UTC time for workflow persistence."""
    return datetime.utcnow()


def to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

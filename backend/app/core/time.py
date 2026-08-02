from datetime import UTC, datetime


def as_utc(value: datetime | str) -> datetime:
    """Return a timestamp as an aware UTC datetime.

    SQLite does not preserve timezone information for SQLAlchemy's
    ``DateTime(timezone=True)`` columns.  All timestamps written by Urus are
    UTC, so a naive value read back from the database is interpreted as UTC at
    the API boundary rather than being reinterpreted in the server's locale.
    """
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None

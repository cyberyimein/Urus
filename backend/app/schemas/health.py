from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str


class DatabaseHealthResponse(BaseModel):
    schema_version: str
    status: str
    checked_at: datetime
    next_check_at: datetime
    integrity_check: str | None = None
    foreign_key_violations: int | None = None
    duration_ms: int | None = None
    database_size_bytes: int | None = None
    message: str | None = None


class VersionResponse(BaseModel):
    app_name: str
    app_version: str
    api_schema_version: str

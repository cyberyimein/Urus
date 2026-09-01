from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str


class VersionResponse(BaseModel):
    app_name: str
    app_version: str
    api_schema_version: str

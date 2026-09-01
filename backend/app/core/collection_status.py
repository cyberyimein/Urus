from enum import StrEnum


class CollectionStatus(StrEnum):
    """Status values shared by provider-backed collection services."""

    PLACEHOLDER = "placeholder"
    UNAVAILABLE = "unavailable"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

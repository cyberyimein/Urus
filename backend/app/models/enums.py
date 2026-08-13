from enum import StrEnum


class RunType(StrEnum):
    PRE_MARKET = "pre_market"
    PRE_CLOSE = "pre_close"
    POST_CLOSE_REVIEW = "post_close_review"
    MANUAL_ANALYSIS = "manual_analysis"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    MIXED = "mixed"
    PARTIAL = "partial"
    FAILED = "failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PLACEHOLDER = "placeholder"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
    FAILED = "failed"

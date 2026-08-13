from enum import StrEnum


class RunTypeValue(StrEnum):
    PRE_MARKET = "pre_market"
    PRE_CLOSE = "pre_close"
    POST_CLOSE_REVIEW = "post_close_review"
    MANUAL_ANALYSIS = "manual_analysis"


class RunStatusValue(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    MIXED = "mixed"
    PARTIAL = "partial"
    FAILED = "failed"


class StepStatusValue(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PLACEHOLDER = "placeholder"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
    FAILED = "failed"


class StepCodeValue(StrEnum):
    MARKET = "1a"
    MARKET_EVENT = "1b"
    OPTIONS = "2"
    INSTRUMENT = "3a"
    INSTRUMENT_EVENT = "3b"
    DECISION = "4"
    OUTPUT = "5"

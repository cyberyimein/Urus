"""Daily Decision Harness modules.

The package intentionally does not eagerly import the service.  Repository
modules import the lightweight contracts submodule, and eager re-exports here
would create a repository/package circular import during application startup.
"""

__all__ = ["DailyMarketEvidenceService"]


def __getattr__(name: str):
    if name == "DailyMarketEvidenceService":
        from app.decision_harness.market_evidence import DailyMarketEvidenceService

        return DailyMarketEvidenceService
    raise AttributeError(name)

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventDefinitionSpec:
    key: str
    category: str
    subject_type: str
    event_type: str
    title: str
    result_schema_name: str
    description: str
    cadence: str
    preferred_sources: tuple[str, ...]
    horizon_days: int = 120
    discovery_mode: str = "scheduled"


# Validation deliberately starts with a small, high-signal scheduled universe.
# The breaking-event definition is represented in config but not enabled here.
DEFAULT_EVENT_DEFINITIONS: tuple[EventDefinitionSpec, ...] = (
    EventDefinitionSpec(
        key="macro:fomc_decision",
        category="macro",
        subject_type="market",
        event_type="fomc_decision",
        title="FOMC rate decision",
        result_schema_name="scheduled_event_result",
        description="Scheduled FOMC monetary-policy decision and statement release.",
        cadence="approximately every six weeks",
        preferred_sources=("Federal Reserve FOMC calendar", "Federal Reserve statements"),
    ),
    EventDefinitionSpec(
        key="macro:cpi",
        category="macro",
        subject_type="market",
        event_type="cpi",
        title="US Consumer Price Index release",
        result_schema_name="scheduled_event_result",
        description="Monthly US CPI and core CPI release.",
        cadence="monthly",
        preferred_sources=("US Bureau of Labor Statistics release calendar",),
    ),
    EventDefinitionSpec(
        key="macro:pce",
        category="macro",
        subject_type="market",
        event_type="pce",
        title="US Personal Income and Outlays release",
        result_schema_name="scheduled_event_result",
        description="Monthly PCE and core PCE inflation release.",
        cadence="monthly",
        preferred_sources=("US Bureau of Economic Analysis release schedule",),
    ),
    EventDefinitionSpec(
        key="macro:nonfarm_payrolls",
        category="macro",
        subject_type="market",
        event_type="nonfarm_payrolls",
        title="US Employment Situation release",
        result_schema_name="scheduled_event_result",
        description="Monthly nonfarm payrolls, unemployment rate, and wage data release.",
        cadence="monthly",
        preferred_sources=("US Bureau of Labor Statistics release calendar",),
    ),
    EventDefinitionSpec(
        key="macro:gdp",
        category="macro",
        subject_type="market",
        event_type="gdp",
        title="US Gross Domestic Product release",
        result_schema_name="scheduled_event_result",
        description="Advance, second, or third estimate of quarterly US GDP.",
        cadence="quarterly with revisions",
        preferred_sources=("US Bureau of Economic Analysis release schedule",),
    ),
    EventDefinitionSpec(
        key="macro:ism_manufacturing",
        category="macro",
        subject_type="market",
        event_type="ism_manufacturing",
        title="ISM Manufacturing PMI release",
        result_schema_name="scheduled_event_result",
        description="Monthly ISM Manufacturing Report On Business release.",
        cadence="monthly",
        preferred_sources=("Institute for Supply Management release calendar",),
    ),
    EventDefinitionSpec(
        key="macro:ism_services",
        category="macro",
        subject_type="market",
        event_type="ism_services",
        title="ISM Services PMI release",
        result_schema_name="scheduled_event_result",
        description="Monthly ISM Services Report On Business release.",
        cadence="monthly",
        preferred_sources=("Institute for Supply Management release calendar",),
    ),
    EventDefinitionSpec(
        key="instrument:earnings",
        category="instrument",
        subject_type="symbol",
        event_type="earnings",
        title="Quarterly earnings release and earnings call",
        result_schema_name="scheduled_event_result",
        description=(
            "Official quarterly earnings release, webcast, and management guidance "
            "for one configured watchlist company."
        ),
        cadence="quarterly",
        preferred_sources=(
            "company investor-relations calendar",
            "company press releases",
            "SEC filings",
        ),
    ),
)


BREAKING_EVENT_AGENT_NAME = "breaking-event-investigator"

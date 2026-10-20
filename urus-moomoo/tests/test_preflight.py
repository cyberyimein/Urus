from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from urus_moomoo.config import CollectionTask, CollectorConfig
from urus_moomoo import cli
from urus_moomoo.preflight import latest_completed_session, run_preflight


class _Calendar:
    def is_session(self, value: date) -> bool:
        return value.weekday() < 5

    def session_close(self, value: date) -> datetime:
        return datetime(value.year, value.month, value.day, 20, tzinfo=UTC)


def _config() -> CollectorConfig:
    return CollectorConfig(
        timezone="Asia/Tokyo",
        output_dir=Path("/unused"),
        tasks=(
            CollectionTask("market-post-close", True, "post-close", ("SPY",), "instrument_cards"),
            CollectionTask("options-snapshot-post-close", True, "post-close", ("SPY",), "options_snapshot"),
        ),
        publisher={"api_url": "http://gateway:7777", "timeout_seconds": 5},
    )


def _plan(_url: str, trading_date: str, _timeout: float) -> dict:
    return {
        "schema_version": "urus.observation.collection_plan.v1",
        "plan_id": "a" * 64,
        "trading_date": trading_date,
        "group_ids": ["core"],
        "group_version_ids": ["version-1"],
        "market_symbols": ["SPY"],
        "options_symbols": ["SPY"],
    }


def test_preflight_uses_completed_session_and_checks_api_without_collecting() -> None:
    calls = []

    def get(url: str, timeout: float):
        calls.append(("GET", url, timeout))
        return 200, {"status": "ok", "database": "ok"}

    def fetch(url: str, trading_date: str, timeout: float):
        calls.append(("PLAN", url, trading_date, timeout))
        return _plan(url, trading_date, timeout)

    result = run_preflight(
        _config(),
        now=datetime(2026, 9, 22, 10, tzinfo=UTC),
        calendar=_Calendar(),
        get=get,
        fetch_plan=fetch,
    )
    assert result == {
        "trading_date": "2026-09-21",
        "group_count": 1,
        "market_symbol_count": 1,
        "options_symbol_count": 1,
    }
    assert calls == [
        ("GET", "http://gateway:7777/api/health", 5.0),
        ("PLAN", "http://gateway:7777", "2026-09-21", 5.0),
    ]


def test_preflight_fails_on_bad_health_before_plan() -> None:
    with pytest.raises(RuntimeError, match="health check failed"):
        run_preflight(
            _config(),
            now=datetime(2026, 9, 22, 10, tzinfo=UTC),
            calendar=_Calendar(),
            get=lambda *_args: (200, {"status": "ok", "database": "error"}),
            fetch_plan=lambda *_args: pytest.fail("must not request a plan"),
        )


def test_preflight_fails_on_invalid_plan() -> None:
    bad = _plan("", "2026-09-21", 5)
    bad["options_symbols"] = []
    with pytest.raises(RuntimeError, match="options_symbols"):
        run_preflight(
            _config(),
            now=datetime(2026, 9, 22, 10, tzinfo=UTC),
            calendar=_Calendar(),
            get=lambda *_args: (200, {"status": "ok", "database": "ok"}),
            fetch_plan=lambda *_args: bad,
        )


def test_latest_completed_session_waits_for_close() -> None:
    assert latest_completed_session(_Calendar(), datetime(2026, 9, 21, 19, tzinfo=UTC)) == date(2026, 9, 18)


def test_cli_preflight_does_not_start_market_runtime(monkeypatch, capsys) -> None:
    from urus_moomoo import preflight

    monkeypatch.setattr(cli, "load_config", lambda _path: _config())
    monkeypatch.setattr(cli, "_build_runtime", lambda _config: pytest.fail("must not start OpenD"))
    monkeypatch.setattr(preflight, "run_preflight", lambda _config: {
        "trading_date": "2026-09-21",
        "group_count": 1,
        "market_symbol_count": 1,
        "options_symbol_count": 1,
    })
    assert cli.main(["preflight", "--config", "/unused/config.toml"]) == 0
    assert "observation preflight ok" in capsys.readouterr().out

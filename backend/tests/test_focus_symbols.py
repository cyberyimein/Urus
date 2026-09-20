from __future__ import annotations

from sqlalchemy import func, select

from app.models.focus import FocusSymbolModel
from app.models.observation import ObservationGroupVersionModel, ObservationRunModel, ObservationUniverseRevisionModel
from app.models.universe import InstrumentUniverseVersionModel


def _scope_counts(app) -> dict[str, int]:
    with app.state.session_factory() as session:
        return {
            "universe_revisions": int(
                session.scalar(select(func.count()).select_from(ObservationUniverseRevisionModel)) or 0
            ),
            "universe_versions": int(
                session.scalar(select(func.count()).select_from(InstrumentUniverseVersionModel)) or 0
            ),
            "group_versions": int(
                session.scalar(select(func.count()).select_from(ObservationGroupVersionModel)) or 0
            ),
            "runs": int(session.scalar(select(func.count()).select_from(ObservationRunModel)) or 0),
        }


def test_focus_symbols_normalize_are_idempotent_and_persist(client) -> None:
    assert client.get("/api/focus-symbols").json() == []

    added = client.put("/api/focus-symbols/ intc ")
    assert added.status_code == 200
    assert added.json()["symbol"] == "INTC"
    created_at = added.json()["created_at"]

    repeated = client.put("/api/focus-symbols/intc")
    assert repeated.status_code == 200
    assert repeated.json() == {"symbol": "INTC", "created_at": created_at}
    assert client.get("/api/focus-symbols").json() == [repeated.json()]


def test_focus_symbols_delete_is_idempotent_and_invalid_symbols_use_validation_error(client) -> None:
    assert client.delete("/api/focus-symbols/does-not-exist").json() == {
        "symbol": "DOES-NOT-EXIST",
        "removed": False,
    }

    client.put("/api/focus-symbols/SMH")
    removed = client.delete("/api/focus-symbols/smh")
    assert removed.status_code == 200
    assert removed.json() == {"symbol": "SMH", "removed": True}
    assert client.delete("/api/focus-symbols/SMH").json() == {
        "symbol": "SMH",
        "removed": False,
    }

    invalid = client.put("/api/focus-symbols/not a symbol")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


def test_focus_operations_do_not_change_universe_groups_or_runs(app, client) -> None:
    before = _scope_counts(app)

    assert client.put("/api/focus-symbols/QQQ").status_code == 200
    assert client.delete("/api/focus-symbols/QQQ").status_code == 200

    assert _scope_counts(app) == before
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FocusSymbolModel)) == 0

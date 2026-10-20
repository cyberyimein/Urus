from __future__ import annotations

import os
from pathlib import Path
import sys

from urus_moomoo.config import load_config


ROOT = Path(__file__).resolve().parents[2]


def _render_deploy_config(monkeypatch, tmp_path: Path):
    script = (ROOT / "scripts" / "deploy_apple_moomoo.sh").read_text(encoding="utf-8")
    marker = 'python3 - "$config" <<\'PY\'\n'
    embedded = script.split(marker, 1)[1].split("\nPY\n", 1)[0]
    config_path = tmp_path / "collection_tasks.local.toml"
    monkeypatch.setattr(sys, "argv", ["render", str(config_path)])
    exec(compile(embedded, "deploy_apple_moomoo.sh:embedded-python", "exec"), {})
    return load_config(config_path)


def test_deploy_options_snapshot_matches_backend_symbol_policy(monkeypatch, tmp_path: Path) -> None:
    values = {
        "MOOMOO_MARKET_SYMBOLS": "SPY,QQQ,AAPL",
        "INTRADAY_OPTIONS_SYMBOLS": "SPY,QQQ,IWM",
        "OPTIONS_TARGET_SYMBOLS": "SPY,QQQ,SMH,IGV",
        "OPTIONS_WATCHLIST_SYMBOLS": "AAPL,NVDA,SPCX,QQQ",
        "OPTIONS_WATCHLIST_EXCLUDED_SYMBOLS": "SPCX, IGV",
        "MOOMOO_HOST": "host.test",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("URUS_INTERNAL_API_URL", "http://192.168.65.1:7777")

    config = _render_deploy_config(monkeypatch, tmp_path)
    tasks = {task.name: task for task in config.tasks}

    assert tasks["options-snapshot-post-close"].operation == "options_snapshot"
    assert tasks["options-snapshot-post-close"].symbols == (
        "SPY",
        "QQQ",
        "SMH",
        "AAPL",
        "NVDA",
    )
    assert tasks["options-intraday"].symbols == ("SPY", "QQQ", "IWM")
    assert config.publisher["api_url"] == "http://192.168.65.1:7777"

from __future__ import annotations

from pathlib import Path

import pytest

from urus_moomoo.config import load_config


def test_load_config_normalizes_symbols(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """
[runtime]
timezone = "America/New_York"
output_dir = "data"

[[tasks]]
name = "daily"
enabled = true
schedule = "after-close"
symbols = [" aaa ", "AAA", "BBB"]
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.tasks[0].symbols == ("AAA", "BBB")


def test_load_config_rejects_duplicate_tasks(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """
[runtime]
timezone = "America/New_York"

[[tasks]]
name = "same"
schedule = "first"
symbols = ["AAA"]

[[tasks]]
name = "same"
schedule = "second"
symbols = ["BBB"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_config(path)


def test_load_config_keeps_task_definitions_local(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="task configuration"):
        load_config(tmp_path / "missing.local.toml")


def test_load_config_rejects_unknown_operation(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """
[runtime]
timezone = "UTC"

[[tasks]]
name = "synthetic"
schedule = "12:34"
operation = "unknown"
symbols = ["TEST_ASSET"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="operation"):
        load_config(path)


def test_load_config_accepts_eoflora_eod_operation(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """
[runtime]
timezone = "Asia/Tokyo"

[[tasks]]
name = "eoflora-eod"
schedule = "post-close"
operation = "capture_eod_options"
delay_seconds = 3600
symbols = ["SPY", "QQQ", "IWM"]
[tasks.params]
band_percent = 10
max_dte = 45
""",
        encoding="utf-8",
    )

    task = load_config(path).tasks[0]

    assert task.operation == "capture_eod_options"
    assert task.delay_seconds == 3600
    assert task.params == {"band_percent": 10, "max_dte": 45}


def test_load_config_keeps_private_publisher_settings(tmp_path: Path) -> None:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """
[runtime]
timezone = "Asia/Tokyo"
[runtime.publisher]
api_url = "http://urus:8000"
timeout_seconds = 12

[[tasks]]
name = "options-close"
schedule = "post-close"
operation = "options_snapshot"
symbols = ["SPY"]
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.publisher == {"api_url": "http://urus:8000", "timeout_seconds": 12}

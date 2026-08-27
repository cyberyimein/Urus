from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "register_decision_workflows.py"
SPEC = importlib.util.spec_from_file_location("register_decision_workflows", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_refuses_to_rebind_published_definition() -> None:
    with pytest.raises(RuntimeError, match="does not match published"):
        MODULE._assert_existing_definition_matches(
            filename="workflow.json",
            name="urus-review",
            version=1,
            existing={"status": "published", "definition_hash": "sha256:" + "a" * 64},
            validation_definition_hash="b" * 64,
        )


def test_release_accepts_same_definition_hash_with_sha_prefix() -> None:
    MODULE._assert_existing_definition_matches(
        filename="workflow.json",
        name="urus-review",
        version=1,
        existing={"status": "published", "definition_hash": "sha256:" + "a" * 64},
        validation_definition_hash="a" * 64,
    )

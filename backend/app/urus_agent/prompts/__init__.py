"""Versioned Urus Agent prompt resources."""

from pathlib import Path
from typing import Any

import yaml


def load_system_prompt() -> str:
    path = Path(__file__).resolve().parent / "agent.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    prompt = value.get("system_prompt") if isinstance(value, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("agent prompt is missing system_prompt")
    return prompt.strip()


def load_task_prompt(stage: str) -> str:
    """Load the versioned instructions for one Agent Invocation stage."""

    path = Path(__file__).resolve().parent / "tasks.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    prompts = value.get("task_prompts") if isinstance(value, dict) else None
    prompt = prompts.get(stage) if isinstance(prompts, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = prompts.get("equity") if isinstance(prompts, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"agent task prompt is missing stage: {stage}")
    return prompt.strip()


def load_agent_profile(decision_phase: str) -> dict[str, Any]:
    """Load one versioned daily-cycle Agent identity and instruction block."""

    path = Path(__file__).resolve().parent / "daily_agents.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles = value.get("agent_profiles") if isinstance(value, dict) else None
    profile = profiles.get(decision_phase) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ValueError(f"agent profile is missing decision phase: {decision_phase}")
    required = ("agent_name", "description", "forecast_horizon", "instructions")
    missing = [key for key in required if not isinstance(profile.get(key), str) or not profile[key].strip()]
    if missing:
        raise ValueError(
            f"agent profile {decision_phase} is missing fields: {', '.join(missing)}"
        )
    return {key: item.strip() if isinstance(item, str) else item for key, item in profile.items()}

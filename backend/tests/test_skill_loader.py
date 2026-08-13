from pathlib import Path

from app.urus_agent.skill_loader import SkillLoader


def test_default_skill_loader_finds_equity_skill() -> None:
    skill = SkillLoader().load("urus-equity-decision")

    assert skill.name == "urus-equity-decision"
    assert "Rank Urus Stage 4B" in skill.description
    assert "references/input-contract.md" in skill.instructions or "## Reference: input-contract.md" in skill.instructions


def test_skill_loader_can_use_bundled_root() -> None:
    bundled_root = Path(__file__).resolve().parents[1] / "app" / "urus_agent" / "skills"
    skill = SkillLoader(bundled_root).load("urus-options-decision")

    assert skill.name == "urus-options-decision"
    assert skill.content_hash

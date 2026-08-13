from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    instructions: str
    content_hash: str
    path: str


class SkillLoader:
    """Load versioned, context-only Skill documents for Urus Agent."""

    def __init__(self, root: Path | None = None) -> None:
        # Runtime skills are application resources, not Codex tooling.  A
        # caller can inject a temporary root for tests.
        self.root = root or Path(__file__).resolve().parent / "skills"

    def load(self, name: str) -> SkillDefinition:
        skill_dir = self.root / name
        path = skill_dir / "SKILL.md"
        if not path.exists():
            raise FileNotFoundError(f"skill_not_found:{name}")
        raw = path.read_text(encoding="utf-8")
        metadata, instructions = self._parse(raw, name)
        description = str(metadata.get("description") or "")
        if not description:
            raise ValueError(f"skill_invalid:{name}:description is required")
        references = []
        for reference in sorted(skill_dir.glob("references/*.md")):
            reference_text = reference.read_text(encoding="utf-8")
            references.append(f"\n\n## Reference: {reference.name}\n{reference_text.strip()}")
        full_instructions = instructions.strip() + "".join(references)
        digest_input = raw.encode("utf-8") + b"\n" + b"\n".join(item.encode("utf-8") for item in references)
        return SkillDefinition(
            name=str(metadata.get("name") or name),
            description=description,
            instructions=full_instructions,
            content_hash=hashlib.sha256(digest_input).hexdigest(),
            path=str(path),
        )

    def list(self) -> list[SkillDefinition]:
        if not self.root.exists():
            return []
        definitions = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                definitions.append(self.load(path.parent.name))
            except (OSError, ValueError, yaml.YAMLError):
                continue
        return definitions

    @staticmethod
    def _parse(raw: str, default_name: str) -> tuple[dict[str, Any], str]:
        if not raw.startswith("---"):
            return {"name": default_name}, raw
        parts = raw.split("---", 2)
        if len(parts) != 3:
            raise ValueError(f"skill_invalid:{default_name}:invalid frontmatter")
        metadata = yaml.safe_load(parts[1]) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"skill_invalid:{default_name}:frontmatter must be a mapping")
        return metadata, parts[2]

from __future__ import annotations

from pathlib import Path


DEFAULT_WORLDBOOK = """# Scene Orchestrator Worldbook

Edit this file to describe the shared world setting for AI roleplay.

Suggested sections:
- World premise:
- Timeline:
- Locations:
- Factions:
- Character relationships:
- Tone and boundaries:
- Ongoing plot hooks:
"""


class Worldbook:
    def __init__(
        self,
        plugin_dir: Path,
        path: str = "data/worldbook.md",
        enabled: bool = True,
        max_chars: int = 6000,
        auto_create: bool = True,
    ) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.relative_path = str(path or "data/worldbook.md").strip() or "data/worldbook.md"
        self.enabled = enabled
        self.max_chars = max(int(max_chars), 0)
        self.auto_create = auto_create

    @property
    def path(self) -> Path:
        candidate = Path(self.relative_path)
        if candidate.is_absolute():
            return candidate
        return self.plugin_dir / candidate

    def read(self) -> str:
        if not self.enabled:
            return ""

        path = self.path
        if not path.exists():
            if not self.auto_create:
                return ""
            self.create_template()

        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

        if self.max_chars <= 0:
            return ""
        if len(text) > self.max_chars:
            return text[: self.max_chars].rstrip()
        return text

    def create_template(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(DEFAULT_WORLDBOOK, encoding="utf-8")

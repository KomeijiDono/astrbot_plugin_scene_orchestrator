from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_origin_name(origin: str) -> str:
    text = str(origin or "").strip()
    if not text:
        return "unknown"

    normalized = SAFE_NAME_RE.sub("_", text).strip("._-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    if not normalized:
        normalized = "origin"
    return f"{normalized[:80]}_{digest}"


class StateScopeResolver:
    def __init__(self, plugin_dir: Path, scope: str = "origin") -> None:
        self.plugin_dir = Path(plugin_dir)
        self.scope = str(scope or "origin").strip().lower()

    def state_path_for_event(self, event: Any) -> Path:
        if self.scope != "origin":
            return self.plugin_dir / "data" / "world_states" / "global.json"

        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        return self.plugin_dir / "data" / "world_states" / f"{safe_origin_name(origin)}.json"

    def state_path_for_scene_key(self, scene_key: str) -> Path:
        return self.plugin_dir / "data" / "world_states" / f"{safe_origin_name(scene_key)}.json"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state_scope import safe_origin_name


def default_performance_state(scene_key: str) -> dict[str, Any]:
    return {
        "active": False,
        "scene_key": scene_key,
        "session_id": "",
        "scene": "",
        "summary": "",
        "round_limit": 0,
        "turn_index": 0,
        "participants": [],
        "plan": [],
        "transcript": [],
        "pause_prompt": "",
        "waiting_user_instruction": False,
    }


class PerformanceStateStore:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = Path(plugin_dir)

    def path_for_scene_key(self, scene_key: str) -> Path:
        return (
            self.plugin_dir
            / "data"
            / "performance_states"
            / f"{safe_origin_name(scene_key)}.json"
        )

    def load(self, scene_key: str) -> dict[str, Any]:
        path = self.path_for_scene_key(scene_key)
        if not path.exists():
            return default_performance_state(scene_key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_performance_state(scene_key)
        if not isinstance(data, dict):
            return default_performance_state(scene_key)
        state = default_performance_state(scene_key)
        state.update(data)
        state["scene_key"] = scene_key
        return state

    def save(self, scene_key: str, state: dict[str, Any]) -> None:
        path = self.path_for_scene_key(scene_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = default_performance_state(scene_key)
        payload.update(state)
        payload["scene_key"] = scene_key
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self, scene_key: str) -> dict[str, Any]:
        state = default_performance_state(scene_key)
        self.save(scene_key, state)
        return state

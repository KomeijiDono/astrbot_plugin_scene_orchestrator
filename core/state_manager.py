from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateManager:
    def __init__(self, state_path: str | Path, max_events: int = 100) -> None:
        self.path = Path(state_path)
        self.max_events = max(max_events, 1)

    def load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, json.JSONDecodeError):
            return self.default_state()

        if not isinstance(state, dict):
            return self.default_state()

        state.setdefault("scene", "default")
        state.setdefault("events", [])
        state.setdefault("mood", {})
        state.setdefault("current_speaker", "")

        if not isinstance(state["events"], list):
            state["events"] = []
        if not isinstance(state["mood"], dict):
            state["mood"] = {}

        return state

    def update(self, event: dict[str, Any]) -> dict[str, Any]:
        state = self.load()
        state["scene"] = event.get("scene") or state.get("scene") or "default"
        state["current_speaker"] = event.get("speaker") or state.get("current_speaker") or ""

        emotion = event.get("emotion")
        speaker = event.get("speaker")
        if speaker and emotion:
            state.setdefault("mood", {})[speaker] = emotion

        state.setdefault("events", []).append(event)
        state["events"] = state["events"][-self.max_events :]
        self.save(state)
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)

    def default_state(self) -> dict[str, Any]:
        return {
            "scene": "default",
            "events": [],
            "mood": {},
            "current_speaker": "",
        }

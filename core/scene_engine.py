from __future__ import annotations

from typing import Any


DECISION_FIELDS = (
    "scene",
    "speaker",
    "emotion",
    "intent",
    "world_event",
    "next_direction",
    "focus",
)


class SceneEngine:
    def __init__(self, enable_auto_scene: bool = True) -> None:
        self.enable_auto_scene = enable_auto_scene

    def normalize_decision(
        self,
        decision: dict[str, Any] | None,
        state: dict[str, Any],
        user_input: str,
    ) -> dict[str, Any]:
        raw = decision if isinstance(decision, dict) else {}
        normalized = {field: str(raw.get(field) or "").strip() for field in DECISION_FIELDS}

        if not normalized["scene"]:
            normalized["scene"] = str(state.get("scene") or "default")
        if not normalized["emotion"]:
            normalized["emotion"] = "neutral"
        if not normalized["intent"]:
            normalized["intent"] = "respond"
        if not normalized["world_event"]:
            normalized["world_event"] = user_input[:200]
        if not normalized["next_direction"]:
            normalized["next_direction"] = str(state.get("next_direction") or "").strip()

        normalized["auto_scene"] = self.enable_auto_scene
        return normalized

    def build_inject_context(self, state: dict[str, Any]) -> str:
        recent_events = state.get("events", [])
        if not isinstance(recent_events, list):
            recent_events = []

        recent = recent_events[-5:]
        lines = [
            "<scene_orchestrator_context>",
            f"scene: {state.get('scene', 'default')}",
            f"current_speaker: {state.get('current_speaker', '')}",
            f"next_direction: {state.get('next_direction', '')}",
            f"mood: {state.get('mood', {})}",
            "recent_events:",
        ]
        for item in recent:
            if isinstance(item, dict):
                lines.append(
                    "- "
                    + "; ".join(
                        f"{key}={value}"
                        for key, value in item.items()
                        if key
                        in {
                            "scene",
                            "speaker",
                            "emotion",
                            "intent",
                            "world_event",
                            "next_direction",
                        }
                    )
                )
            else:
                lines.append(f"- {item}")
        lines.append("</scene_orchestrator_context>")
        return "\n".join(lines)

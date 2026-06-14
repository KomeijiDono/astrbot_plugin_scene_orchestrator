from typing import Any


class RoleSelector:
    def __init__(self, default_role: str = "anon_default") -> None:
        self.default_role = default_role

    def select(self, decision: dict[str, Any], state: dict[str, Any]) -> str:
        speaker = str(decision.get("speaker") or "").strip()
        if speaker:
            return speaker

        focus = str(decision.get("focus") or "")
        if "爱音" in focus:
            return "chihaya_anon"

        scene = str(decision.get("scene") or "")
        intent = str(decision.get("intent") or "")
        if "冲突" in scene or "冲突" in intent:
            return "saki"

        current_speaker = str(state.get("current_speaker") or "").strip()
        if current_speaker:
            return current_speaker

        return self.default_role

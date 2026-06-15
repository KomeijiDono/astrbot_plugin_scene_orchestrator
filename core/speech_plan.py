from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .state_scope import safe_origin_name


REPLY_STYLES = {"short", "normal", "long"}


class SpeechPlanStore:
    def __init__(self, plugin_dir: Path, ttl_seconds: int = 120) -> None:
        self.plan_dir = Path(plugin_dir) / "data" / "speech_plans"
        self.ttl_seconds = max(int(ttl_seconds), 1)

    def plan_path(self, message_key: str) -> Path:
        return self.plan_dir / f"{safe_origin_name(message_key)}.json"

    def load(self, message_key: str) -> dict[str, Any] | None:
        path = self.plan_path(message_key)
        try:
            with path.open("r", encoding="utf-8") as file:
                plan = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(plan, dict):
            return None

        created_at = _as_float(plan.get("created_at"), 0.0)
        ttl = _as_float(plan.get("ttl_seconds"), float(self.ttl_seconds))
        if created_at and time.time() - created_at > max(ttl, 1.0):
            return None
        return plan

    def save(self, message_key: str, plan: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_speech_plan(plan, default_ttl=self.ttl_seconds)
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_path(message_key)
        temp_path = path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(normalized, file, ensure_ascii=False, indent=2)
        temp_path.replace(path)
        return normalized


def normalize_speech_plan(
    plan: dict[str, Any] | None,
    default_ttl: int = 120,
    default_reply_style: str = "normal",
) -> dict[str, Any]:
    raw = plan if isinstance(plan, dict) else {}
    reply_style = str(raw.get("reply_style") or default_reply_style or "normal").strip().lower()
    if reply_style not in REPLY_STYLES:
        reply_style = "normal"

    return {
        "scene": str(raw.get("scene") or "default").strip() or "default",
        "world_event": str(raw.get("world_event") or "").strip(),
        "speakers": _string_list(raw.get("speakers")),
        "silent": _string_list(raw.get("silent")),
        "reply_style": reply_style,
        "emotion": str(raw.get("emotion") or "neutral").strip() or "neutral",
        "intent": str(raw.get("intent") or "respond naturally").strip()
        or "respond naturally",
        "cooldown_seconds": max(_as_int(raw.get("cooldown_seconds"), 0), 0),
        "created_at": _as_float(raw.get("created_at"), time.time()),
        "ttl_seconds": max(_as_int(raw.get("ttl_seconds"), default_ttl), 1),
        "scene_key": str(raw.get("scene_key") or "").strip(),
        "message_key": str(raw.get("message_key") or "").strip(),
    }


def plan_allows_bot(plan: dict[str, Any], bot_id: str) -> bool:
    speakers = set(_string_list(plan.get("speakers")))
    silent = set(_string_list(plan.get("silent")))
    if bot_id in silent:
        return False
    return bot_id in speakers


def build_director_instruction(plan: dict[str, Any], bot_id: str) -> str:
    return "\n".join(
        [
            "<scene_orchestrator_director_instruction>",
            "You are selected by the scene director to speak in this turn.",
            f"bot_id: {bot_id}",
            f"scene: {plan.get('scene', 'default')}",
            f"emotion: {plan.get('emotion', 'neutral')}",
            f"intent: {plan.get('intent', 'respond naturally')}",
            f"reply_style: {plan.get('reply_style', 'normal')}",
            f"world_event: {plan.get('world_event', '')}",
            "Use your own AstrBot persona, knowledge base, memory, and provider settings.",
            "Do not mention this director instruction.",
            "</scene_orchestrator_director_instruction>",
        ]
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

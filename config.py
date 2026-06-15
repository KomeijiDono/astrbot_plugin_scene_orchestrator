import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DialogueTarget:
    bot_id: str = ""
    mention_id: str = ""
    display_name: str = ""


@dataclass(frozen=True)
class SceneOrchestratorConfig:
    enabled: bool = True
    mode: str = "takeover"
    debug_mode: bool = False
    enable_auto_scene: bool = True
    max_events: int = 100
    strict_json: bool = True
    default_role: str = "anon_default"
    state_scope: str = "origin"
    inherit_astrbot_persona: bool = True
    debug_persona_resolution: bool = False
    speech_plan_ttl_seconds: int = 120
    default_reply_style: str = "normal"
    worldbook_enabled: bool = True
    worldbook_path: str = "data/worldbook.md"
    worldbook_max_chars: int = 6000
    worldbook_auto_create: bool = True
    dialogue_enabled: bool = False
    dialogue_handoff_delay_seconds: int = 2
    dialogue_cooldown_seconds: int = 10
    dialogue_targets: dict[str, DialogueTarget] | None = None


def _get_group_value(
    config: Any,
    group: str,
    key: str,
    default: Any,
    legacy_key: str | None = None,
) -> Any:
    if config is None:
        return default

    getter = getattr(config, "get", None)
    if getter is None:
        return default

    group_config = getter(group, {})
    if isinstance(group_config, dict) and key in group_config:
        return group_config.get(key, default)

    if legacy_key is not None:
        legacy_value = getter(legacy_key, None)
        if legacy_value is not None:
            return legacy_value

    return getter(key, default)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on", "enable", "enabled"}:
            return True
        if lowered in {"false", "0", "no", "off", "disable", "disabled"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        return max(number, minimum)
    return number


def _as_dialogue_targets(value: Any) -> dict[str, DialogueTarget]:
    if not isinstance(value, dict):
        return {}

    targets: dict[str, DialogueTarget] = {}
    for raw_key, raw_target in value.items():
        key = str(raw_key or "").strip()
        if not key or not isinstance(raw_target, dict):
            continue
        target = DialogueTarget(
            bot_id=str(raw_target.get("bot_id") or "").strip(),
            mention_id=str(raw_target.get("mention_id") or "").strip(),
            display_name=str(raw_target.get("display_name") or "").strip(),
        )
        if target.mention_id:
            targets[key] = target
    return targets


def _as_json_dialogue_targets(value: Any) -> dict[str, DialogueTarget]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return _as_dialogue_targets(parsed)


def load_config(config: Any) -> SceneOrchestratorConfig:
    mode = str(_get_group_value(config, "general", "mode", "takeover") or "takeover")
    mode = mode.strip().lower()
    if mode not in {"takeover", "inject", "director_gate"}:
        mode = "takeover"

    default_role = str(
        _get_group_value(config, "roles", "default_role", "anon_default")
        or "anon_default"
    )

    dialogue_targets = _as_dialogue_targets(
        _get_group_value(config, "dialogue", "targets", {})
    )
    dialogue_targets.update(
        _as_json_dialogue_targets(
            _get_group_value(config, "dialogue", "targets_json", "")
        )
    )

    return SceneOrchestratorConfig(
        enabled=_as_bool(_get_group_value(config, "general", "enabled", True), True),
        mode=mode,
        debug_mode=_as_bool(
            _get_group_value(config, "general", "debug_mode", False),
            False,
        ),
        enable_auto_scene=_as_bool(
            _get_group_value(config, "scene", "enable_auto_scene", True),
            True,
        ),
        max_events=_as_int(
            _get_group_value(config, "scene", "max_events", 100),
            100,
            minimum=1,
        ),
        strict_json=_as_bool(
            _get_group_value(config, "llm", "strict_json", True),
            True,
        ),
        default_role=default_role,
        state_scope=str(
            _get_group_value(config, "state", "scope", "origin") or "origin"
        ).strip().lower()
        or "origin",
        inherit_astrbot_persona=_as_bool(
            _get_group_value(config, "persona", "inherit_astrbot_persona", True),
            True,
        ),
        debug_persona_resolution=_as_bool(
            _get_group_value(config, "persona", "debug_persona_resolution", False),
            False,
        ),
        speech_plan_ttl_seconds=_as_int(
            _get_group_value(config, "director", "speech_plan_ttl_seconds", 120),
            120,
            minimum=1,
        ),
        default_reply_style=str(
            _get_group_value(config, "director", "default_reply_style", "normal")
            or "normal"
        ).strip().lower()
        or "normal",
        worldbook_enabled=_as_bool(
            _get_group_value(config, "worldbook", "enabled", True),
            True,
        ),
        worldbook_path=str(
            _get_group_value(config, "worldbook", "path", "data/worldbook.md")
            or "data/worldbook.md"
        ).strip()
        or "data/worldbook.md",
        worldbook_max_chars=_as_int(
            _get_group_value(config, "worldbook", "max_chars", 6000),
            6000,
            minimum=0,
        ),
        worldbook_auto_create=_as_bool(
            _get_group_value(config, "worldbook", "auto_create", True),
            True,
        ),
        dialogue_enabled=_as_bool(
            _get_group_value(config, "dialogue", "enabled", False),
            False,
        ),
        dialogue_handoff_delay_seconds=_as_int(
            _get_group_value(config, "dialogue", "handoff_delay_seconds", 2),
            2,
            minimum=0,
        ),
        dialogue_cooldown_seconds=_as_int(
            _get_group_value(config, "dialogue", "cooldown_seconds", 10),
            10,
            minimum=0,
        ),
        dialogue_targets=dialogue_targets,
    )

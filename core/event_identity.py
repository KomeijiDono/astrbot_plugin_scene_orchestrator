from __future__ import annotations

import hashlib
from typing import Any


def _call_str(event: Any, method_name: str) -> str:
    method = getattr(event, method_name, None)
    if not callable(method):
        return ""
    try:
        return str(method() or "").strip()
    except Exception:
        return ""


def _attr_str(value: Any, *names: str) -> str:
    for name in names:
        item = getattr(value, name, None)
        if item:
            return str(item).strip()
    return ""


def event_platform_id(event: Any) -> str:
    return _call_str(event, "get_platform_id") or _call_str(event, "get_platform_name")


def event_self_id(event: Any) -> str:
    direct = _call_str(event, "get_self_id") or _call_str(event, "get_bot_id")
    if direct:
        return direct

    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if isinstance(raw_message, dict):
        self_id = str(raw_message.get("self_id") or "").strip()
        if self_id:
            return self_id
    return _attr_str(message_obj, "self_id")


def event_sender_id(event: Any) -> str:
    return _call_str(event, "get_sender_id")


def event_group_id(event: Any) -> str:
    return _call_str(event, "get_group_id")


def event_message_id(event: Any) -> str:
    for method_name in ("get_message_id", "get_msg_id"):
        value = _call_str(event, method_name)
        if value:
            return value

    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if isinstance(raw_message, dict):
        for key in ("message_id", "msg_id", "id"):
            value = str(raw_message.get(key) or "").strip()
            if value:
                return value

    return _attr_str(message_obj, "message_id", "msg_id", "id")


def event_text(event: Any) -> str:
    return str(getattr(event, "message_str", "") or "").strip()


def text_hash(text: str) -> str:
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:16]


def scene_key_for_event(event: Any) -> str:
    platform = event_platform_id(event) or "platform"
    group_id = event_group_id(event)
    if group_id:
        return f"group:{platform}:{group_id}"

    self_id = event_self_id(event) or "bot"
    sender_id = event_sender_id(event) or "sender"
    return f"private:{platform}:{self_id}:{sender_id}"


def message_key_for_event(event: Any) -> str:
    scene_key = scene_key_for_event(event)
    message_id = event_message_id(event)
    if message_id:
        return f"{scene_key}:message:{message_id}"
    return f"{scene_key}:text:{text_hash(event_text(event))}"


def bot_id_for_event(event: Any) -> str:
    platform = event_platform_id(event) or "platform"
    self_id = event_self_id(event)
    if self_id:
        return f"{platform}:{self_id}"
    origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
    return origin or platform

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def load_prompt(plugin_dir: Path, relative_path: str) -> str:
    path = plugin_dir / relative_path
    return path.read_text(encoding="utf-8")


def parse_json_object(text: str, strict: bool = True) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        if strict:
            raise ValueError("empty LLM response")
        return {}

    candidates = [raw]
    block_match = JSON_BLOCK_RE.search(raw)
    if block_match:
        candidates.insert(0, block_match.group(1).strip())

    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    if strict:
        raise ValueError("LLM response does not contain a JSON object")
    return {}


class AstrBotLLMClient:
    def __init__(self, context: Any, plugin_dir: Path) -> None:
        self.context = context
        self.plugin_dir = plugin_dir

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        event: Any | None = None,
        provider_id: str = "",
        model: str = "",
    ) -> str:
        provider = self._get_provider(event, provider_id=provider_id)
        if provider is None:
            raise RuntimeError("No active AstrBot LLM provider is available")

        kwargs = {"prompt": prompt, "system_prompt": system_prompt}
        if model:
            kwargs["model"] = model
        response = await provider.text_chat(**kwargs)
        return str(getattr(response, "completion_text", "") or "")

    def _get_provider(self, event: Any | None = None, provider_id: str = "") -> Any:
        provider_id = str(provider_id or "").strip()
        if provider_id:
            get_provider_by_id = getattr(self.context, "get_provider_by_id", None)
            if callable(get_provider_by_id):
                provider = get_provider_by_id(provider_id)
                if provider is not None:
                    return provider
            provider_manager = getattr(self.context, "provider_manager", None)
            manager_getter = getattr(provider_manager, "get_provider_by_id", None)
            if callable(manager_getter):
                provider = manager_getter(provider_id)
                if provider is not None:
                    return provider

        umo = getattr(event, "unified_msg_origin", None)
        get_using_provider = getattr(self.context, "get_using_provider", None)
        if callable(get_using_provider):
            try:
                return get_using_provider(umo=umo)
            except TypeError:
                return get_using_provider()
        provider_manager = getattr(self.context, "provider_manager", None)
        if provider_manager is not None:
            manager_getter = getattr(provider_manager, "get_using_provider", None)
            if callable(manager_getter):
                try:
                    return manager_getter(umo=umo)
                except TypeError:
                    return manager_getter()
        return None

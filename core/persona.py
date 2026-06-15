from __future__ import annotations

from dataclasses import dataclass
from typing import Any


NONE_PERSONA_ID = "[%None]"


@dataclass(frozen=True)
class PersonaContext:
    persona_id: str = ""
    name: str = ""
    prompt: str = ""
    source: str = "none"

    @property
    def has_persona(self) -> bool:
        return bool(self.persona_id or self.name or self.prompt)

    def format_for_prompt(self) -> str:
        if not self.has_persona:
            return ""
        lines = ["AstrBot current persona:"]
        if self.name or self.persona_id:
            lines.append(f"- name: {self.name or self.persona_id}")
        if self.prompt:
            lines.append("- persona prompt:")
            lines.append(self.prompt)
        return "\n".join(lines)


class PersonaResolver:
    def __init__(self, context: Any) -> None:
        self.context = context

    async def resolve(self, event: Any) -> PersonaContext:
        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not origin:
            return PersonaContext()

        persona_id = await self._resolve_persona_id(origin)
        if persona_id == NONE_PERSONA_ID:
            return PersonaContext(source="explicit_none")
        if not persona_id:
            return PersonaContext()

        return self._build_persona_context(persona_id)

    async def _resolve_persona_id(self, origin: str) -> str:
        session_persona_id = await self._get_session_service_persona_id(origin)
        if session_persona_id:
            return session_persona_id

        conversation_persona_id = await self._get_conversation_persona_id(origin)
        if conversation_persona_id:
            return conversation_persona_id

        default_persona_id = await self._get_default_persona_id(origin)
        if default_persona_id:
            return default_persona_id

        return self._get_config_default_persona_id(origin)

    async def _get_session_service_persona_id(self, origin: str) -> str:
        try:
            from astrbot.api import sp

            payload = await sp.get_async(
                scope="umo",
                scope_id=origin,
                key="session_service_config",
                default={},
            )
        except Exception:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("persona_id") or "").strip()
        return ""

    async def _get_conversation_persona_id(self, origin: str) -> str:
        manager = getattr(self.context, "conversation_manager", None)
        if manager is None:
            return ""

        try:
            conversation_id = await manager.get_curr_conversation_id(origin)
            if not conversation_id:
                return ""
            conversation = await manager.get_conversation(origin, conversation_id)
        except Exception:
            return ""

        return str(getattr(conversation, "persona_id", "") or "").strip()

    async def _get_default_persona_id(self, origin: str) -> str:
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_default_persona_v3", None)
        if not callable(getter):
            return ""

        try:
            default_persona = await getter(umo=origin)
        except TypeError:
            try:
                default_persona = await getter()
            except Exception:
                return ""
        except Exception:
            return ""

        if isinstance(default_persona, dict):
            return str(default_persona.get("name") or "").strip()
        return str(getattr(default_persona, "name", "") or "").strip()

    def _get_config_default_persona_id(self, origin: str) -> str:
        getter = getattr(self.context, "get_config", None)
        if not callable(getter):
            return ""

        try:
            config = getter(umo=origin)
        except TypeError:
            try:
                config = getter()
            except Exception:
                return ""
        except Exception:
            return ""

        if not isinstance(config, dict):
            return ""
        provider_settings = config.get("provider_settings", {})
        if not isinstance(provider_settings, dict):
            return ""
        return str(provider_settings.get("default_personality") or "").strip()

    def _build_persona_context(self, persona_id: str) -> PersonaContext:
        persona = self._find_persona(persona_id)
        if persona is None:
            return PersonaContext(persona_id=persona_id, name=persona_id, source="id_only")

        name = self._read_first_attr(persona, ("name", "id", "persona_id")) or persona_id
        prompt = self._read_first_attr(
            persona,
            (
                "prompt",
                "persona",
                "personality",
                "system_prompt",
                "content",
                "description",
            ),
        )
        if not prompt and isinstance(persona, dict):
            prompt = self._read_first_key(
                persona,
                (
                    "prompt",
                    "persona",
                    "personality",
                    "system_prompt",
                    "content",
                    "description",
                ),
            )
        return PersonaContext(
            persona_id=persona_id,
            name=str(name or persona_id),
            prompt=str(prompt or ""),
            source="persona_manager",
        )

    def _find_persona(self, persona_id: str) -> Any | None:
        manager = getattr(self.context, "persona_manager", None)
        personas = getattr(manager, "personas_v3", None)
        if not personas:
            return None

        if isinstance(personas, dict):
            return personas.get(persona_id)

        for persona in personas:
            name = self._read_first_attr(persona, ("name", "id", "persona_id"))
            if not name and isinstance(persona, dict):
                name = self._read_first_key(persona, ("name", "id", "persona_id"))
            if str(name or "").strip() == persona_id:
                return persona
        return None

    @staticmethod
    def _read_first_attr(value: Any, names: tuple[str, ...]) -> str:
        for name in names:
            item = getattr(value, name, None)
            if item:
                return str(item)
        return ""

    @staticmethod
    def _read_first_key(value: dict[str, Any], names: tuple[str, ...]) -> str:
        for name in names:
            item = value.get(name)
            if item:
                return str(item)
        return ""

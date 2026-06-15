from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import SceneOrchestratorConfig
from ..utils.llm import AstrBotLLMClient, load_prompt, parse_json_object
from .event_identity import (
    bot_id_for_event,
    event_text,
    message_key_for_event,
    scene_key_for_event,
)
from .persona import PersonaContext, PersonaResolver
from .role_selector import RoleSelector
from .scene_engine import SceneEngine
from .speech_plan import (
    SpeechPlanStore,
    build_director_instruction,
    normalize_speech_plan,
    plan_allows_bot,
)
from .state_manager import StateManager
from .state_scope import StateScopeResolver
from .worldbook import Worldbook


class Orchestrator:
    def __init__(
        self,
        context: Any,
        config: SceneOrchestratorConfig,
        plugin_dir: Path,
    ) -> None:
        self.context = context
        self.config = config
        self.plugin_dir = plugin_dir
        self.state_scope = StateScopeResolver(plugin_dir, scope=config.state_scope)
        self.scene_engine = SceneEngine(enable_auto_scene=config.enable_auto_scene)
        self.role_selector = RoleSelector(default_role=config.default_role)
        self.persona_resolver = PersonaResolver(context)
        self.plan_store = SpeechPlanStore(
            plugin_dir,
            ttl_seconds=config.speech_plan_ttl_seconds,
        )
        self.worldbook = Worldbook(
            plugin_dir,
            path=config.worldbook_path,
            enabled=config.worldbook_enabled,
            max_chars=config.worldbook_max_chars,
            auto_create=config.worldbook_auto_create,
        )
        self.llm = AstrBotLLMClient(context, plugin_dir)

    async def process(self, event: Any) -> dict[str, Any]:
        user_input = str(getattr(event, "message_str", "") or "").strip()
        state_manager = self._state_manager_for_event(event)
        state = state_manager.load()
        persona = await self._resolve_persona(event)
        decision = await self.decide_scene(user_input, state, event, persona)
        role = self.role_selector.select(decision, state)
        decision["speaker"] = role
        updated_state = state_manager.update(decision)
        reply = await self.generate_role_reply(
            role,
            decision,
            updated_state,
            user_input,
            event,
            persona,
        )
        return {
            "should_reply": bool(reply),
            "reply": reply,
            "decision": decision,
            "state": updated_state,
            "persona": persona,
        }

    async def decide_scene(
        self,
        user_input: str,
        state: dict[str, Any],
        event: Any,
        persona: PersonaContext,
    ) -> dict[str, Any]:
        system_prompt = self._with_persona(
            load_prompt(self.plugin_dir, "prompts/director_prompt.txt"),
            persona,
        )
        prompt = "\n".join(
            [
                "Current world state:",
                json.dumps(state, ensure_ascii=False, indent=2),
                "",
                "User message:",
                user_input,
            ]
        )

        try:
            text = await self.llm.complete(prompt=prompt, system_prompt=system_prompt, event=event)
            raw_decision = parse_json_object(text, strict=self.config.strict_json)
        except Exception:
            raw_decision = {}

        return self.scene_engine.normalize_decision(raw_decision, state, user_input)

    async def generate_role_reply(
        self,
        role: str,
        decision: dict[str, Any],
        state: dict[str, Any],
        user_input: str,
        event: Any,
        persona: PersonaContext,
    ) -> str:
        system_prompt = self._with_persona(
            load_prompt(self.plugin_dir, "prompts/scene_prompt.txt"),
            persona,
        )
        prompt = "\n".join(
            [
                f"You are speaking as role: {role}",
                "Director decision:",
                json.dumps(decision, ensure_ascii=False, indent=2),
                "",
                "Current world state:",
                json.dumps(state, ensure_ascii=False, indent=2),
                "",
                "User message:",
                user_input,
                "",
                "Write the next in-character reply only.",
            ]
        )

        try:
            return (await self.llm.complete(prompt=prompt, system_prompt=system_prompt, event=event)).strip()
        except Exception:
            return self._fallback_reply(role, decision)

    def build_inject_context(self, event: Any) -> str:
        state = self._state_manager_for_event(event).load()
        return self.scene_engine.build_inject_context(state)

    async def director_gate(self, event: Any) -> dict[str, Any]:
        bot_id = bot_id_for_event(event)
        scene_key = scene_key_for_event(event)
        message_key = message_key_for_event(event)
        state_manager = self._state_manager_for_scene_key(scene_key)
        state = state_manager.load()
        state = self._record_known_bot(state_manager, state, bot_id)

        plan = self.plan_store.load(message_key)
        created = False
        if plan is None:
            plan = await self._create_speech_plan(event, state, scene_key, message_key, bot_id)
            plan = self.plan_store.save(message_key, plan)
            self._record_plan_event(state_manager, plan)
            created = True

        allowed = plan_allows_bot(plan, bot_id)
        return {
            "allow_reply": allowed,
            "bot_id": bot_id,
            "scene_key": scene_key,
            "message_key": message_key,
            "plan": plan,
            "created": created,
        }

    async def build_director_gate_instruction(self, event: Any) -> str:
        bot_id = bot_id_for_event(event)
        plan = self.plan_store.load(message_key_for_event(event))
        if not plan or not plan_allows_bot(plan, bot_id):
            return ""
        return build_director_instruction(plan, bot_id)

    def _fallback_reply(self, role: str, decision: dict[str, Any]) -> str:
        emotion = decision.get("emotion") or "neutral"
        intent = decision.get("intent") or "respond"
        return f"[{role} | {emotion}] {intent}"

    def _state_manager_for_event(self, event: Any) -> StateManager:
        return StateManager(
            self.state_scope.state_path_for_event(event),
            max_events=self.config.max_events,
        )

    def _state_manager_for_scene_key(self, scene_key: str) -> StateManager:
        return StateManager(
            self.state_scope.state_path_for_scene_key(scene_key),
            max_events=self.config.max_events,
        )

    async def _resolve_persona(self, event: Any) -> PersonaContext:
        if not self.config.inherit_astrbot_persona:
            return PersonaContext()
        return await self.persona_resolver.resolve(event)

    @staticmethod
    def _with_persona(system_prompt: str, persona: PersonaContext) -> str:
        persona_prompt = persona.format_for_prompt()
        if not persona_prompt:
            return system_prompt
        return f"{system_prompt.rstrip()}\n\n{persona_prompt}"

    def _record_known_bot(
        self,
        state_manager: StateManager,
        state: dict[str, Any],
        bot_id: str,
    ) -> dict[str, Any]:
        known_bots = state.get("known_bots", [])
        if not isinstance(known_bots, list):
            known_bots = []
        if bot_id and bot_id not in known_bots:
            known_bots.append(bot_id)
            state["known_bots"] = known_bots
            state_manager.save(state)
        return state

    def _record_plan_event(self, state_manager: StateManager, plan: dict[str, Any]) -> None:
        event = {
            "scene": plan.get("scene") or "default",
            "speaker": ", ".join(plan.get("speakers", [])),
            "emotion": plan.get("emotion") or "neutral",
            "intent": plan.get("intent") or "respond naturally",
            "world_event": plan.get("world_event") or "",
            "focus": "director_gate",
            "auto_scene": self.config.enable_auto_scene,
            "created_at": plan.get("created_at") or time.time(),
        }
        state_manager.update(event)

    async def _create_speech_plan(
        self,
        event: Any,
        state: dict[str, Any],
        scene_key: str,
        message_key: str,
        current_bot_id: str,
    ) -> dict[str, Any]:
        known_bots = state.get("known_bots", [])
        if not isinstance(known_bots, list):
            known_bots = []
        if current_bot_id and current_bot_id not in known_bots:
            known_bots = [*known_bots, current_bot_id]

        system_prompt = load_prompt(self.plugin_dir, "prompts/speech_plan_prompt.txt")
        worldbook = self.worldbook.read()
        prompt = "\n".join(
            [
                "Current chat scene key:",
                scene_key,
                "",
                "Current bot id:",
                current_bot_id,
                "",
                "Known bot ids in this scene:",
                json.dumps(known_bots, ensure_ascii=False, indent=2),
                "",
                "Current world state:",
                json.dumps(state, ensure_ascii=False, indent=2),
                "",
                "Shared editable worldbook:",
                worldbook or "(empty)",
                "",
                "User message:",
                event_text(event),
            ]
        )

        try:
            text = await self.llm.complete(prompt=prompt, system_prompt=system_prompt, event=event)
            raw_plan = parse_json_object(text, strict=self.config.strict_json)
        except Exception:
            raw_plan = {}

        if not raw_plan.get("speakers"):
            raw_plan["speakers"] = [current_bot_id]
        raw_plan.setdefault("silent", [bot for bot in known_bots if bot != current_bot_id])
        raw_plan.setdefault("world_event", event_text(event)[:200])
        raw_plan.setdefault("scene_key", scene_key)
        raw_plan.setdefault("message_key", message_key)
        raw_plan.setdefault("reply_style", self.config.default_reply_style)
        raw_plan.setdefault("ttl_seconds", self.config.speech_plan_ttl_seconds)
        raw_plan.setdefault("created_at", time.time())
        return normalize_speech_plan(
            raw_plan,
            default_ttl=self.config.speech_plan_ttl_seconds,
            default_reply_style=self.config.default_reply_style,
        )

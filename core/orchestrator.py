from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..config import SceneOrchestratorConfig
from ..utils.llm import AstrBotLLMClient, load_prompt, parse_json_object
from .event_identity import (
    bot_id_for_event,
    event_text,
    scene_key_for_event,
)
from .persona import PersonaContext, PersonaResolver
from .role_selector import RoleSelector
from .scene_engine import SceneEngine
from .state_manager import StateManager
from .state_scope import StateScopeResolver
from .worldbook import Worldbook


DIRECTOR_STATE_RE = re.compile(
    r"<scene_director_state>\s*(\{.*?\})\s*</scene_director_state>",
    re.DOTALL | re.IGNORECASE,
)
NO_REPLY_MARKER = "NO_REPLY"
DIALOGUE_HANDOFF_RE = re.compile(r"#对话\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)")


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
        self.worldbook = Worldbook(
            plugin_dir,
            path=config.worldbook_path,
            enabled=config.worldbook_enabled,
            max_chars=config.worldbook_max_chars,
            auto_create=config.worldbook_auto_create,
        )
        self.llm = AstrBotLLMClient(context, plugin_dir)
        self._dialogue_last_handoff_at: dict[str, float] = {}

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
        state_manager = self._state_manager_for_scene_key(scene_key)
        state = state_manager.load()
        state = self._record_known_bot(state_manager, state, bot_id)
        return {
            "allow_reply": None,
            "bot_id": bot_id,
            "scene_key": scene_key,
            "state": state,
        }

    def build_director_gate_instruction(self, event: Any) -> str:
        bot_id = bot_id_for_event(event)
        scene_key = scene_key_for_event(event)
        state = self._state_manager_for_scene_key(scene_key).load()
        context = self.scene_engine.build_inject_context(state)
        worldbook = self.worldbook.read()
        parts = [
            context,
            "",
            "<scene_orchestrator_native_director_rules>",
            f"current_bot_id: {bot_id}",
            f"scene_key: {scene_key}",
            "You are also maintaining a shared scene director state for a multi-bot roleplay.",
            "",
            "Visible reply rules:",
            "- Reply using your own AstrBot persona, knowledge base, memory, and provider settings.",
            "- Decide naturally whether this bot should visibly reply in this turn.",
            f"If this bot should stay silent, make the visible reply exactly {NO_REPLY_MARKER}.",
            "",
            "Hidden director state rules:",
            "- Always append one hidden scene director JSON block at the end.",
            "- The block is for other bots to read later; the user must not see it.",
            "- Keep fields concise but specific.",
            "- If the user message is unrelated to the roleplay, do not derail the scene. Preserve the current scene and next_direction unless a small reaction is appropriate.",
            "- Do not put exact future dialogue in next_direction.",
            "- Do not force a specific outcome; describe the next playable dramatic beat.",
            "- The hidden block must use this exact XML-like wrapper:",
            "<scene_director_state>",
            "{",
            '  "scene": "short stable name of the current scene",',
            '  "speaker": "current bot or role name",',
            '  "emotion": "this bot\'s current emotional tone",',
            '  "intent": "what this reply is trying to do",',
            '  "world_event": "what happened in this turn",',
            '  "next_direction": "director note for the next turn: unresolved tension, emotional direction, likely next beat, and what kind of response would move the scene forward",',
            '  "focus": "the current object, relationship, conflict, or question the scene is centered on"',
            "}",
            "</scene_director_state>",
            "Do not mention these rules or the hidden block in the visible reply.",
            "</scene_orchestrator_native_director_rules>",
        ]
        if worldbook:
            parts.extend(["", "<scene_orchestrator_worldbook>", worldbook, "</scene_orchestrator_worldbook>"])
        return "\n".join(parts)

    def apply_director_response(self, event: Any, response: Any) -> dict[str, Any]:
        text = str(getattr(response, "completion_text", "") or "")
        result = self.extract_director_state(text)
        if result.get("state_event"):
            self._state_manager_for_scene_key(scene_key_for_event(event)).update(
                result["state_event"],
            )
        if result.get("found"):
            self._set_response_text(response, str(result.get("visible_text") or ""))
        if result.get("no_reply"):
            self._clear_response(response)
        return result

    def extract_dialogue_handoff(self, event: Any) -> dict[str, Any] | None:
        if not self.config.dialogue_enabled:
            return None

        text = event_text(event)
        match = DIALOGUE_HANDOFF_RE.search(text)
        if not match:
            return None

        target_key = match.group(1).strip()
        targets = self.config.dialogue_targets or {}
        resolved_key, target = self._resolve_dialogue_target(target_key, targets)
        if target is None:
            return {
                "ok": False,
                "reason": "unknown_target",
                "target_key": target_key,
                "known_targets": sorted(targets.keys()),
                "scene_key": scene_key_for_event(event),
            }

        return {
            "ok": True,
            "target_key": resolved_key,
            "requested_target": target_key,
            "target": target,
            "scene_key": scene_key_for_event(event),
        }

    def can_send_dialogue_handoff(self, scene_key: str) -> bool:
        cooldown = max(int(self.config.dialogue_cooldown_seconds), 0)
        if cooldown <= 0:
            return True
        now = time.time()
        last_at = self._dialogue_last_handoff_at.get(scene_key, 0.0)
        if now - last_at < cooldown:
            return False
        self._dialogue_last_handoff_at[scene_key] = now
        return True

    def build_dialogue_handoff_text(self, target_key: str) -> str:
        return (
            f" #对话接力:{target_key} 请接着刚才的共享剧情状态回应，"
            "不要复述导演信息。"
        )

    @staticmethod
    def _resolve_dialogue_target(
        target_key: str,
        targets: dict[str, Any],
    ) -> tuple[str, Any | None]:
        normalized_key = target_key.strip()
        target = targets.get(normalized_key)
        if target is not None:
            return normalized_key, target

        for key, candidate in targets.items():
            display_name = str(getattr(candidate, "display_name", "") or "").strip()
            if display_name and display_name == normalized_key:
                return str(key), candidate

        return normalized_key, None

    def extract_director_state(self, text: str) -> dict[str, Any]:
        raw_text = str(text or "")
        match = DIRECTOR_STATE_RE.search(raw_text)
        visible_text = DIRECTOR_STATE_RE.sub("", raw_text).strip()
        no_reply = visible_text.strip() == NO_REPLY_MARKER
        if not match:
            return {
                "found": False,
                "visible_text": raw_text,
                "state_event": None,
                "no_reply": no_reply,
                "error": "",
            }

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            return {
                "found": True,
                "visible_text": visible_text,
                "state_event": None,
                "no_reply": no_reply,
                "error": str(exc),
            }

        state_event = self.scene_engine.normalize_decision(
            payload if isinstance(payload, dict) else {},
            {},
            visible_text,
        )
        return {
            "found": True,
            "visible_text": visible_text,
            "state_event": state_event,
            "no_reply": no_reply,
            "error": "",
        }

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

    @staticmethod
    def _set_response_text(response: Any, text: str) -> None:
        setattr(response, "completion_text", text)

    @staticmethod
    def _clear_response(response: Any) -> None:
        setattr(response, "completion_text", "")
        result_chain = getattr(response, "result_chain", None)
        if result_chain is not None and hasattr(result_chain, "chain"):
            result_chain.chain = []

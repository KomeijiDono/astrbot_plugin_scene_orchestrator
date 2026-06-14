from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import SceneOrchestratorConfig
from ..utils.llm import AstrBotLLMClient, load_prompt, parse_json_object
from .role_selector import RoleSelector
from .scene_engine import SceneEngine
from .state_manager import StateManager


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
        self.state_manager = StateManager(
            plugin_dir / "data" / "world_state.json",
            max_events=config.max_events,
        )
        self.scene_engine = SceneEngine(enable_auto_scene=config.enable_auto_scene)
        self.role_selector = RoleSelector(default_role=config.default_role)
        self.llm = AstrBotLLMClient(context, plugin_dir)

    async def process(self, event: Any) -> dict[str, Any]:
        user_input = str(getattr(event, "message_str", "") or "").strip()
        state = self.state_manager.load()
        decision = await self.decide_scene(user_input, state, event)
        role = self.role_selector.select(decision, state)
        decision["speaker"] = role
        updated_state = self.state_manager.update(decision)
        reply = await self.generate_role_reply(role, decision, updated_state, user_input, event)
        return {
            "should_reply": bool(reply),
            "reply": reply,
            "decision": decision,
            "state": updated_state,
        }

    async def decide_scene(
        self,
        user_input: str,
        state: dict[str, Any],
        event: Any,
    ) -> dict[str, Any]:
        system_prompt = load_prompt(self.plugin_dir, "prompts/director_prompt.txt")
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
    ) -> str:
        system_prompt = load_prompt(self.plugin_dir, "prompts/scene_prompt.txt")
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

    def build_inject_context(self) -> str:
        state = self.state_manager.load()
        return self.scene_engine.build_inject_context(state)

    def _fallback_reply(self, role: str, decision: dict[str, Any]) -> str:
        emotion = decision.get("emotion") or "neutral"
        intent = decision.get("intent") or "respond"
        return f"[{role} | {emotion}] {intent}"

from __future__ import annotations

import json
import re
import time
import uuid
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
from .performance_state import PerformanceStateStore
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
PERFORMANCE_START_RE = re.compile(r"#开演\b")
PERFORMANCE_CONTINUE_RE = re.compile(r"#继续\b")
PERFORMANCE_PAUSE_RE = re.compile(r"#暂停\b")
PERFORMANCE_RESET_RE = re.compile(r"#重开\b")
PERFORMANCE_STATUS_RE = re.compile(r"#剧情\b")
PERFORMANCE_ROUNDS_RE = re.compile(r"(?:轮次|轮数|rounds?)\s*[=:：]?\s*(\d+)", re.IGNORECASE)


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
        self.performance_store = PerformanceStateStore(plugin_dir)
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

    async def handle_performance_command(self, event: Any) -> dict[str, Any] | None:
        if not self.config.performance_enabled:
            return None

        text = event_text(event)
        scene_key = scene_key_for_event(event)
        if PERFORMANCE_PAUSE_RE.search(text):
            state = self.performance_store.load(scene_key)
            state["active"] = False
            state["waiting_user_instruction"] = True
            self.performance_store.save(scene_key, state)
            return {"handled": True, "message": "演出已暂停。可以用 #继续 给出下一步指示。"}

        if PERFORMANCE_RESET_RE.search(text):
            self.performance_store.clear(scene_key)
            return {"handled": True, "message": "演出状态已重开。可以用 #开演 开始新一幕。"}

        if PERFORMANCE_STATUS_RE.search(text):
            return {"handled": True, "message": self.describe_performance(scene_key)}

        if PERFORMANCE_START_RE.search(text):
            state = await self.start_performance(event, text, previous_state=None)
            return {
                "handled": True,
                "message": self._performance_started_message(state),
                "handoff": self.next_performance_handoff(scene_key),
            }

        if PERFORMANCE_CONTINUE_RE.search(text):
            previous = self.performance_store.load(scene_key)
            state = await self.start_performance(event, text, previous_state=previous)
            return {
                "handled": True,
                "message": self._performance_started_message(state),
                "handoff": self.next_performance_handoff(scene_key),
            }

        return None

    async def start_performance(
        self,
        event: Any,
        instruction: str,
        previous_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        scene_key = scene_key_for_event(event)
        participants = self._select_performance_participants(event, instruction, previous_state)
        round_limit = self._extract_round_limit(instruction)
        world_state = self._state_manager_for_scene_key(scene_key).load()
        worldbook = self.worldbook.read()
        director_payload = await self._create_performance_plan(
            event,
            instruction,
            scene_key,
            participants,
            round_limit,
            previous_state,
            world_state,
            worldbook,
        )
        state = self._normalize_performance_plan(
            scene_key,
            participants,
            round_limit,
            director_payload,
            previous_state,
        )
        self.performance_store.save(scene_key, state)
        return state

    async def _create_performance_plan(
        self,
        event: Any,
        instruction: str,
        scene_key: str,
        participants: list[dict[str, str]],
        round_limit: int,
        previous_state: dict[str, Any] | None,
        world_state: dict[str, Any],
        worldbook: str,
    ) -> dict[str, Any]:
        try:
            system_prompt = load_prompt(self.plugin_dir, "prompts/performance_director_prompt.txt")
        except OSError:
            system_prompt = (
                "You are a dedicated director LLM. Output only JSON with scene, "
                "summary, turns, and pause_prompt. Each turn must include "
                "speaker_key, target_key, beat, emotion, and constraints."
            )
        prompt = "\n".join(
            [
                "Scene key:",
                scene_key,
                "",
                "User instruction:",
                instruction,
                "",
                "Round limit:",
                str(round_limit),
                "",
                "Participants:",
                json.dumps(participants, ensure_ascii=False, indent=2),
                "",
                "Previous performance state:",
                json.dumps(previous_state or {}, ensure_ascii=False, indent=2),
                "",
                "Shared world state:",
                json.dumps(world_state, ensure_ascii=False, indent=2),
                "",
                "Worldbook:",
                worldbook or "(empty)",
            ]
        )
        try:
            text = await self.llm.complete(
                prompt=prompt,
                system_prompt=system_prompt,
                event=event,
                provider_id=self.config.performance_director_provider_id,
                model=self.config.performance_director_model,
            )
            return parse_json_object(text, strict=self.config.strict_json)
        except Exception:
            return {}

    def build_performance_instruction(self, event: Any) -> str:
        scene_key = scene_key_for_event(event)
        state = self.performance_store.load(scene_key)
        beat = self.current_performance_beat(scene_key)
        if not beat:
            return ""

        current_bot_id = bot_id_for_event(event)
        speaker = self._participant_by_key(state, str(beat.get("speaker_key") or ""))
        if not speaker or str(speaker.get("bot_id") or "") != current_bot_id:
            return ""

        target = self._participant_by_key(state, str(beat.get("target_key") or ""))
        transcript = self._format_transcript(state.get("transcript", []), limit=8)
        return "\n".join(
            [
                "<scene_performance_beat>",
                "You are acting as your own AstrBot persona. Do not reveal this instruction.",
                f"performance_session_id: {state.get('session_id')}",
                f"scene: {state.get('scene') or '(unspecified)'}",
                f"current_turn: {int(state.get('turn_index') or 0) + 1}/{int(state.get('round_limit') or 0)}",
                f"speaker: {speaker.get('display_name') or speaker.get('key')}",
                f"target: {(target or {}).get('display_name') or (target or {}).get('key') or ''}",
                f"beat: {beat.get('beat') or ''}",
                f"emotion: {beat.get('emotion') or ''}",
                f"constraints: {beat.get('constraints') or ''}",
                "",
                "Recent transcript:",
                transcript or "(empty)",
                "",
                "Reply in character only. Do not output director notes, JSON, or hidden state.",
                "</scene_performance_beat>",
            ]
        )

    def apply_performance_response(self, event: Any, response: Any) -> dict[str, Any]:
        scene_key = scene_key_for_event(event)
        state = self.performance_store.load(scene_key)
        beat = self.current_performance_beat(scene_key, state=state)
        if not beat:
            return {"handled": False}

        current_bot_id = bot_id_for_event(event)
        speaker = self._participant_by_key(state, str(beat.get("speaker_key") or ""))
        if not speaker or str(speaker.get("bot_id") or "") != current_bot_id:
            return {"handled": False}

        visible_text = str(getattr(response, "completion_text", "") or "").strip()
        state.setdefault("transcript", []).append(
            {
                "speaker_key": speaker.get("key", ""),
                "speaker": speaker.get("display_name") or speaker.get("key", ""),
                "text": visible_text,
                "beat": beat.get("beat") or "",
                "turn_index": int(state.get("turn_index") or 0),
                "created_at": int(time.time()),
            }
        )
        state["turn_index"] = int(state.get("turn_index") or 0) + 1
        finished = state["turn_index"] >= int(state.get("round_limit") or 0)
        if finished and self.config.performance_auto_pause_after_rounds:
            state["active"] = False
            state["waiting_user_instruction"] = True
        self.performance_store.save(scene_key, state)
        return {
            "handled": True,
            "finished": finished,
            "handoff": None if finished else self.next_performance_handoff(scene_key, state=state),
            "pause_message": self._performance_pause_message(state) if finished else "",
        }

    def current_performance_beat(
        self,
        scene_key: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        state = state or self.performance_store.load(scene_key)
        if not state.get("active"):
            return None
        plan = state.get("plan")
        if not isinstance(plan, list) or not plan:
            return None
        index = int(state.get("turn_index") or 0)
        if index < 0 or index >= len(plan) or index >= int(state.get("round_limit") or 0):
            return None
        beat = plan[index]
        return beat if isinstance(beat, dict) else None

    def next_performance_handoff(
        self,
        scene_key: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        state = state or self.performance_store.load(scene_key)
        beat = self.current_performance_beat(scene_key, state=state)
        if not beat:
            return None
        speaker = self._participant_by_key(state, str(beat.get("speaker_key") or ""))
        if not speaker:
            return None
        mention_id = str(speaker.get("mention_id") or "").strip()
        if not mention_id:
            return None
        return {
            "session_id": str(state.get("session_id") or ""),
            "scene_key": scene_key,
            "speaker_key": speaker.get("key", ""),
            "mention_id": mention_id,
            "text": f" #演出接力:{state.get('session_id')} 请按当前剧情节拍继续回应。",
        }

    def describe_performance(self, scene_key: str) -> str:
        state = self.performance_store.load(scene_key)
        if not state.get("session_id"):
            return "当前没有演出会话。可以用 #开演 创建新一幕。"
        beat = self.current_performance_beat(scene_key, state=state)
        lines = [
            f"场景：{state.get('scene') or '(未指定)'}",
            f"状态：{'进行中' if state.get('active') else '已暂停'}",
            f"进度：{int(state.get('turn_index') or 0)}/{int(state.get('round_limit') or 0)}",
        ]
        if beat:
            lines.append(f"下一位：{beat.get('speaker_key')}")
            lines.append(f"节拍：{beat.get('beat') or ''}")
        if state.get("pause_prompt"):
            lines.append(f"提示：{state.get('pause_prompt')}")
        return "\n".join(lines)

    def _normalize_performance_plan(
        self,
        scene_key: str,
        participants: list[dict[str, str]],
        round_limit: int,
        payload: dict[str, Any],
        previous_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        participant_keys = [p["key"] for p in participants]
        raw_turns = payload.get("turns") if isinstance(payload, dict) else None
        plan: list[dict[str, str]] = []
        if isinstance(raw_turns, list):
            for i, raw in enumerate(raw_turns[:round_limit]):
                item = raw if isinstance(raw, dict) else {}
                speaker_key = str(item.get("speaker_key") or "").strip()
                if speaker_key not in participant_keys:
                    speaker_key = participant_keys[i % len(participant_keys)]
                target_key = str(item.get("target_key") or "").strip()
                if target_key not in participant_keys:
                    target_key = participant_keys[(participant_keys.index(speaker_key) + 1) % len(participant_keys)]
                plan.append(
                    {
                        "speaker_key": speaker_key,
                        "target_key": target_key,
                        "beat": str(item.get("beat") or "推进当前剧情。").strip(),
                        "emotion": str(item.get("emotion") or "").strip(),
                        "constraints": str(item.get("constraints") or "不要替其他角色做决定。").strip(),
                    }
                )
        while len(plan) < round_limit:
            speaker_key = participant_keys[len(plan) % len(participant_keys)]
            target_key = participant_keys[(participant_keys.index(speaker_key) + 1) % len(participant_keys)]
            plan.append(
                {
                    "speaker_key": speaker_key,
                    "target_key": target_key,
                    "beat": "根据用户指示和前文自然推进当前剧情。",
                    "emotion": "",
                    "constraints": "不要替其他角色做决定，不要复述导演信息。",
                }
            )

        return {
            "active": True,
            "scene_key": scene_key,
            "session_id": uuid.uuid4().hex[:12],
            "scene": str(payload.get("scene") or (previous_state or {}).get("scene") or "").strip(),
            "summary": str(payload.get("summary") or "").strip(),
            "round_limit": round_limit,
            "turn_index": 0,
            "participants": participants,
            "plan": plan,
            "transcript": list((previous_state or {}).get("transcript") or [])[-20:],
            "pause_prompt": str(payload.get("pause_prompt") or "本幕已暂停。可以用 #继续 给出新场景、话题、角色行为或情感变化。").strip(),
            "waiting_user_instruction": False,
        }

    def _select_performance_participants(
        self,
        event: Any,
        instruction: str,
        previous_state: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        targets = self.config.dialogue_targets or {}
        participants: list[dict[str, str]] = []
        current_bot_id = bot_id_for_event(event)
        for key, target in targets.items():
            item = self._target_to_participant(str(key), target)
            if item["bot_id"] == current_bot_id:
                participants.append(item)
                break

        for key, target in targets.items():
            item = self._target_to_participant(str(key), target)
            display_name = item.get("display_name", "")
            if key in instruction or (display_name and display_name in instruction):
                if item not in participants:
                    participants.append(item)

        if len(participants) < 2 and previous_state:
            for item in previous_state.get("participants") or []:
                if isinstance(item, dict) and item not in participants:
                    participants.append({k: str(item.get(k) or "") for k in ("key", "bot_id", "mention_id", "display_name")})
                if len(participants) >= 2:
                    break

        if len(participants) < 2:
            for key, target in targets.items():
                item = self._target_to_participant(str(key), target)
                if item not in participants:
                    participants.append(item)
                if len(participants) >= 2:
                    break

        if not participants:
            participants.append(
                {
                    "key": "self",
                    "bot_id": current_bot_id,
                    "mention_id": str(getattr(event, "get_self_id", lambda: "")() or ""),
                    "display_name": "当前机器人",
                }
            )
        return participants

    @staticmethod
    def _target_to_participant(key: str, target: Any) -> dict[str, str]:
        return {
            "key": key,
            "bot_id": str(getattr(target, "bot_id", "") or ""),
            "mention_id": str(getattr(target, "mention_id", "") or ""),
            "display_name": str(getattr(target, "display_name", "") or key),
        }

    def _extract_round_limit(self, instruction: str) -> int:
        match = PERFORMANCE_ROUNDS_RE.search(instruction)
        rounds = self.config.performance_default_rounds
        if match:
            try:
                rounds = int(match.group(1))
            except ValueError:
                rounds = self.config.performance_default_rounds
        return min(max(rounds, 1), max(self.config.performance_max_rounds, 1))

    @staticmethod
    def _participant_by_key(state: dict[str, Any], key: str) -> dict[str, str] | None:
        for item in state.get("participants") or []:
            if isinstance(item, dict) and str(item.get("key") or "") == key:
                return {k: str(item.get(k) or "") for k in ("key", "bot_id", "mention_id", "display_name")}
        return None

    @staticmethod
    def _format_transcript(transcript: Any, limit: int = 8) -> str:
        if not isinstance(transcript, list):
            return ""
        lines = []
        for item in transcript[-limit:]:
            if not isinstance(item, dict):
                continue
            speaker = str(item.get("speaker") or item.get("speaker_key") or "")
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _performance_started_message(state: dict[str, Any]) -> str:
        return (
            f"演出已开始：{state.get('scene') or '未命名场景'}\n"
            f"轮次：{state.get('round_limit')}\n"
            "我会按导演剧本自动接力，到轮次后暂停等待你的新指示。"
        )

    @staticmethod
    def _performance_pause_message(state: dict[str, Any]) -> str:
        return str(
            state.get("pause_prompt")
            or "本幕已暂停。可以用 #继续 给出新场景、话题、角色行为或情感变化。"
        )

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

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import At, Plain

from .config import load_config
from .core.orchestrator import Orchestrator
from .utils.logger import PLUGIN_NAME, debug_log, info_log, warning_log

try:
    from astrbot.core.agent.message import TextPart
except Exception:  # pragma: no cover - depends on AstrBot runtime version.
    TextPart = None


@register(
    "scene_orchestrator",
    "GesRo",
    "Scene Orchestrator Plugin for multi-role drama systems.",
    "1.0.0",
)
class SceneOrchestratorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.raw_config = config
        self.config = load_config(config)
        self.plugin_dir = Path(__file__).resolve().parent
        self.orchestrator = Orchestrator(context, self.config, self.plugin_dir)

    def _is_takeover_enabled(self) -> bool:
        return self.config.enabled and self.config.mode == "takeover"

    def _is_inject_enabled(self) -> bool:
        return self.config.enabled and self.config.mode == "inject"

    def _is_director_gate_enabled(self) -> bool:
        return self.config.enabled and self.config.mode == "director_gate"

    @filter.event_message_type(filter.EventMessageType.ALL, priority=9999)
    async def on_message(self, event: AstrMessageEvent):
        if not (self._is_takeover_enabled() or self._is_director_gate_enabled()):
            return

        message = str(getattr(event, "message_str", "") or "").strip()
        if not message or message.startswith("/"):
            return

        if self._is_director_gate_enabled():
            gate = await self.orchestrator.director_gate(event)
            performance_command = await self.orchestrator.handle_performance_command(event)
            if performance_command and performance_command.get("handled"):
                message = str(performance_command.get("message") or "")
                if message:
                    yield event.plain_result(message)
                handoff = performance_command.get("handoff")
                if isinstance(handoff, dict):
                    asyncio.create_task(self._send_performance_handoff(event, handoff))
                event.stop_event()
                return

            handoff = self.orchestrator.extract_dialogue_handoff(event)
            if handoff:
                event.set_extra("scene_orchestrator_dialogue_handoff", handoff)
                if not handoff.get("ok"):
                    warning_log(
                        logger,
                        f"unknown dialogue handoff target: {handoff.get('target_key')} "
                        f"known_targets={handoff.get('known_targets')}",
                    )
            info_log(
                logger,
                self.config.debug_mode,
                f"director_gate native mode bot_id={gate.get('bot_id')} "
                f"scene_key={gate.get('scene_key')} "
                f"handoff={handoff.get('target_key') if handoff else ''}",
            )
            return

        result = await self.orchestrator.process(event)
        decision = result.get("decision", {})
        persona = result.get("persona")
        debug_log(
            logger,
            self.config.debug_mode,
            f"takeover decision={decision}",
        )
        debug_log(
            logger,
            self.config.debug_persona_resolution,
            f"persona source={getattr(persona, 'source', 'none')} "
            f"id={getattr(persona, 'persona_id', '')}",
        )

        if result.get("should_reply"):
            yield event.plain_result(str(result.get("reply") or ""))
            event.stop_event()

    @filter.on_llm_request()
    async def on_llm_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        if not (self._is_inject_enabled() or self._is_director_gate_enabled()):
            return

        if self._is_director_gate_enabled():
            context_text = self.orchestrator.build_performance_instruction(event)
            if context_text:
                debug_log(logger, self.config.debug_mode, "inject performance beat")
                self._append_dynamic_context(req, context_text)
                return

            context_text = self.orchestrator.build_director_gate_instruction(event)
            if not context_text:
                return
            debug_log(logger, self.config.debug_mode, "inject native director context")
        else:
            context_text = self.orchestrator.build_inject_context(event)
            debug_log(logger, self.config.debug_mode, "inject scene context into LLM request")
        self._append_dynamic_context(req, context_text)

    def _append_dynamic_context(self, req: ProviderRequest, text: str) -> None:
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(extra_parts, list) and TextPart is not None:
            part: Any = TextPart(text=text)
            mark_as_temp = getattr(part, "mark_as_temp", None)
            if callable(mark_as_temp):
                part = mark_as_temp()
            extra_parts.append(part)
            return

        if hasattr(req, "system_prompt"):
            warning_log(
                logger,
                "extra_user_content_parts is unavailable; appending scene context to system_prompt",
            )
            req.system_prompt = f"{getattr(req, 'system_prompt', '')}\n\n{text}"

    @filter.on_llm_response()
    async def on_llm_response(
        self,
        event: AstrMessageEvent,
        response: LLMResponse,
    ) -> None:
        if not self._is_director_gate_enabled() or response is None:
            return

        performance_result = self.orchestrator.apply_performance_response(event, response)
        if performance_result.get("handled"):
            handoff = performance_result.get("handoff")
            if isinstance(handoff, dict):
                asyncio.create_task(self._send_performance_handoff(event, handoff))
            pause_message = str(performance_result.get("pause_message") or "")
            if pause_message:
                asyncio.create_task(self._send_plain_later(event, pause_message))
            debug_log(
                logger,
                self.config.debug_mode,
                f"performance advanced finished={performance_result.get('finished')}",
            )
            return

        result = self.orchestrator.apply_director_response(event, response)
        if result.get("error"):
            warning_log(
                logger,
                f"failed to parse scene director state: {result.get('error')}",
            )
        elif result.get("found"):
            debug_log(
                logger,
                self.config.debug_mode,
                f"saved scene director state no_reply={result.get('no_reply')}",
            )
        if result.get("no_reply"):
            return

        handoff = event.get_extra("scene_orchestrator_dialogue_handoff", None)
        if not isinstance(handoff, dict) or not handoff.get("ok"):
            return
        scene_key = str(handoff.get("scene_key") or "")
        if not self.orchestrator.can_send_dialogue_handoff(scene_key):
            debug_log(logger, self.config.debug_mode, "skip dialogue handoff due to cooldown")
            return
        asyncio.create_task(self._send_dialogue_handoff(event, handoff))

    async def _send_dialogue_handoff(
        self,
        event: AstrMessageEvent,
        handoff: dict[str, Any],
    ) -> None:
        delay = max(int(self.config.dialogue_handoff_delay_seconds), 0)
        if delay:
            await asyncio.sleep(delay)

        target = handoff.get("target")
        mention_id = str(getattr(target, "mention_id", "") or "").strip()
        if not mention_id:
            return

        target_key = str(handoff.get("target_key") or "")
        text = self.orchestrator.build_dialogue_handoff_text(target_key)
        await event.send(event.chain_result([At(qq=mention_id), Plain(text)]))
        debug_log(
            logger,
            self.config.debug_mode,
            f"sent dialogue handoff target={target_key} mention_id={mention_id}",
        )

    async def _send_performance_handoff(
        self,
        event: AstrMessageEvent,
        handoff: dict[str, Any],
    ) -> None:
        delay = max(int(self.config.performance_handoff_delay_seconds), 0)
        if delay:
            await asyncio.sleep(delay)

        mention_id = str(handoff.get("mention_id") or "").strip()
        text = str(handoff.get("text") or "").strip()
        if not mention_id or not text:
            return
        await event.send(event.chain_result([At(qq=mention_id), Plain(text)]))
        debug_log(
            logger,
            self.config.debug_mode,
            f"sent performance handoff speaker={handoff.get('speaker_key')} "
            f"mention_id={mention_id}",
        )

    async def _send_plain_later(
        self,
        event: AstrMessageEvent,
        text: str,
    ) -> None:
        await asyncio.sleep(0.2)
        await event.send(event.chain_result([Plain(text)]))

    async def terminate(self) -> None:
        pass

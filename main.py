from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .config import load_config
from .core.orchestrator import Orchestrator
from .utils.logger import PLUGIN_NAME, debug_log, warning_log

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

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self._is_takeover_enabled():
            return

        message = str(getattr(event, "message_str", "") or "").strip()
        if not message or message.startswith("/"):
            return

        result = await self.orchestrator.process(event)
        decision = result.get("decision", {})
        debug_log(
            logger,
            self.config.debug_mode,
            f"takeover decision={decision}",
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
        if not self._is_inject_enabled():
            return

        context_text = self.orchestrator.build_inject_context()
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

    async def terminate(self) -> None:
        pass

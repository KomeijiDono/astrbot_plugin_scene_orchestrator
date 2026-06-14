from typing import Any


PLUGIN_NAME = "astrbot_plugin_scene_orchestrator"


def debug_log(logger: Any, enabled: bool, message: str) -> None:
    if enabled:
        logger.debug(f"[{PLUGIN_NAME}] {message}")


def warning_log(logger: Any, message: str) -> None:
    logger.warning(f"[{PLUGIN_NAME}] {message}")

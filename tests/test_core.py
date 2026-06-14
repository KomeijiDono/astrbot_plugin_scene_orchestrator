import json
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_scene_orchestrator.config import load_config
from astrbot_plugin_scene_orchestrator.core.role_selector import RoleSelector
from astrbot_plugin_scene_orchestrator.core.state_manager import StateManager
from astrbot_plugin_scene_orchestrator.utils.llm import parse_json_object


class ConfigTests(unittest.TestCase):
    def test_default_config(self) -> None:
        config = load_config({})
        self.assertTrue(config.enabled)
        self.assertEqual(config.mode, "takeover")
        self.assertEqual(config.max_events, 100)


class StateManagerTests(unittest.TestCase):
    def test_missing_state_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = StateManager(Path(temp_dir) / "world_state.json")
            self.assertEqual(manager.load()["scene"], "default")

    def test_corrupt_state_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world_state.json"
            path.write_text("{bad json", encoding="utf-8")
            manager = StateManager(path)
            self.assertEqual(manager.load()["events"], [])

    def test_event_history_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = StateManager(Path(temp_dir) / "world_state.json", max_events=2)
            manager.update({"scene": "a", "speaker": "one"})
            manager.update({"scene": "b", "speaker": "two"})
            manager.update({"scene": "c", "speaker": "three"})
            state = manager.load()
            self.assertEqual(len(state["events"]), 2)
            self.assertEqual(state["events"][0]["speaker"], "two")


class JsonParseTests(unittest.TestCase):
    def test_parse_plain_json(self) -> None:
        self.assertEqual(parse_json_object('{"scene": "x"}')["scene"], "x")

    def test_parse_fenced_json(self) -> None:
        text = '```json\n{"speaker": "anon"}\n```'
        self.assertEqual(parse_json_object(text)["speaker"], "anon")

    def test_invalid_json_non_strict_fallback(self) -> None:
        self.assertEqual(parse_json_object("not json", strict=False), {})


class RoleSelectorTests(unittest.TestCase):
    def test_explicit_speaker_wins(self) -> None:
        selector = RoleSelector()
        self.assertEqual(selector.select({"speaker": "tomori"}, {}), "tomori")

    def test_anon_focus(self) -> None:
        selector = RoleSelector()
        self.assertEqual(selector.select({"focus": "爱音"}, {}), "chihaya_anon")

    def test_conflict_scene(self) -> None:
        selector = RoleSelector()
        self.assertEqual(selector.select({"scene": "冲突升级"}, {}), "saki")

    def test_default_role(self) -> None:
        selector = RoleSelector(default_role="fallback")
        self.assertEqual(selector.select({}, {}), "fallback")


if __name__ == "__main__":
    unittest.main()

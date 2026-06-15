import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from astrbot_plugin_scene_orchestrator.config import SceneOrchestratorConfig, load_config
from astrbot_plugin_scene_orchestrator.core.orchestrator import Orchestrator
from astrbot_plugin_scene_orchestrator.core.persona import PersonaResolver
from astrbot_plugin_scene_orchestrator.core.role_selector import RoleSelector
from astrbot_plugin_scene_orchestrator.core.speech_plan import (
    SpeechPlanStore,
    build_director_instruction,
    plan_allows_bot,
)
from astrbot_plugin_scene_orchestrator.core.state_manager import StateManager
from astrbot_plugin_scene_orchestrator.core.state_scope import (
    StateScopeResolver,
    safe_origin_name,
)
from astrbot_plugin_scene_orchestrator.core.worldbook import Worldbook
from astrbot_plugin_scene_orchestrator.core.event_identity import (
    bot_id_for_event,
    message_key_for_event,
    scene_key_for_event,
)
from astrbot_plugin_scene_orchestrator.utils.llm import parse_json_object


class ConfigTests(unittest.TestCase):
    def test_default_config(self) -> None:
        config = load_config({})
        self.assertTrue(config.enabled)
        self.assertEqual(config.mode, "takeover")
        self.assertEqual(config.max_events, 100)
        self.assertEqual(config.state_scope, "origin")
        self.assertTrue(config.inherit_astrbot_persona)
        self.assertEqual(config.speech_plan_ttl_seconds, 120)

    def test_director_gate_mode_is_valid(self) -> None:
        config = load_config({"general": {"mode": "director_gate"}})
        self.assertEqual(config.mode, "director_gate")


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

    def test_state_path_is_isolated_by_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = StateScopeResolver(Path(temp_dir), scope="origin")
            first = SimpleNamespace(unified_msg_origin="aiocqhttp:bot-a:GroupMessage:100")
            second = SimpleNamespace(unified_msg_origin="telegram/bot b/private 200")

            first_path = resolver.state_path_for_event(first)
            second_path = resolver.state_path_for_event(second)

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.parent.name, "world_states")
            self.assertNotIn(":", first_path.name)
            self.assertNotIn("/", second_path.name)
            self.assertNotIn(" ", second_path.name)

    def test_origin_state_does_not_load_legacy_global_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_path = root / "data" / "world_state.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps({"scene": "legacy", "events": [], "mood": {}}),
                encoding="utf-8",
            )
            resolver = StateScopeResolver(root, scope="origin")
            event = SimpleNamespace(unified_msg_origin="platform:bot:session")
            manager = StateManager(resolver.state_path_for_event(event))

            self.assertEqual(manager.load()["scene"], "default")

    def test_safe_origin_name_is_stable_and_safe(self) -> None:
        first = safe_origin_name("qq:bot/Group Message:123")
        second = safe_origin_name("qq:bot/Group Message:123")
        self.assertEqual(first, second)
        self.assertNotIn(":", first)
        self.assertNotIn("/", first)
        self.assertNotIn(" ", first)


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


class FakeConversationManager:
    def __init__(self, persona_id: str = "") -> None:
        self.persona_id = persona_id

    async def get_curr_conversation_id(self, origin: str) -> str:
        return "conversation-id"

    async def get_conversation(self, origin: str, conversation_id: str) -> SimpleNamespace:
        return SimpleNamespace(persona_id=self.persona_id)


class FakeDefaultPersonaManager:
    def __init__(self, default_id: str = "", personas: list | None = None) -> None:
        self.default_id = default_id
        self.personas_v3 = personas or []

    async def get_default_persona_v3(self, umo: str | None = None) -> dict:
        if not self.default_id:
            return {}
        return {"name": self.default_id}


class PersonaResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_persona_is_resolved(self) -> None:
        context = SimpleNamespace(
            conversation_manager=FakeConversationManager("anon"),
            persona_manager=FakeDefaultPersonaManager(
                personas=[{"name": "anon", "prompt": "Anon persona prompt"}]
            ),
        )
        resolver = PersonaResolver(context)
        persona = await resolver.resolve(SimpleNamespace(unified_msg_origin="origin-a"))

        self.assertEqual(persona.persona_id, "anon")
        self.assertEqual(persona.prompt, "Anon persona prompt")

    async def test_default_persona_is_resolved(self) -> None:
        context = SimpleNamespace(
            conversation_manager=FakeConversationManager(""),
            persona_manager=FakeDefaultPersonaManager(
                default_id="default",
                personas=[{"name": "default", "prompt": "Default persona prompt"}],
            ),
        )
        resolver = PersonaResolver(context)
        persona = await resolver.resolve(SimpleNamespace(unified_msg_origin="origin-a"))

        self.assertEqual(persona.persona_id, "default")
        self.assertEqual(persona.prompt, "Default persona prompt")

    async def test_explicit_none_persona_disables_injection(self) -> None:
        context = SimpleNamespace(
            conversation_manager=FakeConversationManager("[%None]"),
            persona_manager=FakeDefaultPersonaManager(default_id="default"),
        )
        resolver = PersonaResolver(context)
        persona = await resolver.resolve(SimpleNamespace(unified_msg_origin="origin-a"))

        self.assertFalse(persona.has_persona)
        self.assertEqual(persona.source, "explicit_none")


class PromptPersonaTests(unittest.TestCase):
    def test_persona_is_added_to_system_prompt_when_enabled(self) -> None:
        persona = SimpleNamespace(
            has_persona=True,
            format_for_prompt=lambda: "AstrBot current persona:\n- name: anon",
        )
        prompt = Orchestrator._with_persona("base prompt", persona)

        self.assertIn("base prompt", prompt)
        self.assertIn("AstrBot current persona", prompt)

    def test_persona_is_not_added_when_disabled(self) -> None:
        config = SceneOrchestratorConfig(inherit_astrbot_persona=False)
        self.assertFalse(config.inherit_astrbot_persona)


class FakeMessageObj:
    def __init__(self, self_id: str, message_id: str) -> None:
        self.self_id = self_id
        self.message_id = message_id
        self.raw_message = {"self_id": self_id, "message_id": message_id}


class FakeEvent:
    def __init__(
        self,
        self_id: str,
        message_id: str = "m-1",
        group_id: str = "g-1",
        sender_id: str = "u-1",
        text: str = "hello",
        platform_id: str = "qq",
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = f"{platform_id}:{self_id}:GroupMessage:{group_id}"
        self.message_obj = FakeMessageObj(self_id, message_id)
        self._self_id = self_id
        self._message_id = message_id
        self._group_id = group_id
        self._sender_id = sender_id
        self._platform_id = platform_id

    def get_platform_id(self) -> str:
        return self._platform_id

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_message_id(self) -> str:
        return self._message_id


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.prompts: list[str] = []

    async def complete(self, **kwargs) -> str:
        self.calls += 1
        self.prompts.append(str(kwargs.get("prompt") or ""))
        return self.text


class DirectorGateTests(unittest.IsolatedAsyncioTestCase):
    def _plugin_dir(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        prompt_dir = root / "prompts"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "speech_plan_prompt.txt").write_text("plan prompt", encoding="utf-8")
        return root

    def test_same_group_message_key_across_bots(self) -> None:
        first = FakeEvent(self_id="bot-a", message_id="same-message")
        second = FakeEvent(self_id="bot-b", message_id="same-message")

        self.assertEqual(scene_key_for_event(first), scene_key_for_event(second))
        self.assertEqual(message_key_for_event(first), message_key_for_event(second))
        self.assertNotEqual(bot_id_for_event(first), bot_id_for_event(second))

    async def test_first_bot_creates_plan_second_bot_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_dir = self._plugin_dir(temp_dir)
            config = SceneOrchestratorConfig(mode="director_gate")
            orchestrator = Orchestrator(SimpleNamespace(), config, plugin_dir)
            orchestrator.llm = FakeLLM(
                json.dumps(
                    {
                        "scene": "test scene",
                        "world_event": "user greets the room",
                        "speakers": ["qq:bot-a"],
                        "silent": ["qq:bot-b"],
                        "reply_style": "short",
                        "emotion": "calm",
                        "intent": "answer briefly",
                    }
                )
            )

            first = FakeEvent(self_id="bot-a", message_id="same-message")
            second = FakeEvent(self_id="bot-b", message_id="same-message")
            first_gate = await orchestrator.director_gate(first)
            second_gate = await orchestrator.director_gate(second)

            self.assertTrue(first_gate["created"])
            self.assertFalse(second_gate["created"])
            self.assertTrue(first_gate["allow_reply"])
            self.assertFalse(second_gate["allow_reply"])
            self.assertEqual(orchestrator.llm.calls, 1)

    async def test_selected_bot_gets_director_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_dir = self._plugin_dir(temp_dir)
            config = SceneOrchestratorConfig(mode="director_gate")
            orchestrator = Orchestrator(SimpleNamespace(), config, plugin_dir)
            event = FakeEvent(self_id="bot-a")
            message_key = message_key_for_event(event)
            orchestrator.plan_store.save(
                message_key,
                {
                    "scene": "test",
                    "world_event": "event",
                    "speakers": ["qq:bot-a"],
                    "silent": [],
                    "reply_style": "long",
                    "emotion": "curious",
                    "intent": "ask a follow-up",
                },
            )

            instruction = await orchestrator.build_director_gate_instruction(event)

            self.assertIn("selected by the scene director", instruction)
            self.assertIn("reply_style: long", instruction)

    def test_speech_plan_ttl_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SpeechPlanStore(Path(temp_dir), ttl_seconds=1)
            store.save(
                "message",
                {
                    "speakers": ["qq:bot-a"],
                    "created_at": 1,
                    "ttl_seconds": 1,
                },
            )

            self.assertIsNone(store.load("message"))

    def test_plan_allows_only_selected_bot(self) -> None:
        plan = {"speakers": ["qq:bot-a"], "silent": ["qq:bot-b"]}

        self.assertTrue(plan_allows_bot(plan, "qq:bot-a"))
        self.assertFalse(plan_allows_bot(plan, "qq:bot-b"))
        self.assertFalse(plan_allows_bot(plan, "qq:bot-c"))

    def test_director_instruction_mentions_native_astrbot_chain(self) -> None:
        instruction = build_director_instruction(
            {
                "scene": "test",
                "world_event": "event",
                "speakers": ["qq:bot-a"],
                "reply_style": "short",
                "emotion": "calm",
                "intent": "respond",
            },
            "qq:bot-a",
        )

        self.assertIn("AstrBot persona", instruction)
        self.assertIn("knowledge base", instruction)


class WorldbookTests(unittest.IsolatedAsyncioTestCase):
    def test_worldbook_auto_creates_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worldbook = Worldbook(Path(temp_dir), auto_create=True)
            text = worldbook.read()

            self.assertIn("Scene Orchestrator Worldbook", text)
            self.assertTrue((Path(temp_dir) / "data" / "worldbook.md").exists())

    def test_worldbook_respects_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data" / "worldbook.md"
            path.parent.mkdir(parents=True)
            path.write_text("abcdef", encoding="utf-8")
            worldbook = Worldbook(Path(temp_dir), max_chars=3)

            self.assertEqual(worldbook.read(), "abc")

    def test_worldbook_disabled_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worldbook = Worldbook(Path(temp_dir), enabled=False)

            self.assertEqual(worldbook.read(), "")
            self.assertFalse((Path(temp_dir) / "data" / "worldbook.md").exists())

    async def test_speech_plan_prompt_includes_worldbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_dir = Path(temp_dir)
            prompt_dir = plugin_dir / "prompts"
            prompt_dir.mkdir(parents=True)
            (prompt_dir / "speech_plan_prompt.txt").write_text("plan prompt", encoding="utf-8")
            worldbook_path = plugin_dir / "data" / "worldbook.md"
            worldbook_path.parent.mkdir(parents=True)
            worldbook_path.write_text("Shared world premise: music academy.", encoding="utf-8")

            config = SceneOrchestratorConfig(mode="director_gate")
            orchestrator = Orchestrator(SimpleNamespace(), config, plugin_dir)
            orchestrator.llm = FakeLLM(
                json.dumps(
                    {
                        "speakers": ["qq:bot-a"],
                        "silent": [],
                        "intent": "respond",
                    }
                )
            )

            await orchestrator.director_gate(FakeEvent(self_id="bot-a"))

            self.assertEqual(orchestrator.llm.calls, 1)
            self.assertIn("Shared world premise: music academy.", orchestrator.llm.prompts[0])


if __name__ == "__main__":
    unittest.main()

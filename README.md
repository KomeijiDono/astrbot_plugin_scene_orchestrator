# AstrBot Scene Orchestrator

Multi-role scene orchestration plugin for AstrBot.

## Modes

- `takeover`: the plugin calls a director LLM, generates a role reply itself, sends it, then stops the native AstrBot LLM chain.
- `inject`: the plugin does not reply. It only injects current scene context before native AstrBot LLM requests.
- `director_gate`: native LLM scene state mode. The plugin does not call an extra director LLM, does not choose speakers, and does not stop other bot instances. It injects shared scene state into native AstrBot LLM requests, then reads a hidden JSON block from the native LLM response and saves it as the next shared scene state.

## Configuration

- `general.enabled`: enable the plugin.
- `general.mode`: `takeover`, `inject`, or `director_gate`.
- `general.debug_mode`: print debug logs.
- `scene.enable_auto_scene`: allow automatic scene progression.
- `scene.max_events`: maximum retained world-state events.
- `state.scope`: state isolation scope. `origin` stores state by AstrBot origin; `global` shares one global state. In `director_gate`, group chats use a shared group scene key such as `group:856127739`.
- `worldbook.enabled`: enable the editable worldbook.
- `worldbook.path`: worldbook path, default `data/worldbook.md`.
- `worldbook.max_chars`: maximum worldbook characters injected into prompts.
- `worldbook.auto_create`: create a default worldbook when missing.
- `dialogue.enabled`: enable `#对话X` handoff.
- `dialogue.handoff_delay_seconds`: delay before sending the handoff mention.
- `dialogue.cooldown_seconds`: minimum interval between handoffs in the same group.
- `dialogue.targets`: mapping for `#对话X`, for example `{"B":{"bot_id":"2:2855813757","mention_id":"2855813757","display_name":"千早爱音"}}`.
- `persona.inherit_astrbot_persona`: whether takeover mode should inherit AstrBot persona.
- `persona.debug_persona_resolution`: print persona resolution logs.
- `llm.strict_json`: strict JSON parsing for takeover director decisions.
- `roles.default_role`: fallback role id for takeover mode.

The legacy `director.speech_plan_ttl_seconds` and `director.default_reply_style` settings are kept for config compatibility, but `director_gate` no longer creates speech plans.

## director_gate Hidden State

In `director_gate`, the plugin injects instructions asking the native AstrBot LLM to append:

```text
<scene_director_state>
{
  "scene": "current scene name",
  "speaker": "current bot or role name",
  "emotion": "current emotion",
  "intent": "reply intent",
  "world_event": "what happened in this turn",
  "next_direction": "suggested next scene direction",
  "focus": "current scene focus"
}
</scene_director_state>
```

The plugin removes this block before the message is sent. If the visible reply is exactly `NO_REPLY`, the plugin saves the hidden state and clears the visible response.

## Dialogue Handoff

When `dialogue.enabled` is true and `dialogue.targets` contains `B`, a user can send:

```text
@机器人A #对话B start the scene
```

After A finishes its native AstrBot LLM reply, the plugin waits for `dialogue.handoff_delay_seconds` and sends a real group message that mentions B:

```text
@机器人B #对话接力:B 请接着刚才的共享剧情状态回应，不要复述导演信息。
```

The handoff message does not contain `#对话B`, so it does not trigger an automatic loop.

## State Files

Runtime state is stored under:

```text
astrbot_plugin_scene_orchestrator/data/world_states/
```

The editable worldbook is stored at:

```text
astrbot_plugin_scene_orchestrator/data/worldbook.md
```

Final visible replies still use AstrBot's native persona, knowledge base, provider, and memory chain.

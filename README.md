# AstrBot Scene Orchestrator

AstrBot 多机器人剧情导演插件。插件用于让多个机器人在同一个群聊里共享剧情状态、按导演计划接力演出，同时保留每个机器人自己的 AstrBot 人格、知识库、记忆和 provider。

## 运行模式

- `takeover`：插件自己调用导演 LLM，生成角色回复并发送，然后阻止 AstrBot 原生 LLM 链路继续执行。
- `inject`：插件不直接回复，只在 AstrBot 原生 LLM 请求前注入当前场景上下文。
- `director_gate`：推荐模式。插件使用 AstrBot 原生 LLM 回复，支持共享剧情状态、手动 `#对话` 接力，以及新的“分幕演出”自动接力。

## 分幕演出

在 `director_gate` 模式下，可以用 `#开演` 启动一幕剧情。插件会先调用一次专门的导演 LLM，生成固定轮次的剧情节拍计划，然后按计划依次 @ 配置好的角色机器人。

角色机器人收到 @ 后，仍然使用自己的 AstrBot 原生人格、知识库、记忆和 provider 生成回复。导演 LLM 只负责“这一轮该演什么”，不替角色写最终台词。

示例：

```text
@若叶睦 #开演 #对话千早爱音 轮次=4
场景：睦家门口
话题：邀请爱音到睦家
要求：睦主动但少话，爱音嘴硬但动摇
```

常用指令：

- `#开演`：创建新的演出会话，并生成本幕导演计划。
- `#继续`：基于上一幕的对话记录和用户新指示，生成下一幕计划。
- `#暂停`：停止自动接力，但保留当前演出状态。
- `#重开`：清空当前群的演出状态。
- `#剧情`：查看当前场景、进度和下一步剧情节拍。

自动接力消息示例：

```text
@千早爱音 #演出接力:<session_id> 请按当前剧情节拍继续回应。
```

角色 LLM 会收到一个隐藏的 `<scene_performance_beat>` 指令块，里面包含当前场景、当前节拍、情绪、限制和最近演出记录。这个指令块会作为当前用户消息后的额外内容注入，不会替换 AstrBot 原有人格提示词。

## 配置说明

- `general.enabled`：是否启用插件。
- `general.mode`：运行模式，可选 `takeover`、`inject`、`director_gate`。
- `general.debug_mode`：是否输出调试日志。
- `scene.enable_auto_scene`：是否允许自动推进场景状态。
- `scene.max_events`：世界状态中最多保留多少条事件。
- `state.scope`：状态隔离范围。`director_gate` 群聊默认按 `group:{群号}` 共享状态。
- `worldbook.*`：世界观文件设置。
- `dialogue.enabled`：是否启用旧版 `#对话X` 手动接力。
- `dialogue.targets`：角色映射表。
- `dialogue.targets_json`：角色映射表的 JSON 字符串版本。如果 AstrBot 面板把对象配置清空，优先使用这里。
- `performance.enabled`：是否启用 `#开演/#继续/#暂停/#重开/#剧情`。
- `performance.default_rounds`：用户未写 `轮次=N` 时默认自动演出多少个角色回合。
- `performance.max_rounds`：用户指定轮次的上限，防止无限接力。
- `performance.handoff_delay_seconds`：每次角色回复后，等待多少秒再 @ 下一位。
- `performance.director_provider_id`：导演 LLM 使用的 provider id。留空则使用当前 AstrBot 默认 provider。
- `performance.director_model`：导演 LLM 使用的模型名。留空则使用 provider 默认模型。
- `performance.auto_pause_after_rounds`：达到计划轮次后是否自动暂停，等待用户继续指示。

角色映射表示例：

```json
{
  "A": {
    "bot_id": "default:980999560",
    "mention_id": "980999560",
    "display_name": "若叶睦"
  },
  "B": {
    "bot_id": "2:2855813757",
    "mention_id": "2855813757",
    "display_name": "千早爱音"
  }
}
```

`bot_id` 用于识别哪个机器人正在回复，格式是 `platform_id:self_id`。  
`mention_id` 用于群聊里实际 @ 机器人。  
`display_name` 可用于指令里写 `#对话千早爱音`。

## 旧版隐藏状态

当没有激活的分幕演出节拍时，`director_gate` 仍保留旧版隐藏状态流程。插件会要求原生 AstrBot LLM 在回复末尾附加：

```text
<scene_director_state>
{
  "scene": "当前场景名",
  "speaker": "当前机器人或角色名",
  "emotion": "当前情绪",
  "intent": "本次回复意图",
  "world_event": "本轮发生的剧情事件",
  "next_direction": "接下来建议的剧情方向",
  "focus": "当前剧情焦点"
}
</scene_director_state>
```

插件会在消息发送前移除这个隐藏块。分幕演出模式不要求角色机器人输出这个隐藏块。

## 状态文件

运行时世界状态保存于：

```text
astrbot_plugin_scene_orchestrator/data/world_states/
```

分幕演出状态保存于：

```text
astrbot_plugin_scene_orchestrator/data/performance_states/
```

世界观文件默认保存于：

```text
astrbot_plugin_scene_orchestrator/data/worldbook.md
```

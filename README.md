# AstrBot Scene Orchestrator

多角色剧情调度插件，用于在 AstrBot 中维护场景、选择说话角色、推进剧情并生成角色回复。

## 模式

- `takeover`: 插件监听消息，执行导演决策并主动回复，然后停止后续默认 LLM 回复。
- `inject`: 插件不主动回复，只在 AstrBot 原生 LLM 请求前追加当前场景上下文。

## 配置

- `general.enabled`: 是否启用插件。
- `general.mode`: `takeover` 或 `inject`。
- `general.debug_mode`: 是否输出调试日志。
- `scene.enable_auto_scene`: 是否允许自动推进场景。
- `scene.max_events`: 世界状态最多保留事件数量。
- `llm.strict_json`: 导演决策是否要求 JSON。
- `roles.default_role`: 默认角色 ID。

## 状态文件

插件运行后会在目录内创建：

```text
astrbot_plugin_scene_orchestrator/data/world_state.json
```

该文件保存当前场景、近期事件、角色情绪和当前说话角色。

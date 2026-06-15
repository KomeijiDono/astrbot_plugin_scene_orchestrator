# AstrBot Scene Orchestrator

多角色剧情调度插件，用于在 AstrBot 中维护场景、选择说话角色、推进剧情并生成角色回复。

## 模式

- `takeover`: 插件监听消息，执行导演决策并主动回复，然后停止后续默认 LLM 回复。
- `inject`: 插件不主动回复，只在 AstrBot 原生 LLM 请求前追加当前场景上下文。
- `director_gate`: 多机器人推荐模式。插件作为 AstrBot 内部主导演，只决定当前机器人是否放行，并把导演意图注入原生 LLM 请求；最终回复仍由 AstrBot 自己的人格、知识库、provider 和记忆链路生成。

## 配置

- `general.enabled`: 是否启用插件。
- `general.mode`: `takeover`、`inject` 或 `director_gate`。
- `general.debug_mode`: 是否输出调试日志。
- `scene.enable_auto_scene`: 是否允许自动推进场景。
- `scene.max_events`: 世界状态最多保留事件数量。
- `state.scope`: 状态隔离范围，默认 `origin`，每个 `unified_msg_origin` 单独保存剧情。
- `director.speech_plan_ttl_seconds`: 发言计划有效期。
- `director.default_reply_style`: 导演未指定时的默认回复长度。
- `worldbook.enabled`: 是否启用用户可编辑世界观。
- `worldbook.path`: 世界观文件路径，默认 `data/worldbook.md`。
- `worldbook.max_chars`: 世界观最大读取字符数。
- `worldbook.auto_create`: 文件不存在时是否自动创建模板。
- `persona.inherit_astrbot_persona`: 是否继承 AstrBot 当前会话人格，默认开启。
- `persona.debug_persona_resolution`: 是否输出人格解析日志。
- `llm.strict_json`: 导演决策是否要求 JSON。
- `roles.default_role`: 默认角色 ID。

## 状态文件

插件运行后会在目录内创建：

```text
astrbot_plugin_scene_orchestrator/data/world_states/<origin>.json
```

每个 `unified_msg_origin` 会有独立状态文件，避免多个平台适配器或多个机器人实例共享同一套剧情。

`director_gate` 模式会额外创建：

```text
astrbot_plugin_scene_orchestrator/data/speech_plans/<message>.json
```

这些文件是短期发言计划缓存，用于让同一条消息只生成一次主导演决策。

## 世界观与知识库

插件会读取全局世界观文件：

```text
astrbot_plugin_scene_orchestrator/data/worldbook.md
```

这个文件供“导演决策”使用，适合写共同舞台、势力、关系、剧情边界和当前主线。AstrBot 知识库不由插件直接查询；最终回复仍走 AstrBot 原生 LLM 链路，所以每个机器人自己的 AstrBot 人格、知识库、provider 和记忆配置仍会生效。

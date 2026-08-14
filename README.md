# citation 插件

记忆引用追踪协议。在系统 prompt 中注入引用规范，从 LLM 回复里提取引用的记忆 ID，并清理协议标签。

---

## 接入点

| 接入方式 | 阶段 |
|---|---|
| v3 `PROMPT_RENDER_AFTER_EVENT_BUS` | legacy Prompt 事件后注入引用协议文本 |
| v3 `AFTER_REASONING_BEFORE_EVENT_BUS` | legacy AfterReasoning 事件前提取 cited ID |
| v3 `AFTER_REASONING_BEFORE_PERSIST` | 持久化前清理残留协议标签 |

插件通过模块命名导出 `api_version = 3` 与 `apply(ctx, config)` 注册这些 listener，并提供 `citation.protocol` Service 给依赖引用协议顺序的插件。旧 `CitationPlugin` 与 phase module 暂时保留，只用于迁移期行为等价验证；新 Core 不再从固定 PluginManager 列表装配 Citation。

---

## 运作逻辑

### 1. 注入引用协议（CitationPromptModule）

每轮推理前，在系统 prompt 底部追加一段隐藏指令（`_CITATION_PROTOCOL`），要求 LLM 在用到记忆条目时，在回复末尾输出 `§cited:[id1,id2]§` 格式的引用行，且不向用户暴露这行的存在。

### 2. 提取 cited ID（CitationAfterReasoningModule）

推理完成后，用正则扫描 `reply` 尾部，匹配 `§cited:[...]§` 标签：

- 若匹配成功，提取 ID 列表，v3 写入 `AfterReasoningCtx.persist_assistant_metadata["cited_memory_ids"]`，并把标签从 reply 中剥除。
- 若 reply 里没有引用行，fallback 到工具调用链：扫描 `recall_memory` 工具的返回结果，从 JSON 里取出 `cited_item_ids` 或 `items[].id`，作为本轮引用 ID。

提取到的 ID 由下游持久化模块写入数据库，用于更新记忆条目的被引用计数和时间戳。

### 3. 清理协议标签（ProtocolTagCleanupModule）

在 persist 之前再做一次扫描，用正则清除 reply 末尾所有残留的 `<tag:value>` 形式协议标签（包括其他插件可能留下的），保证对外输出的文本干净。

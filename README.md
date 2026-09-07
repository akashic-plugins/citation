# citation 插件

记忆引用追踪协议。插件向 Core 的普通 Content 服务注册提示与文本解析器，清理回复中的引用标记，并把引用依据保存到 `Message.metadata.citation`。

## 行为

- 提示模型在使用记忆后输出 `§cited:[id1,id2]§`。
- 只解析非 Markdown 代码区间中的自有引用标记，不清理其他插件的协议。
- 模型声明的引用记录为 `declared=true`。
- 调用方提供的真实召回 `Reference` 可补充 `retrieval_ref` 和 `resolved_ref`。
- 没有显式声明时，只把带真实 `retrieval_ref` 的候选记录为 `declared=false`。
- 未解析的模型引用保留原始 `ref`，不会伪造来源。

插件不读取工具调用名，也不依赖其他内容协议。Content 在同一 generation lease 中组合正文与 metadata，并随 Message 原子提交。

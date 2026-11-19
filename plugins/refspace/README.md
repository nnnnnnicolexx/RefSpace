# 引知 RefSpace（Antigravity 插件）

本插件将 **引知 RefSpace** 接入 Antigravity 工作台，包含：

- **MCP 服务（`refspace`）**：提供资料空间绑定、范围状态查询、多格式资料定位、文档按需切片与 Excel/CSV 确定性计算 5 个原语。
- **生命周期钩子（`hooks.json`）**：
  - `PreInvocation`：在已绑定资料空间的会话中轻量注入当前会话默认空间状态；
  - `PreToolUse`：自动注入宿主真实的 `conversationId`，确保多窗口物理隔离、零串用；
  - `Stop`：单轮对话结束时自动清除“这次只用 XXX”临时覆盖，恢复原会话默认空间。
- **会话引用规则（`rules/AGENTS.md`）**：统一约束 7 类范围切换意图、数据防注入与结果来源引用格式。

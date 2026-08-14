# MCP 工具提供者 Spec

## Why

SAgent 当前仅支持内置本地工具（文件读写、Shell、记忆），通过预留的 `ToolProvider` 接口可扩展接入外部工具源。MCP（Model Context Protocol）作为业界标准协议，能以统一方式连接各类外部工具服务器（文件系统、GitHub、数据库、搜索等）。本次变更通过实现 `ToolProvider` 接口添加 MCP 调用能力，并提供工具级（白名单/黑名单）与服务器级（启用/禁用）双层过滤控制，使 Agent 能安全、可控地使用外部 MCP 工具。

设计参考业界主流 Agent MCP 最佳实践（OpenAI Agents SDK、mcp-filter、MCP 官方 Client Best Practices）：
- **工具过滤**：静态白名单/黑名单，减少无关工具定义注入上下文造成的 token 浪费
- **服务器过滤**：禁用不信任或不需要的服务器，不建立连接
- **工具名前缀**：`mcp_{server}_{tool}` 强制添加，防止跨服务器命名冲突，且从工具名可辨识为 MCP 来源
- **连接错误隔离**：单个服务器连接失败不影响其他服务器
- **异步同步桥接**：MCP SDK 为异步，项目为同步，通过后台事件循环线程桥接

## What Changes

- 新增 `tools/mcp/` 子包，实现 MCP 客户端集成
- 实现 `MCPToolProvider`（继承 `ToolProvider`）：连接服务器 -> 发现工具 -> 过滤 -> 返回 MCPTool 列表
- 实现 `MCPTool`（继承 `Tool`）：将单个 MCP 工具包装为本地工具，覆写 schema 生成与参数校验以适配 MCP 的原生 JSON schema
- 实现 `MCPSessionManager`：后台事件循环线程桥接异步 MCP SDK 与同步项目代码，管理持久化会话生命周期
- 实现 `ToolFilter`：通过 `allow`/`deny` 两个列表字段控制白名单/黑名单过滤逻辑
- 新增配置模型 `MCPConfig` / `MCPServerConfig` / `ToolFilterConfig`，支持多服务器、多传输方式（stdio / sse / streamable_http）
- 服务器级过滤：每个服务器可设 `enabled: false` 跳过连接
- 工具名前缀：强制 `mcp_{server_name}_{tool_name}` 格式，不可关闭
- 连接错误隔离：单个服务器连接失败记录日志并跳过
- 超时控制：每个服务器独立配置 `connect_timeout`（连接超时，默认 30 秒）与 `call_timeout`（工具调用超时，默认 60 秒），防止挂起
- 优雅关闭：退出时关闭所有 MCP 会话与子进程
- 集成结构化日志：MCP 连接、工具发现、工具调用均记录 JSON 日志
- 更新 `config.example.yaml` 增加 MCP 配置示例
- 更新 `AGENTS.md` 文档新增 MCP 模块说明

## Impact

- **Affected specs**: 工具系统（ToolProvider 接口从预留变为实现）、配置系统（新增 MCPConfig）
- **Affected code**:
  - `src/sagent/config/models.py`：新增 MCP 配置模型，AppConfig 新增 `mcp` 字段
  - `src/sagent/tools/mcp/`（新建子包）：`filtering.py`、`session_manager.py`、`tool.py`、`provider.py`、`__init__.py`
  - `src/sagent/cli/app.py`：启动时构建 MCP 提供者并注册，退出时关闭会话
  - `config.example.yaml`：新增 mcp 配置段
  - `AGENTS.md`：新增 MCP 模块文档
  - `tests/unit/`：新增 MCP 过滤、配置、工具包装的单元测试

## ADDED Requirements

### Requirement: MCP 工具提供者

系统 SHALL 通过实现 `ToolProvider` 接口的 `MCPToolProvider` 连接 MCP 服务器，发现服务器暴露的工具，经工具过滤后将通过过滤的工具包装为 `MCPTool` 实例返回，由 `ToolRegistry.register_provider()` 注册到工具表，使 LLM 可通过 function calling 调用 MCP 工具。

#### Scenario: 成功连接并注册工具

- **WHEN** 配置中启用了 MCP（`mcp.enabled = true`）且某服务器配置有效且 `enabled = true`
- **THEN** 系统在启动时连接该服务器，调用 `list_tools` 发现其工具列表
- **AND** 经工具过滤后将通过过滤的工具包装为 MCPTool 注册到工具表
- **AND** 启动后 Agent 可通过 function calling 调用这些 MCP 工具

#### Scenario: 连接失败隔离

- **WHEN** 某个 MCP 服务器连接失败（如命令不存在、网络不通、握手超时）
- **THEN** 系统记录错误日志（`event: mcp_connect_error`）并跳过该服务器
- **AND** 其他服务器的工具正常注册，Agent 不受影响

### Requirement: MCP 工具过滤

系统 SHALL 支持对每个 MCP 服务器的工具进行白名单或黑名单过滤，控制哪些工具暴露给 LLM。过滤逻辑由独立的 `ToolFilter` 类实现，不依赖 MCP SDK，可单独单元测试。

过滤语义（通过 `allow` 和 `deny` 两个列表字段控制，无需 mode 参数）：
- `allow` 与 `deny` 均为空 -> 不过滤，所有工具通过
- `allow` 非空 -> 只有 `allow` 列表中的工具通过（白名单）
- `deny` 非空 -> `deny` 列表中的工具被拦截，其余通过（黑名单）
- `allow` 与 `deny` 均非空 -> 先白名单过滤，再从结果中移除 `deny` 中的工具（交集后再排除）

#### Scenario: 白名单过滤

- **WHEN** 服务器配置 `tool_filter.allow = ["tool_a", "tool_b"]` 且 `tool_filter.deny` 为空
- **THEN** 只有 tool_a 和 tool_b 被注册到工具表
- **AND** 服务器暴露的其他工具被过滤掉

#### Scenario: 黑名单过滤

- **WHEN** 服务器配置 `tool_filter.deny = ["dangerous_tool"]` 且 `tool_filter.allow` 为空
- **THEN** dangerous_tool 被过滤掉
- **AND** 服务器暴露的其他工具正常注册

#### Scenario: 无过滤配置

- **WHEN** 服务器未配置 tool_filter 或 `allow` 与 `deny` 均为空
- **THEN** 服务器暴露的所有工具均被注册（无过滤）

### Requirement: MCP 服务器过滤

系统 SHALL 支持通过 `enabled` 字段控制是否连接某个 MCP 服务器，禁用的服务器不会被连接。

#### Scenario: 禁用服务器

- **WHEN** 服务器配置 `enabled = false`
- **THEN** 系统不连接该服务器
- **AND** 该服务器的工具不会出现在工具表中

#### Scenario: 启用服务器

- **WHEN** 服务器配置 `enabled = true`（默认）
- **THEN** 系统正常连接并注册该服务器的工具

### Requirement: 多传输方式支持

系统 SHALL 支持 stdio、SSE、Streamable HTTP 三种 MCP 传输方式，通过 `transport` 字段配置。

#### Scenario: stdio 传输

- **WHEN** 服务器配置 `transport = "stdio"`，提供 `command` 和 `args`
- **THEN** 系统通过启动子进程并经 stdin/stdout 通信连接
- **AND** 可选配置 `env`（环境变量）和 `cwd`（工作目录）

#### Scenario: SSE 传输

- **WHEN** 服务器配置 `transport = "sse"`，提供 `url`
- **THEN** 系统通过 SSE（Server-Sent Events）连接远程服务器

#### Scenario: Streamable HTTP 传输

- **WHEN** 服务器配置 `transport = "streamable_http"`，提供 `url`
- **THEN** 系统通过 Streamable HTTP 连接远程服务器

### Requirement: 工具名前缀

系统 SHALL 给所有 MCP 工具名添加前缀（格式 `mcp_{server_name}_{tool_name}`），防止不同服务器的同名工具冲突，并从工具名即可辨识为 MCP 来源工具。前缀为强制行为，不可关闭。

#### Scenario: 前缀格式

- **WHEN** 服务器名为 "filesystem" 且该服务器暴露 "read_file" 工具
- **THEN** 该工具注册到工具表时名称为 "mcp_filesystem_read_file"

### Requirement: 异步同步桥接

系统 SHALL 通过后台事件循环线程桥接异步 MCP SDK 与同步项目代码，保持项目同步调用风格不变。`MCPSessionManager` 负责管理后台事件循环、持久化会话与同步调用包装。

#### Scenario: 启动时连接

- **WHEN** MCP 启用且配置了服务器
- **THEN** 系统启动后台事件循环线程，在循环中创建传输连接与 ClientSession，执行 initialize 握手，保持会话存活

#### Scenario: 工具调用

- **WHEN** LLM 通过 function calling 调用 MCP 工具
- **THEN** `ToolRegistry.execute()` 同步调用 `MCPTool.run()`
- **AND** `MCPTool.run()` 通过 `MCPSessionManager.call_tool()` 将异步调用提交到后台事件循环并同步等待结果

#### Scenario: 连接超时

- **WHEN** MCP 服务器连接（含握手）超过该服务器配置的 `connect_timeout` 秒（默认 30 秒）
- **THEN** 记录 `event: mcp_connect_error` 错误日志并跳过该服务器
- **AND** 其他服务器不受影响

#### Scenario: 工具调用超时

- **WHEN** MCP 工具调用超过该服务器配置的 `call_timeout` 秒（默认 60 秒）
- **THEN** 返回以 "错误:" 开头的超时提示字符串，不阻断 Agent 流程

### Requirement: MCP 工具结果处理

系统 SHALL 将 MCP 工具调用的返回结果提取为文本字符串，兼容项目 `Tool.run() -> str` 接口约定。

#### Scenario: 文本结果

- **WHEN** MCP 工具返回包含 text 类型的 content
- **THEN** 提取所有 text content 的 `.text` 字段并拼接为单个字符串返回

#### Scenario: 非文本结果

- **WHEN** MCP 工具返回包含 image 等非 text 类型的 content
- **THEN** 跳过非文本内容，仅返回文本部分；无文本内容时返回占位提示

### Requirement: 优雅关闭

系统 SHALL 在 CLI 退出时关闭所有 MCP 会话与子进程，避免资源泄露（特别是 stdio 模式的子进程）。

#### Scenario: 正常退出

- **WHEN** 用户输入 exit 或发生 EOFError
- **THEN** 系统调用 `MCPSessionManager.shutdown()` 关闭所有会话上下文并停止后台事件循环
- **AND** stdio 子进程被正确终止

### Requirement: 结构化日志

系统 SHALL 对 MCP 操作记录结构化 JSON 日志，与现有日志体系一致。

#### Scenario: 连接日志

- **WHEN** 系统连接 MCP 服务器
- **THEN** 记录 `event: mcp_connect_start`（含服务器名、传输方式）与 `event: mcp_connect_done`（含发现的工具数量）

#### Scenario: 工具调用日志

- **WHEN** LLM 调用 MCP 工具
- **THEN** 记录 `event: mcp_tool_call`（含服务器名、工具名、参数）与 `event: mcp_tool_result`（含结果长度、延迟）

## MODIFIED Requirements

### Requirement: AppConfig

AppConfig 新增 `mcp` 字段，类型为 `MCPConfig`，缺省可用（`enabled` 默认为 `false`，不影响现有行为）。现有配置文件不含 `mcp` 段时使用默认值。

### Requirement: ToolProvider 接口

`ToolProvider` 接口保持 `provide_tools() -> list[Tool]` 不变。`MCPToolProvider` 实现该接口，在 `provide_tools()` 中完成连接、发现、过滤并返回 MCPTool 列表。会话生命周期管理（关闭）由 `MCPSessionManager` 独立负责，不通过 ToolProvider 接口。

### Requirement: tools/__init__.py

`build_default_registry()` 行为不变。新增 `build_mcp_providers(config, session_manager)` 工厂函数，由 CLI 在 MCP 启用时调用，返回 `MCPToolProvider` 列表供 `registry.register_provider()` 批量注册。

## Future Enhancements（本次不实现）

以下能力基于业界最佳实践推荐，但本次不实现，留作后续迭代：

- **渐进式工具发现（Progressive Tool Discovery）**：当工具定义占上下文窗口 1%-5% 以上时，不预加载全部工具定义，改为提供 `search_tools` 元工具按需加载。适用于连接大量 MCP 服务器的场景。
- **人工审批（Human-in-the-loop Approval）**：对敏感 MCP 工具调用要求用户确认。
- **MCP 资源与提示词**：MCP 除工具外还支持 resources（数据）和 prompts（模板），当前仅接入工具。
- **动态服务器管理**：通过斜杠命令在运行时添加/移除 MCP 服务器。
- **工具结果图片处理**：当前仅提取文本，未来可支持 image content 传递给多模态模型。

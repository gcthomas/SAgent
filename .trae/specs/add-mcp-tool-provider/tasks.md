# Tasks

- [x] Task 1: 新增 MCP 配置模型
  - [x] SubTask 1.1: 在 `src/sagent/config/models.py` 新增 `ToolFilterConfig`（allow: list[str], deny: list[str]，均默认空列表）
  - [x] SubTask 1.2: 在 `src/sagent/config/models.py` 新增 `MCPServerConfig`（name, transport, command, args, env, cwd, url, enabled, tool_filter, connect_timeout, call_timeout）
  - [x] SubTask 1.3: 在 `src/sagent/config/models.py` 新增 `MCPConfig`（enabled, servers）
  - [x] SubTask 1.4: 在 `AppConfig` 中新增 `mcp: MCPConfig` 字段（default_factory，缺省可用）

- [x] Task 2: 实现 ToolFilter 过滤逻辑
  - [x] SubTask 2.1: 创建 `src/sagent/tools/mcp/__init__.py`（空包初始化，后续补充公开接口）
  - [x] SubTask 2.2: 创建 `src/sagent/tools/mcp/filtering.py`，实现 `ToolFilter` 类
  - [x] SubTask 2.3: 实现 `should_include(tool_name) -> bool` 方法，遵循 allow/deny 语义（均空不过滤、allow 非空白名单、deny 非空黑名单、两者均非空先白名单再排除）
  - [x] SubTask 2.4: 实现 `filter_tools(tool_names) -> list[str]` 批量过滤方法

- [x] Task 3: 实现 MCPSessionManager 异步同步桥接
  - [x] SubTask 3.1: 创建 `src/sagent/tools/mcp/session_manager.py`，实现 `MCPSessionManager` 类
  - [x] SubTask 3.2: 实现 `start()` 启动后台事件循环线程（daemon 线程 + asyncio.new_event_loop）
  - [x] SubTask 3.3: 实现 `connect_server(config: MCPServerConfig) -> list` 异步连接（按 transport 分发 stdio/sse/streamable_http），返回 MCP Tool 对象列表
  - [x] SubTask 3.4: 实现连接上下文管理（保持传输层与 ClientSession 上下文存活，存入 _contexts 列表）
  - [x] SubTask 3.5: 实现 `call_tool(server_name, tool_name, arguments) -> str` 同步包装，通过 run_coroutine_threadsafe 提交到后台循环
  - [x] SubTask 3.6: 实现工具结果提取（从 CallToolResult.content 提取 text，跳过非文本，无文本返回占位提示）
  - [x] SubTask 3.7: 实现 `shutdown()` 关闭所有会话上下文并停止后台循环
  - [x] SubTask 3.8: 实现超时处理（connect_server 等待超时使用 config.connect_timeout，call_tool 等待超时使用 config.call_timeout，超时返回错误字符串）与结构化日志（mcp_connect_start/done/error, mcp_tool_call/result）

- [x] Task 4: 实现 MCPTool 工具包装
  - [x] SubTask 4.1: 创建 `src/sagent/tools/mcp/tool.py`，实现 `MCPTool` 类（继承 `Tool`）
  - [x] SubTask 4.2: 在 `__init__` 中接收 tool_name, description, input_schema（原始 JSON schema）, session_manager, server_name
  - [x] SubTask 4.3: 覆写 `to_openai_schema()` 直接使用原始 JSON schema 生成 function schema
  - [x] SubTask 4.4: 覆写 `validate_args()` 直接返回原始 dict（MCP 服务端负责校验）
  - [x] SubTask 4.5: 实现 `run(args)` 委托 session_manager.call_tool() 执行，返回结果字符串

- [x] Task 5: 实现 MCPToolProvider 提供者
  - [x] SubTask 5.1: 创建 `src/sagent/tools/mcp/provider.py`，实现 `MCPToolProvider` 类（继承 `ToolProvider`）
  - [x] SubTask 5.2: 在 `__init__` 中接收 MCPServerConfig, MCPSessionManager
  - [x] SubTask 5.3: 实现 `provide_tools() -> list[Tool]`：调用 session_manager.connect_server 连接并发现工具 -> 用 ToolFilter 过滤 -> 为每个通过过滤的工具创建 MCPTool（强制添加 `mcp_{server}_{tool}` 名称前缀）-> 返回列表

- [x] Task 6: 实现包公开接口与工厂函数
  - [x] SubTask 6.1: 在 `src/sagent/tools/mcp/__init__.py` 中导出 MCPToolProvider, MCPTool, MCPSessionManager, ToolFilter
  - [x] SubTask 6.2: 实现 `build_mcp_providers(config: MCPConfig, session_manager: MCPSessionManager) -> list[MCPToolProvider]`：遍历 enabled 服务器创建 provider 列表

- [x] Task 7: CLI 集成
  - [x] SubTask 7.1: 在 `src/sagent/cli/app.py` 中，MCP 启用时创建 MCPSessionManager 并 start()
  - [x] SubTask 7.2: 调用 build_mcp_providers 获取 provider 列表，逐个 registry.register_provider()
  - [x] SubTask 7.3: 启动横幅中打印 MCP 服务器与工具信息
  - [x] SubTask 7.4: 在退出路径（exit/EOFError/异常）中调用 session_manager.shutdown()

- [x] Task 8: 更新配置示例
  - [x] SubTask 8.1: 在 `config.example.yaml` 末尾新增 mcp 配置段，含 stdio/sse/streamable_http 三种服务器示例与工具过滤示例

- [x] Task 9: 单元测试
  - [x] SubTask 9.1: 创建 `tests/unit/test_mcp_filtering.py`：测试 allow 白名单/deny 黑名单/两者均空无过滤/两者均非空先白名单再排除 各种场景
  - [x] SubTask 9.2: 创建 `tests/unit/test_mcp_config.py`：测试 MCPConfig/MCPServerConfig/ToolFilterConfig 默认值与校验（含 connect_timeout/call_timeout 默认值）
  - [x] SubTask 9.3: 创建 `tests/unit/test_mcp_tool.py`：测试 MCPTool 的 to_openai_schema、validate_args、run（用 fake session_manager）、工具名前缀格式 mcp_{server}_{tool}
  - [x] SubTask 9.4: 创建 `tests/unit/test_mcp_provider.py`：测试 MCPToolProvider.provide_tools 的过滤逻辑与名称前缀（用 fake session_manager 返回预设工具列表）

- [x] Task 10: 更新项目文档
  - [x] SubTask 10.1: 在 `AGENTS.md` 的项目结构中新增 tools/mcp/ 子包说明
  - [x] SubTask 10.2: 在 `AGENTS.md` 的重要模块职责中新增 MCP 各模块说明
  - [x] SubTask 10.3: 在 `AGENTS.md` 的配置模型说明中新增 MCPConfig 描述
  - [x] SubTask 10.4: 在 `AGENTS.md` 的扩展说明中更新 MCP 接入从"预留接口"改为"已实现"

# 验证阶段发现的问题（已修复）

- [x] Task 11: 修复 CLI 集成缺失导入
  - [x] SubTask 11.1: 在 `src/sagent/cli/app.py` 顶部新增 `from ..tools.mcp import MCPSessionManager, build_mcp_providers` 导入语句
  - [x] SubTask 11.2: 验证 `python -c "from sagent.cli.app import run; print('CLI OK')"` 仍通过

- [x] Task 12: 修复 CLI 启动横幅缺失 MCP 信息
  - [x] SubTask 12.1: 在启动横幅中打印 MCP 服务器与工具信息

- [x] Task 13: 修复 CLI exit 退出路径缺失 MCP shutdown
  - [x] SubTask 13.1: 在 exit 命令分支中调用 `mcp_session_manager.shutdown()`

- [x] Task 14: 新增 MCPServerConfig 传输方式必填字段校验
  - [x] SubTask 14.1: 添加 model_validator 校验 transport=stdio 时 command 非空、transport=sse/streamable_http 时 url 非空

- [~] Task 15: 修复 memory/security.py 预存 bug（非 MCP 范围，不在本次 spec 范围内处理）

# Task Dependencies

- [Task 2] 无依赖，可与 Task 1 并行
- [Task 3] 依赖 [Task 1]（需要 MCPServerConfig）
- [Task 4] 依赖 [Task 1]（需要配置）和 [Task 3]（需要 session_manager）
- [Task 5] 依赖 [Task 2]（ToolFilter）、[Task 3]（session_manager）、[Task 4]（MCPTool）
- [Task 6] 依赖 [Task 4] 和 [Task 5]
- [Task 7] 依赖 [Task 6]
- [Task 8] 依赖 [Task 1]
- [Task 9] 依赖 [Task 1, 2, 4, 5]，各子任务可与对应实现并行或在其后
- [Task 10] 依赖全部实现完成

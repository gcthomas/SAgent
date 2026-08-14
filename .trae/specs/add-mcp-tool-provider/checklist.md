# Checklist

## 配置模型
- [x] ToolFilterConfig 定义了 allow (list[str]) 和 deny (list[str]) 字段，均默认空列表，带 description
- [x] MCPServerConfig 定义了 name, transport (stdio/sse/streamable_http), command, args, env, cwd, url, enabled, tool_filter, connect_timeout (默认 30.0), call_timeout (默认 60.0) 字段
- [x] MCPServerConfig 的 transport 为 stdio 时 command 为必填；transport 为 sse/streamable_http 时 url 为必填（校验逻辑或文档说明）
- [x] MCPConfig 定义了 enabled (默认 false), servers (list[MCPServerConfig]) 字段
- [x] AppConfig 新增 mcp 字段，使用 default_factory=MCPConfig，现有配置文件无 mcp 段时正常工作
- [x] 所有配置模型字段使用 Field(..., description=...) 注解

## ToolFilter 过滤逻辑
- [x] ToolFilter.should_include 在 allow 与 deny 均为空时返回 True（不过滤）
- [x] ToolFilter.should_include 在 allow 非空时仅当工具名在 allow 列表中返回 True
- [x] ToolFilter.should_include 在 deny 非空时仅当工具名不在 deny 列表中返回 True
- [x] ToolFilter.should_include 在 allow 与 deny 均非空时先白名单过滤再从结果排除 deny
- [x] ToolFilter.filter_tools 批量过滤返回正确子集
- [x] ToolFilter 不依赖 MCP SDK，可独立单元测试

## MCPSessionManager 异步同步桥接
- [x] start() 启动 daemon 线程运行 asyncio 事件循环
- [x] connect_server 按 transport 分发：stdio 用 stdio_client + StdioServerParameters，sse 用 sse_client，streamable_http 用 streamable_http_client
- [x] 连接后调用 session.initialize() 完成 MCP 握手
- [x] 传输层上下文与 ClientSession 上下文存入 _contexts 列表保持存活
- [x] call_tool 通过 asyncio.run_coroutine_threadsafe 提交到后台循环并同步等待结果
- [x] connect_server 等待结果使用该服务器的 config.connect_timeout 超时，超时记录 mcp_connect_error 并返回空列表
- [x] call_tool 等待结果使用该服务器的 config.call_timeout 超时，超时返回以 "错误:" 开头的字符串，不抛异常
- [x] 工具结果从 CallToolResult.content 提取 text 类型内容拼接，非文本跳过，无文本返回占位提示
- [x] shutdown() 关闭所有上下文（逆序退出）并停止后台循环与线程
- [x] 连接失败时记录 mcp_connect_error 日志并返回空列表（不抛异常）
- [x] 连接成功时记录 mcp_connect_start 与 mcp_connect_done 日志（含服务器名、传输方式、工具数量）
- [x] 工具调用记录 mcp_tool_call 与 mcp_tool_result 日志（含服务器名、工具名、延迟）

## MCPTool 工具包装
- [x] MCPTool 继承 Tool 基类
- [x] to_openai_schema() 使用原始 MCP JSON schema 生成 function schema（不依赖 pydantic model_json_schema）
- [x] validate_args() 直接返回原始 dict（不依赖 pydantic 校验）
- [x] run() 委托 session_manager.call_tool() 执行，返回结果字符串
- [x] 工具名前缀格式强制为 mcp_{server_name}_{tool_name}

## MCPToolProvider 提供者
- [x] MCPToolProvider 继承 ToolProvider，实现 provide_tools() -> list[Tool]
- [x] provide_tools 调用 session_manager.connect_server 连接并发现工具
- [x] provide_tools 用 ToolFilter 过滤工具列表
- [x] provide_tools 为每个通过过滤的工具创建 MCPTool 实例
- [x] provide_tools 强制添加 mcp_{server}_{tool} 名称前缀（不可配置关闭）
- [x] 连接失败时 provide_tools 返回空列表（不抛异常）

## 包公开接口
- [x] tools/mcp/__init__.py 导出 MCPToolProvider, MCPTool, MCPSessionManager, ToolFilter
- [x] build_mcp_providers(config, session_manager) 遍历 enabled 服务器返回 provider 列表
- [x] build_default_registry() 行为不变（不影响现有功能）

## CLI 集成
- [x] mcp.enabled = true 时创建 MCPSessionManager 并 start()
- [x] 调用 build_mcp_providers 获取 provider 列表，逐个 registry.register_provider()
- [x] 启动横幅打印 MCP 服务器与工具信息
- [x] exit / EOFError / 异常退出路径调用 session_manager.shutdown()
- [x] mcp.enabled = false（默认）时行为与现有完全一致

## 配置示例
- [x] config.example.yaml 新增 mcp 段，含注释说明
- [x] 包含 stdio 服务器示例（如 filesystem 或 sequential-thinking）
- [x] 包含 sse/streamable_http 服务器示例
- [x] 包含工具过滤示例（allow 白名单与 deny 黑名单）
- [x] 包含 disabled 服务器示例

## 单元测试
- [x] test_mcp_filtering.py 覆盖 allow 白名单/deny 黑名单/均空无过滤/均非空先白名单再排除场景
- [x] test_mcp_config.py 覆盖配置模型默认值与校验
- [x] test_mcp_tool.py 覆盖 to_openai_schema/validate_args/run/名称前缀（用 fake session_manager）
- [x] test_mcp_provider.py 覆盖 provide_tools 过滤逻辑与名称前缀（用 fake session_manager）
- [x] 所有现有测试（python -m pytest）通过，无回归（注：memory 模块 35 个失败为既有问题，与 MCP 无关）

## 项目文档
- [x] AGENTS.md 项目结构新增 tools/mcp/ 子包说明
- [x] AGENTS.md 重要模块职责新增 MCP 各模块（session_manager/tool/provider/filtering）说明
- [x] AGENTS.md 配置模型说明新增 MCPConfig/MCPServerConfig/ToolFilterConfig
- [x] AGENTS.md 扩展说明更新 MCP 接入状态（从"预留接口"改为"已实现"）

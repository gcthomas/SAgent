# AGENTS.md

本文件为 AI Agent（以及开发者）提供 SAgent 项目的操作指南。修改代码前请先阅读本文件。

## 项目概述

**SAgent** 是一个简单、通用、可扩展的 CLI Agent 应用。基于 OpenAI SDK 调用大模型，支持 ReAct 与 Plan 两种执行模式，内置文件读写与 Shell 执行工具，并通过 MCP 协议连接外部工具服务器扩展 Agent 能力。

- **项目类型**：CLI 工具 / Python 应用
- **核心功能**：命令行交互式 Agent，支持工具调用与多轮编排
- **技术栈**：Python 3.10+、openai SDK、pydantic v2、pyyaml、mcp SDK、pytest、sqlite3（标准库）
- **架构说明**：
  - 两种执行模式：ReAct（推理-行动-观察循环）、Plan（拆解-逐步执行-汇总）
  - 工具系统基于 pydantic 定义参数 schema，对接 LLM function calling
  - 上下文管理：自动追踪 token 用量，超阈值时分层压缩（截断→卸载→摘要→裁剪），支持 API 精确 token 校准
  - 会话管理：会话历史持久化到 SQLite（FTS5 全文检索），双层存储模型（append-only 完整历史 + 压缩事件日志），增量保存，斜杠命令交互
  - 长期记忆：基于两个 Markdown 文件（USER.md / MEMORY.md）的跨会话记忆，LLM 通过记忆工具自主读写，写入前自动安全扫描（凭证泄露/Prompt 注入/Shell 后门/不可见字符），写入超限时自动反思整理，会话开始前注入冻结前缀
  - 兼容任意 OpenAI 兼容的 LLM 服务（通过 base_url 指定）
  - 配置集中在 YAML 文件，敏感项支持环境变量覆盖
  - 内置 JSON 结构化日志，按天滚动，带 trace_id 全链路关联

## 开发命令

所有命令在 PowerShell 中执行，工作目录为项目根目录（即包含 main.py 的目录）。

### 环境准备

```powershell
# 安装运行时依赖
pip install -r requirements.txt

# 安装开发与测试依赖（包含 pytest、pytest-cov）
pip install -r requirements-dev.txt

# 复制示例配置并填写
Copy-Item config.example.yaml config.yaml
```

### 运行

```powershell
# 使用配置文件默认模式
python main.py

# 指定 ReAct 模式
python main.py --mode react

# 指定 Plan 模式与自定义配置文件
python main.py --config config.yaml --mode plan
```

### 测试

```powershell
# 运行全部离线用例（默认，不依赖真实 LLM）
python -m pytest

# 运行指定目录的测试
python -m pytest tests/unit
python -m pytest tests/engines

# 启用真实 LLM 评测（需配置好 LLM_API_KEY / LLM_API_URL 与 config.yaml）
$env:RUN_LLM_EVALS = "1"
python -m pytest tests/evals

# 查看测试覆盖率
python -m pytest --cov=sagent --cov-report=term-missing
```

### 检查

项目未配置 lint 工具，但需遵守以下约定（见"代码规范"章节）。**关键**：修改后务必运行 `python -m pytest` 确认全部用例通过。

## 项目结构

```
main.py                     程序入口（将 src 加入 sys.path 后调用 CLI）
config.example.yaml         配置示例（config.yaml 为实际使用，已被 gitignore）
requirements.txt            运行时依赖
requirements-dev.txt        开发与测试依赖
pytest.ini                  pytest 配置（pythonpath=src，testpaths=tests）
src/sagent/
  cli/app.py                CLI 应用：参数解析、交互循环、引擎构建、斜杠命令分发、会话集成
  cli/commands.py           斜杠命令纯函数解析器 parse_command（与命令族无关，非斜杠输入返回 None）
  config/
    models.py               配置模型（pydantic）：LLMConfig、AgentConfig、LoggingConfig、ContextConfig、SessionConfig、AppConfig
    loader.py               YAML 配置加载 + 环境变量覆盖 + 校验
  llm/client.py             LLM 客户端：封装 openai SDK，统一 LLMResponse 结构（含 usage 字段）
  core/
    react_engine.py         ReAct 引擎：推理-工具调用-观察循环（集成上下文管理）
    plan_engine.py          Plan 引擎：拆解-逐步执行(复用 ReAct)-汇总
    prompts.py              系统提示词定义
  context/
    token_counter.py        Token 估算器：tiktoken 精确 / 字符启发式 / auto 自动回退
    strategies.py           分层压缩策略：截断、卸载、摘要、裁剪
    context_manager.py      上下文管理器：消息维护、token 追踪、触发压缩、混合校准、seq 维护与增量导出
    prompts.py              摘要压缩系统提示词
  session/
    models.py               会话数据模型：SessionMeta、SessionMessage、CompactionEvent
    store.py                会话持久化存储：SQLite + FTS5，双层表结构（append-only 消息 + 压缩事件）
    manager.py              会话管理器：创建/切换/删除/增量保存/工作上下文还原，桥接 ContextManager
  memory/
    store.py                Markdown 文件记忆存储：USER.md / MEMORY.md 的加载/读取/追加/替换/删除/原子落盘/字符上限检查
    manager.py              记忆管理器：会话前缀注入构造、写入前安全扫描把关、写入超限反思整理，桥接 store 与 LLM
    security.py             记忆安全扫描器：写入前检测凭证泄露、Shell 后门、Prompt 注入、净化不可见字符（默认启用、不可关闭）
    prompts.py              记忆提示词：使用引导（MEMORY_GUIDE_PROMPT）、反思整理（REFLECT_SYSTEM_PROMPT）、注入前缀构造
  tools/
    base.py                 Tool 抽象基类 + ToolProvider 接口（已实现 MCP 接入）
    registry.py             ToolRegistry：注册、schema 输出、按名称执行
    file_tools.py           内置工具：read_file、write_file
    shell_tool.py           内置工具：run_shell
    memory_tool.py          内置工具：add_memory、replace_memory、remove_memory（长期记忆读写）
    __init__.py             build_default_registry() 构建默认工具集；build_mcp_providers() 构建 MCP 提供者列表
    mcp/                    MCP 工具提供者子包
      filtering.py          ToolFilter：allow/deny 白名单/黑名单过滤
      session_manager.py    MCPSessionManager：后台事件循环线程桥接异步 MCP SDK，管理会话生命周期
      tool.py               MCPTool：将 MCP 工具包装为本地 Tool，覆写 schema 生成与参数校验
      provider.py           MCPToolProvider：实现 ToolProvider，连接->发现->过滤->返回 MCPTool 列表
      __init__.py           包公开接口与 build_mcp_providers() 工厂函数
  observability/
    logging_setup.py        JSON 结构化日志、按天滚动、trace_id 上下文关联
tests/
  conftest.py               公共 fixture：FakeLLMClient、make_tool_call、text_response（支持 usage）
  unit/                     单元测试（配置、工具、注册表、Plan 解析、上下文管理、会话存储与管理、记忆存储与管理、记忆安全扫描、记忆工具、斜杠命令解析，无需 LLM）
  engines/                  引擎测试（ReAct / Plan，用 FakeLLMClient 离线回放）
  evals/                    Agent 能力评测（离线回放 + 可选真实 LLM）
```

### 重要模块职责

- **`src/sagent/core/react_engine.py`**：ReAct 执行引擎，核心循环逻辑。`run()` 方法是主入口，调用 LLM → 判断是否工具调用 → 执行工具 → 追加观察 → 继续，直到得到最终答案或达到 `max_iterations` 上限。
- **`src/sagent/core/plan_engine.py`**：Plan 执行引擎。先调用 LLM 拆解任务为 JSON 步骤列表，每步复用 ReAct 引擎执行，最后汇总。
- **`src/sagent/tools/registry.py`**：工具注册表。**关键**：`execute()` 对未知工具、参数错误、执行异常均做容错处理，返回以"错误:"开头的字符串而不抛异常（避免 Agent 流程中断）。
- **`src/sagent/llm/client.py`**：LLM 客户端。封装 openai SDK，统一返回 `LLMResponse`（含 content、tool_calls、usage）。记录请求/响应日志，默认仅摘要。`usage` 字段暴露 API 返回的 `prompt_tokens` / `completion_tokens` / `total_tokens`，供上下文管理器做混合校准。
- **`src/sagent/context/context_manager.py`**：上下文管理器。维护消息历史，自动追踪 token 用量，超阈值时触发分层压缩。关键方法：`add_message()` 添加消息（对 tool 结果立即内联截断）、`get_messages()` 获取消息（超阈值自动压缩）、`record_llm_usage()` 记录 API 返回的精确 token 数用于混合校准、`token_count` 属性返回当前估算 token 数（混合校准模式 = 精确基准 + delta）。压缩触发时重置校准基准。会话集成扩展：维护单调递增 `seq` 序号、`export_new_messages(after_seq)` 增量导出新消息、`load_messages()` 替换消息并重建 seq/重置校准、`set_compaction_callback()` 注册压缩回调（摘要文本 + 覆盖 seq 区间）供会话管理器记录压缩事件。
- **`src/sagent/context/strategies.py`**：分层压缩策略。四层依次为：ToolOutputTruncation（截断过长工具输出）、ToolMessageOffload（卸载过期工具消息对）、LLMSummaryCompression（LLM 生成摘要替换旧消息）、SlidingWindowPruning（兜底裁剪，裁剪边界自动对齐 tool_call/tool_result pair 防止孤儿消息）。所有策略实现 `CompressionStrategy` 接口，返回新列表不修改原列表。
- **`src/sagent/context/token_counter.py`**：Token 估算器。`count_tokens()` 对 OpenAI 消息列表估算总 token（含 tool_calls 开销与格式开销）；`count_text_tokens()` 对单段文本估算。支持 auto/tiktoken/heuristic 三种方式。
- **`src/sagent/session/models.py`**：会话数据模型（pydantic）。`SessionMeta`（含 `persisted_seq` 已持久化游标）、`SessionMessage`（OpenAI 消息结构 + 单调递增 `seq`）、`CompactionEvent`（压缩事件：摘要文本 + 覆盖 seq 区间）。
- **`src/sagent/session/store.py`**：会话持久化存储。基于 SQLite，双层表结构：`sessions` 表（元数据）、`messages` 表（append-only 完整历史，按 `seq` 递增）、`session_events` 表（压缩事件日志）。`append_messages()` 仅插入 `seq > persisted_seq` 的新消息实现增量写入；`build_working_context()` 由最近压缩摘要 + 其后原始消息拼接还原工作上下文；`append_compaction_event()` 记录压缩事件不改动原始历史。FTS5 全文索引不可用时自动降级为 LIKE 查询。
- **`src/sagent/session/manager.py`**：会话管理器。协调 SessionStore 与 ContextManager，负责会话创建/切换/重命名/删除、增量保存与工作上下文还原。`save_current()` 调用 `export_new_messages` 导出新增消息并委托 Store 增量写入；`switch_session()` 通过 `build_working_context` 还原上下文并 `load_messages` 装入 ContextManager；通过 `set_compaction_callback` 注册回调，压缩发生时将压缩事件写入 `session_events` 而不删改已落盘历史。删除当前会话时自动切换到其他会话以保护状态。
- **`src/sagent/memory/store.py`**：Markdown 文件记忆存储。维护两个本地 Markdown 文件（USER.md 与 MEMORY.md）的内容缓存，支持加载、读取全文、追加、替换、删除与字符上限检查。所有写操作先更新内存缓存再通过临时文件 + `os.replace` 原子落盘，避免中途异常导致文件损坏。不依赖 LLM 与 config 模块，构造时传入目录与字符上限。
- **`src/sagent/memory/manager.py`**：记忆管理器。协调 MemoryStore 与 LLM 客户端，提供会话前缀注入、写入前安全扫描把关与写入超限反思整理。`build_memory_prefix()` 读取两个文件全文拼装为记忆前缀文本（含使用引导提示词），会话开始时调用一次由调用方冻结复用；`add` / `replace` / `_reflect` 在写入前经 `_scan()` 安全扫描（拦截凭证泄露、Shell 后门、Prompt 注入，净化不可见字符），拦截则返回错误字符串不落盘；`remove` 不扫描；写入后超限则触发 `_reflect()` 调用 LLM 做去重/合并/精简并原子写回；LLM 调用失败时保留写入前内容，不抛异常、不阻断主流程。
- **`src/sagent/memory/security.py`**：记忆安全扫描器。纯 Python 标准库规则实现，无构造参数，四类规则全部启用、不可关闭。`scan()` 先净化不可见字符（C0/C1 控制字符、零宽字符、双向控制符），再依次检测凭证泄露（PEM 私钥、API Key、Bearer token 等）、Shell 威胁（authorized_keys、rm -rf /、curl|sh 等）、Prompt 注入（中英文组合模式），任一命中即拦截。由 `MemoryManager` 在写入路径强制调用，生产构造自动启用，无配置开关可关闭。
- **`src/sagent/tools/memory_tool.py`**：长期记忆读写工具。`add_memory` / `replace_memory` / `remove_memory` 三个工具，继承 Tool 基类，参数用 pydantic 模型定义 schema，执行委托 MemoryManager 对应方法。通过 `target` 参数区分目标文件（user→USER.md，memory→MEMORY.md）。
- **`src/sagent/cli/commands.py`**：斜杠命令纯函数解析器。`parse_command(raw) -> ParsedCommand | None` 将 `/name arg...` 解析为命令名+参数结构，非斜杠输入返回 `None`。与命令族无关，未来新增任意 slash command 均复用此解析器。
- **`src/sagent/cli/app.py`**：CLI 应用。构建 SessionStore/SessionManager（`session.enabled` 时）、交互循环中调用 `parse_command` 分发斜杠命令到 SessionManager、`auto_save` 时每轮问答后增量保存、退出时保存当前会话。
- **`src/sagent/tools/mcp/session_manager.py`**：MCP 会话管理器。通过后台 daemon 线程运行 asyncio 事件循环，桥接异步 MCP SDK 与同步项目代码。`start()` 启动后台循环；`connect_server(config)` 在循环中创建传输连接（stdio/sse/streamable_http）与 ClientSession，执行 initialize 握手，保持会话存活，返回发现的工具列表（连接失败返回空列表）；`call_tool(server, tool, args)` 通过 `run_coroutine_threadsafe` 提交异步调用并同步等待结果（超时返回错误字符串）；`shutdown()` 逆序关闭所有上下文并停止循环。每个服务器独立配置 connect_timeout（默认 30s）与 call_timeout（默认 60s）。
- **`src/sagent/tools/mcp/tool.py`**：MCP 工具包装。`MCPTool` 继承 `Tool` 基类，覆写 `to_openai_schema()` 直接使用 MCP 原始 JSON schema（不依赖 pydantic model_json_schema），覆写 `validate_args()` 直接返回原始 dict（MCP 服务端负责校验），`run()` 委托 `MCPSessionManager.call_tool()` 执行。工具名强制 `mcp_{server}_{tool}` 前缀，防冲突且可辨识来源。
- **`src/sagent/tools/mcp/provider.py`**：MCP 工具提供者。`MCPToolProvider` 实现 `ToolProvider` 接口，`provide_tools()` 中调用 `session_manager.connect_server` 连接并发现工具，用 `ToolFilter` 过滤，为每个通过过滤的工具创建 `MCPTool` 实例返回。连接失败返回空列表不抛异常。
- **`src/sagent/tools/mcp/filtering.py`**：MCP 工具过滤器。纯 Python 实现不依赖 MCP SDK。`ToolFilter(allow, deny)` 通过 allow/deny 两个列表控制白名单/黑名单：均空不过滤、allow 非空白名单、deny 非空黑名单、两者均非空先白名单再排除 deny。
- **`src/sagent/observability/logging_setup.py`**：日志系统。文件日志为 JSON 每行一条，按天滚动；控制台为纯文本。每次问答生成 trace_id 注入全部日志。

## 代码规范

### 命名与风格

- 模块、类、函数使用 `snake_case`；类名使用 `PascalCase`
- 私有方法以单下划线前缀（如 `_emit`、`_decompose`）
- 常量使用全大写下划线（如 `_MAX_READ_CHARS`、`_EXIT_COMMANDS`）
- 每个模块顶部必须有中文 docstring，说明模块用途
- 函数 docstring 用中文，说明参数与返回值

### 类型注解

- 所有模块顶部添加 `from __future__ import annotations`
- 使用现代类型语法：`str | None`、`list[dict[str, Any]]`、`Type[BaseModel]`
- 公开方法必须标注参数与返回类型

### 配置与数据建模

- 配置结构使用 pydantic `BaseModel`，字段带 `Field(..., description=...)`
- 配置模型包括：`LLMConfig`、`AgentConfig`、`LoggingConfig`、`ContextConfig`（上下文管理）、`SessionConfig`（会话管理）、`MemoryConfig`（长期记忆）、`AppConfig`（顶层聚合）
- `ContextConfig` 控制 token 预算（`max_context_tokens`）、压缩阈值（`compression_threshold` / `safe_threshold`）、压缩策略参数（`keep_recent_messages`、`max_tool_output_tokens`、`enable_summary`、`summary_max_tokens`）、token 计数方式（`token_counter_method`）、混合校准开关（`use_api_calibration`）
- `SessionConfig` 控制会话管理（`enabled` 开关、`db_path` SQLite 路径、`enable_fts` FTS5 全文索引、`auto_save` 每轮自动增量保存）；`AppConfig.session` 缺省可用
- `MemoryConfig` 控制长期记忆（`enabled` 开关、`dir` 记忆文件目录、`user_max_chars` / `memory_max_chars` 字符上限触发反思整理）；`AppConfig.memory` 缺省可用
- `MCPConfig` 控制 MCP 工具提供者（`enabled` 开关默认 false、`servers` 服务器配置列表）；`MCPServerConfig` 定义单个服务器（`name`、`transport` stdio/sse/streamable_http、`command`/`args`/`env`/`cwd` stdio 参数、`url` 远程地址、`enabled` 服务器开关、`tool_filter` 工具过滤、`connect_timeout` 连接超时默认 30 秒、`call_timeout` 调用超时默认 60 秒）；`ToolFilterConfig` 控制工具过滤（`allow`/`deny` 列表）；`AppConfig.mcp` 缺省可用
- 工具参数使用 pydantic 模型定义 `args_schema`，自动生成 JSON schema 供 function calling

### 日志

- 每个模块获取独立 logger：`logger = get_logger(__name__)`
- 事件字段通过 `extra={"event": "xxx", ...}` 传入，会被序列化进 JSON 日志
- ⚠️ **不要使用 `message`、`asctime`、`trace_id` 作为 extra 的 key**，它们是日志保留属性，会冲突
- 异常使用 `logger.exception()` 记录堆栈

### 工具开发约定

- 新增工具：继承 `sagent.tools.base.Tool`，定义类属性 `name`、`description`、`args_schema`，实现 `run(args)` 方法
- 工具 `run()` 返回字符串结果；**错误情况返回以"错误:"开头的字符串**，不要抛异常（让 LLM 能读到错误并自我纠正）
- 在 `src/sagent/tools/__init__.py` 的 `build_default_registry()` 中注册新工具

### 编码与字符

- 所有 Python 文件使用 UTF-8 编码
- 代码注释使用中文
- ⚠️ 生成含中文的代码时，确认无乱码（文件以 UTF-8 无 BOM 保存）

## 测试策略

### 框架与配置

- 测试框架：pytest（配置见 `pytest.ini`，`pythonpath = src` 使 `import sagent` 生效）
- 覆盖率工具：pytest-cov

### 测试分层

1. **单元测试**（`tests/unit/`）：不依赖 LLM，覆盖配置加载、工具执行与容错、注册表、Plan 步骤解析、上下文管理（压缩策略、token 计数、混合校准、seq 维护与增量导出）、会话存储（建表/CRUD/增量追加/压缩事件/工作上下文还原/FTS5 降级）、会话管理（创建/切换/增量保存/删除保护）、记忆存储（加载/追加/替换/删除/原子落盘/字符上限检查）、记忆管理（前缀注入/写入前安全扫描/反思整理/写入超限触发）、记忆安全扫描（四类规则命中/未误报/集成/审计日志）、记忆工具（参数校验/委托执行）、斜杠命令解析（`parse_command` 纯函数用例）
2. **引擎测试**（`tests/engines/`）：使用 `FakeLLMClient` 按预设响应队列离线回放，验证 ReAct / Plan 多轮编排逻辑，快速且可复现
3. **能力评测**（`tests/evals/`）：以数据形式集中定义评测场景。默认走离线回放；设置 `RUN_LLM_EVALS=1` 调用真实模型并用 LLM-as-judge 打分

### FakeLLMClient 约定

`tests/conftest.py` 中的 `FakeLLMClient` 实现 `chat(messages, tools=None) -> LLMResponse` 接口，按队列返回预设响应。**关键**：预设响应数量必须与预期调用轮次匹配，否则会抛 `AssertionError`。使用 `make_fake_llm` fixture 工厂构造实例。

辅助构造函数：
- `make_tool_call(name, arguments, call_id)` - 构造 tool_call dict
- `text_response(content, usage=None)` - 纯文本响应（可选传入 usage 模拟 API 返回的 token 用量）
- `tool_response(tool_calls, content="", usage=None)` - 带工具调用的响应（可选传入 usage）

### 评测场景

新增 Agent 能力时，在 `tests/evals/test_agent_evals.py` 的 `SCENARIOS` 列表中追加 `Scenario` 即可，无需新建测试函数。

## 调试技巧

- **日志检索**：每次问答生成 8 位 trace_id，可按 trace_id 检索整条链路：
  ```powershell
  Select-String -Path logs/sagent.log -Pattern '"trace_id": "a1b2c3d4"'
  ```
- **LLM 内容记录**：将 `config.yaml` 中 `logging.log_llm_content` 设为 `true` 可记录完整请求/响应（注意体积与敏感信息）
- **离线调试引擎**：使用 `FakeLLMClient` 构造预设响应序列，可在不调用真实模型的情况下调试 ReAct / Plan 编排逻辑
- **配置问题**：`LLM_API_KEY` / `LLM_API_URL` 环境变量优先级高于配置文件；缺失 API Key 会抛 `ConfigError`
- **上下文管理调试**：混合校准的关键日志事件可通过 `event` 字段过滤：
  - `calibration_recorded`（INFO）：LLM 调用后记录精确基准
  - `calibration_delta`（DEBUG）：每次 token 估算的基准 + delta 计算
  - `calibration_reset`（INFO）：压缩触发时重置基准
  - `context_compress_start` / `context_compress_done`：压缩触发与各层完成
  ```powershell
  Select-String -Path logs/sagent.log -Pattern '"event": "calibration'
  ```

## 扩展说明

- **新增工具**：详见"代码规范 > 工具开发约定"章节
- **新增压缩策略**：继承 `sagent.context.strategies.CompressionStrategy`，实现 `compress(messages) -> list`，在 `ContextManager.__init__` 中实例化并在 `_compress()` 中按需调用
- **新增斜杠命令**：`cli/commands.py` 中的 `parse_command` 为通用纯函数解析器，新增任意 slash command 均复用该解析器，仅需在 `cli/app.py` 的分发逻辑中增加对应分支；解析用例统一并入 `tests/unit/test_cli.py`
- **MCP 工具接入**：已实现 `sagent.tools.mcp.MCPToolProvider`（实现 `ToolProvider` 接口），通过 `build_mcp_providers(config, session_manager)` 构建提供者列表，逐个调用 `registry.register_provider()` 注册。配置在 `config.yaml` 的 `mcp` 段，支持 stdio/sse/streamable_http 三种传输方式，工具级 allow/deny 过滤与服务器级 enabled 开关，工具名强制 `mcp_{server}_{tool}` 前缀。详见 `tools/mcp/` 子包
- **新增执行模式**：参考 `ReActEngine` / `PlanEngine` 实现引擎类，在 `cli/app.py` 的 `build_engine()` 中添加分支

## 特殊限制

- ⚠️ `run_shell` 工具会执行任意系统命令，存在安全风险，仅在可信环境使用
- ⚠️ `config.yaml` 含密钥，已被 gitignore，不要提交到版本库
- 单次读取文件内容超过 20000 字符会被截断；Shell 输出超过 10000 字符会被截断
- 环境要求 Python 3.10+（使用 `str | None` 等现代类型语法）

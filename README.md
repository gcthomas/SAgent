# SAgent

一个简单、通用、可扩展的 CLI Agent 应用。基于 OpenAI SDK 调用大模型，支持 ReAct 与 Plan 两种执行模式，内置文件读写与 Shell 执行工具，并为未来 MCP / skill 扩展预留接口。

## 特性

- CLI 交互：命令行接收输入并给出回答
- 两种模式：
  - ReAct：推理 - 行动 - 观察 循环
  - Plan：先拆解任务为步骤，再逐步执行并汇总
- 工具系统：基于 pydantic 定义参数 schema，对接 LLM function calling
  - 内置工具：`read_file`、`write_file`、`run_shell`
  - 预留 `ToolProvider` 接口，供未来接入 MCP / skill
- 兼容任意 OpenAI 兼容的 LLM 服务（通过 base_url 指定）
- 上下文管理：自动追踪 token 用量，超阈值时分层压缩（截断 → 卸载 → 摘要 → 裁剪），支持 API 精确 token 校准
- 会话管理：创建/切换/重命名/删除会话，历史持久化到 SQLite（FTS5 全文检索），支持斜杠命令交互
- 长期记忆：基于两个 Markdown 文件（USER.md / MEMORY.md）的跨会话记忆，LLM 通过记忆工具自主读写，写入前自动安全扫描（凭证泄露/Prompt 注入/Shell 后门/不可见字符），写入超限时自动反思整理，会话开始前注入冻结前缀
- 配置集中在 YAML 文件

## 环境要求

- Python 3.10+

## 安装

```powershell
pip install -r requirements.txt
```

## 配置

复制示例配置并修改：

```powershell
Copy-Item config.example.yaml config.yaml
```

`config.yaml` 关键字段：

```yaml
llm:
  model: "gpt-4o-mini"
  api_key: ""       # 也可用环境变量 LLM_API_KEY
  base_url: ""      # 也可用环境变量 LLM_API_URL（对接 OpenAI 兼容服务）
  temperature: 0.7
  timeout: 60
agent:
  mode: "react"     # 默认模式：react 或 plan
  max_iterations: 10
logging:
  enabled: true
  level: "DEBUG"          # 文件日志级别
  console_level: "INFO"   # 控制台日志级别
  dir: "logs"
  file: "sagent.log"      # 按天滚动，归档为 sagent.log.2026-07-10
  backup_count: 7
  log_llm_content: false  # 是否记录 LLM 完整请求/响应内容
context:
  max_context_tokens: 128000     # 模型上下文窗口大小（token）
  compression_threshold: 0.7     # 触发压缩的阈值占比
  safe_threshold: 0.5            # 压缩目标安全线占比
  keep_recent_messages: 20       # 始终保留的最近消息条数
  max_tool_output_tokens: 2000   # 单条工具结果最大 token 数
  token_counter_method: "auto"   # 计数方式：auto/tiktoken/heuristic
  use_api_calibration: true      # 使用 API 返回的 prompt_tokens 做混合校准
  enable_summary: true           # 启用第三层 LLM 摘要压缩
  summary_max_tokens: 500        # 摘要最大 token 数（注入提示词约束 LLM 输出）
session:                         # 会话管理（可选，缺省时使用默认值）
  enabled: true                  # 是否启用会话管理
  db_path: "sessions.db"         # 会话数据库文件路径（相对运行目录）
  enable_fts: true               # 是否启用 FTS5 全文索引（不支持时降级为 LIKE 查询）
  auto_save: true                # 是否每轮问答后自动增量保存会话消息
memory:                          # 长期记忆（可选，缺省时使用默认值）
  enabled: true                  # 是否启用长期记忆
  dir: "memory"                  # 记忆文件所在目录（USER.md 与 MEMORY.md 均存于此目录）
  user_max_chars: 2000           # 用户文件字符上限，超限触发 LLM 反思整理
  memory_max_chars: 4000         # 记忆文件字符上限，超限触发 LLM 反思整理
```

环境变量覆盖（优先级高于配置文件）：

```powershell
$env:LLM_API_KEY = "你的密钥"
$env:LLM_API_URL = "https://api.deepseek.com/v1"
```

## 运行

```powershell
# 使用配置文件默认模式
python main.py

# 指定 ReAct 模式
python main.py --mode react

# 指定 Plan 模式与自定义配置文件
python main.py --config config.yaml --mode plan
```

进入交互后输入问题即可对话，输入 `exit` 或 `quit` 退出。启用会话管理后还可用斜杠命令管理会话：

```
/new [名称]       创建新会话（名称可选，缺省自动生成）
/switch <名称>    切换到指定会话
/sessions         列出所有会话
/rename <新名称>  重命名当前会话
/delete <名称>    删除指定会话（删除当前会话会自动切换到其他会话）
```

## 日志

程序内置基于标准库 `logging` 的日志系统，记录从用户输入到 Agent 处理、LLM 调用、工具执行直至最终回复的完整运行轨迹，并对异常记录堆栈，便于事后定位。

- **输出**：控制台输出简洁纯文本（默认 `INFO`）；文件日志写入 `logs/sagent.log`，为 **JSON 每行一条**（默认 `DEBUG`），按天滚动归档为 `sagent.log.2026-07-10`，并按 `backup_count` 自动清理。
- **trace_id**：每次问答生成一个 8 位 `trace_id` 并注入当次全部日志，可用同一 id 检索整条链路。
- **LLM 内容**：默认仅记录摘要（模型、消息条数、耗时、是否含工具调用等）；将 `logging.log_llm_content` 设为 `true` 后会记录完整请求/响应内容（注意体积与敏感信息）。
- **调整级别**：通过 `logging.level` / `logging.console_level` 配置；设为 `false` 时用 `logging.enabled: false` 关闭。

按某次问答的 trace_id 检索日志（PowerShell）：

```powershell
Select-String -Path logs/sagent.log -Pattern '"trace_id": "a1b2c3d4"'
```

## 上下文管理

Agent 运行过程中消息历史不断增长，超过模型上下文窗口会导致请求失败。内置上下文管理器自动追踪 token 用量，在超阈值时执行分层压缩，保证发送给 LLM 的消息始终在安全范围内。

**四层压缩策略**（低成本操作 → 语义保留 → 兜底裁剪，依次执行，每层执行后检查是否已达安全线）：

| 层级 | 策略 | 说明 |
|------|------|------|
| 第一层 | 工具输出截断 | 截断过长的工具结果内容（保留头尾），信息无损 |
| 第二层 | 工具消息卸载 | 将过期工具调用/结果对替换为简短摘要，轻度信息损失 |
| 第三层 | LLM 摘要压缩 | 调用 LLM 对旧消息生成摘要替换原消息，语义保留 |
| 第四层 | 滑动窗口裁剪 | 兜底：仅保留 system 消息与最近 N 条消息（裁剪边界自动对齐 tool_call/tool_result pair） |

**Token 计数与混合校准**：

- 支持三种计数方式：`auto`（优先 tiktoken，回退启发式）、`tiktoken`、`heuristic`（字符启发式）
- 启用 `use_api_calibration`（默认开启）后，每次 LLM 调用返回的 `prompt_tokens` 作为精确基准，新增消息仅估算 delta，总量 = 精确基准 + 估算 delta，精度更高且不依赖 tiktoken
- 压缩触发时自动重置校准基准，下次 LLM 调用后重新校准

触发压缩与停止的阈值由 `compression_threshold` 与 `safe_threshold` 控制（相对 `max_context_tokens` 的占比）。`context` 段为可选配置，缺省时使用默认值。

## 会话管理

启用会话管理（`session.enabled: true`）后，每轮问答的历史会持久化到 SQLite 数据库，支持创建、切换、重命名、删除会话，并通过斜杠命令在交互中管理。

**双层存储模型**（参考 LangGraph、OpenAI Assistants、MemGPT 等业界实践）：

- **append-only 完整历史**：`messages` 表按单调递增 `seq` 追加写入，从不修改已落盘消息，保留无损完整历史
- **压缩事件日志**：`session_events` 表记录上下文压缩事件（摘要文本 + 覆盖的 seq 区间），不改动原始历史
- **工作上下文还原**：切换会话时由「最近一次压缩摘要 + 其后原始消息」拼接得到，与运行时上下文管理器桥接

**增量保存**：仅追加 `seq > persisted_seq` 的新消息，避免全量重写开销。上下文压缩发生时，压缩事件单独写入 `session_events`，原消息历史不被删改，后续可基于完整历史做记忆检索。

**FTS5 全文检索**：消息正文建立 FTS5 索引，便于后续按关键词检索历史对话；运行环境不支持 FTS5 时自动降级为 LIKE 查询。

## 长期记忆

启用长期记忆（`memory.enabled: true`）后，Agent 通过记忆工具（`add_memory` / `replace_memory` / `remove_memory`）在对话过程中自主读写两个本地 Markdown 文件，实现跨会话记忆。文件为自由格式 Markdown，可直接用编辑器查看与修改。

**两个记忆文件**（参考 Hermes Agent 的记忆引导实践）：

- `USER.md`（target=user）：用户档案——姓名、角色、时区、沟通偏好、反感事项、技术水平
- `MEMORY.md`（target=memory）：Agent 笔记——环境事实、项目约定、工具怪癖、已完成工作、经验教训

**会话前缀注入**：会话开始前读取两个文件全文，拼装为记忆前缀（含使用引导提示词 + 文件内容）注入到系统提示词最前面，整个会话期间冻结复用以命中 prefix cache。引导提示词始终注入（即使记忆为空），让 LLM 知道有记忆工具可用及何时使用。

**反思整理**：每次写入后检查字符上限，超限时自动调用 LLM 对记忆全文做去重、合并冗余与精简表述，整理后原子写回文件。LLM 调用失败时保留写入前内容，不阻断主流程。

**写入前安全扫描**：所有记忆写入（`add` / `replace` / 反思整理写回）在持久化前强制经过安全扫描，不可关闭。扫描包含四类检测：

- 不可见字符净化：移除 C0/C1 控制字符、零宽字符、双向控制符等，净化后内容继续落盘
- 凭证泄露检测：拦截 PEM 私钥、API Key、Bearer token 等敏感凭证
- Shell 后门检测：拦截 authorized_keys、rm -rf /、curl|sh 等恶意命令
- Prompt 注入检测：拦截"忽略以上指令""你现在是"等越权指令（组合模式降低误报）

命中拒绝类规则时返回错误提示供 LLM 自我纠正，不落盘；安全扫描默认启用，无配置开关可关闭。

## 目录结构

```
main.py                     程序入口
config.example.yaml         配置示例
requirements.txt            依赖
src/sagent/
  config/                   配置模型与 YAML 加载
  llm/                      基于 openai SDK 的 LLM 客户端
  tools/                    工具基类、注册表、内置工具
  core/                     ReAct 与 Plan 执行引擎、提示词
  context/                  上下文管理：token 估算、分层压缩、上下文管理器
  session/                  会话管理：数据模型、SQLite 持久化、会话管理器
  memory/                   长期记忆：Markdown 文件存储、记忆管理器、安全扫描器、提示词
  observability/            日志系统（JSON 结构化、按天滚动、trace_id）
  cli/                      命令行应用、斜杠命令解析
tests/
  conftest.py               公共 fixture 与 FakeLLMClient
  unit/                     单元测试（配置、工具、注册表、Plan 解析、上下文管理、会话存储与管理、记忆存储与管理与记忆工具、命令解析，无需 LLM）
  engines/                  引擎测试（ReAct / Plan，用 FakeLLMClient 离线回放）
  evals/                    Agent 能力评测（离线回放 + 可选真实 LLM）
```

## 测试

安装开发依赖并运行测试：

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

测试分三层：

- 单元测试：不依赖 LLM，覆盖配置加载、工具执行与容错、注册表、Plan 步骤解析、上下文管理、会话存储与管理、长期记忆存储与管理与安全扫描、斜杠命令解析。
- 引擎测试：使用 `FakeLLMClient`（`tests/conftest.py`）按预设响应队列离线回放，验证 ReAct / Plan 的多轮编排逻辑，快速且可复现。
- 能力评测（`tests/evals`）：以数据形式集中定义评测场景。默认走离线回放；设置 `RUN_LLM_EVALS=1` 并配置好真实 LLM 后，会调用真实模型执行任务并用 LLM-as-judge 打分。

```powershell
# 仅运行离线用例（默认，跳过真实 LLM 评测）
python -m pytest

# 启用真实 LLM 评测（需有效的 LLM_API_KEY / LLM_API_URL 与 config.yaml）
$env:RUN_LLM_EVALS = "1"
python -m pytest tests/evals
```

新增 Agent 能力时，在 `tests/evals/test_agent_evals.py` 的 `SCENARIOS` 中追加场景即可。

## 扩展说明

- 新增工具：继承 `sagent.tools.base.Tool`，定义 `name`、`description`、`args_schema` 与 `run`，再注册到 `ToolRegistry`。
- 新增压缩策略：继承 `sagent.context.strategies.CompressionStrategy`，实现 `compress(messages) -> list`，在 `ContextManager` 中集成。
- 新增斜杠命令：`cli/commands.py` 中的 `parse_command` 为通用纯函数解析器，新增任意 slash command 均复用该解析器，仅需在 `cli/app.py` 的分发逻辑中增加对应分支。
- MCP / skill：实现 `sagent.tools.base.ToolProvider` 接口，通过 `ToolRegistry.register_provider` 接入（当前仅预留接口）。

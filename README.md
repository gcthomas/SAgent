# SAgent

> 简单、通用、可扩展的 CLI Agent，支持 ReAct 执行循环与自适应计划（Plan）。

SAgent 是一个基于 Python 的 CLI Agent 应用，通过 OpenAI 兼容 API 调用大模型完成推理与工具调用。内置 ReAct 与自适应计划执行、文件与 Shell 工具、上下文压缩、会话持久化、长期记忆、MCP 工具接入、工具权限审批与 OpenTelemetry 可观测性。

## 特性

- 统一执行引擎：ReAct 与 Plan 融合为单一入口，计划策略由 `planning.strategy` 控制（adaptive / always / never）
- 工具系统：基于 Pydantic 定义参数 schema，对接 LLM function calling；内置 `read_file`、`write_file`、`run_shell` 及记忆工具，并支持通过 MCP 接入外部工具
- 上下文管理：自动追踪 token 用量，超阈值时分层压缩（截断 → 卸载 → 摘要 → 裁剪），支持基于 API 的精确校准
- 会话管理：历史持久化到 SQLite（FTS5 全文检索），支持创建、切换、搜索等斜杠命令
- 长期记忆：基于 `USER.md` / `MEMORY.md` 的跨会话记忆，写入前自动安全扫描
- 工具权限：执行前统一授权，写盘与执行类工具需用户确认，危险命令直接拒绝
- 可观测性：OpenTelemetry Span 追踪与指标聚合、内容脱敏、本地 JSON Lines 导出，可选 OTLP 上报
- 流式输出：支持 Chat Completions 流式响应
- 配置集中：单一 YAML 文件，支持环境变量覆盖敏感字段

## 环境要求

- Python 3.10+

## 安装

```powershell
# 运行时依赖
pip install -r requirements.txt

# 开发与测试依赖（可选）
pip install -r requirements-dev.txt
```

## 快速开始

```powershell
# 1. 生成配置并按需修改
Copy-Item config.example.yaml config.yaml

# 2. 配置密钥与地址（也可直接写入 config.yaml）
$env:LLM_API_KEY = "你的密钥"
$env:LLM_API_URL = "https://api.deepseek.com/v1"   # OpenAI 兼容服务的 base_url

# 3. 运行
python main.py
python main.py --config config.yaml                # 指定配置文件
```

进入交互后直接输入问题即可对话，输入 `exit` / `quit` / `:q` 退出。

## 配置

配置采用单一 YAML 文件，完整字段与逐项说明见 [config.example.yaml](config.example.yaml)。主要配置段：

| 配置段 | 说明 |
| --- | --- |
| `llm` | 模型、密钥、base_url、温度、超时、流式开关 |
| `agent` | ReAct 循环最大迭代次数 |
| `planning` | 计划策略与步数、重规划上限 |
| `context` | 上下文窗口大小与分层压缩参数 |
| `session` | 会话持久化（SQLite、FTS5、自动保存） |
| `memory` | 长期记忆目录与文件字符上限 |
| `mcp` | MCP 服务器列表与工具过滤 |
| `permissions` | 工具权限规则与审批策略 |
| `observability` | 追踪、指标、脱敏与导出 |
| `logging` | 日志级别、文件滚动与内容记录 |

敏感字段可用环境变量覆盖：`LLM_API_KEY` 覆盖 `llm.api_key`，`LLM_API_URL` 覆盖 `llm.base_url`。

## 斜杠命令

启用会话管理（`session.enabled: true`）后，可在交互中使用以下命令：

| 命令 | 说明 |
| --- | --- |
| `/new [标题]` | 创建新会话 |
| `/sessions [每页数量] [页码]` | 分页列出会话 |
| `/switch <id>` | 切换会话 |
| `/rename <标题>` | 重命名当前会话 |
| `/delete <id>` | 删除会话（不能删除当前会话） |
| `/search <关键词>` | 搜索历史消息 |
| `/session` | 显示当前会话信息 |
| `/help` | 显示帮助 |

## 项目结构

```text
main.py                     程序入口
src/sagent/
  cli/                      命令行应用、命令解析与事件展示
  config/                   配置模型与 YAML 加载
  llm/                      LLM 客户端封装
  core/                     ReAct / Plan 执行引擎与提示词
  context/                  上下文预算、Token 统计与分层压缩
  session/                  会话持久化与会话管理
  memory/                   长期记忆存储、安全扫描与管理
  permissions/              工具权限策略、执行与审批
  observability/            可观测性（追踪、指标、脱敏、导出）
  tools/                    工具基类、注册表、内置工具与 MCP 接入
tests/                      单元测试、引擎测试与能力评测
```

## 测试

```powershell
python -m pytest                 # 默认离线测试
python -m pytest tests/unit      # 仅单元测试
python -m pytest tests/engines   # 仅引擎测试
```

默认测试不依赖真实 LLM。启用真实 LLM 能力评测（需已配置密钥与地址）：

```powershell
$env:RUN_LLM_EVALS = "1"
python -m pytest tests/evals
```

## 扩展

- 新增工具：继承 `sagent.tools.base.Tool`，定义 `name`、`description`、`args_schema` 与 `run`，再注册到 `ToolRegistry`；默认工具在 `build_default_registry()` 中注册。
- 接入 MCP：在 `config.yaml` 的 `mcp.servers` 中加入服务器配置即可，其工具会以 `mcp_{server}_{tool}` 命名自动注册。
- 新增压缩策略：继承 `sagent.context.strategies.CompressionStrategy`，并在 `ContextManager` 中集成。
- 新增斜杠命令：复用 `cli/commands.py` 的通用解析器，在 `cli/app.py` 的分发逻辑中增加对应分支。

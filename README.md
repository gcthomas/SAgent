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

进入交互后输入问题即可对话，输入 `exit` 或 `quit` 退出。

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
  observability/            日志系统（JSON 结构化、按天滚动、trace_id）
  cli/                      命令行应用
tests/
  conftest.py               公共 fixture 与 FakeLLMClient
  unit/                     单元测试（配置、工具、注册表、Plan 解析，无需 LLM）
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

- 单元测试：不依赖 LLM，覆盖配置加载、工具执行与容错、注册表、Plan 步骤解析。
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
- MCP / skill：实现 `sagent.tools.base.ToolProvider` 接口，通过 `ToolRegistry.register_provider` 接入（当前仅预留接口）。

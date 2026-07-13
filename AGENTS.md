# AGENTS.md

本文件为 AI Agent（以及开发者）提供 SAgent 项目的操作指南。修改代码前请先阅读本文件。

## 项目概述

**SAgent** 是一个简单、通用、可扩展的 CLI Agent 应用。基于 OpenAI SDK 调用大模型，支持 ReAct 与 Plan 两种执行模式，内置文件读写与 Shell 执行工具，并为未来 MCP / skill 扩展预留接口。

- **项目类型**：CLI 工具 / Python 应用
- **核心功能**：命令行交互式 Agent，支持工具调用与多轮编排
- **技术栈**：Python 3.10+、openai SDK、pydantic v2、pyyaml、pytest
- **架构说明**：
  - 两种执行模式：ReAct（推理-行动-观察循环）、Plan（拆解-逐步执行-汇总）
  - 工具系统基于 pydantic 定义参数 schema，对接 LLM function calling
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
  cli/app.py                CLI 应用：参数解析、交互循环、引擎构建
  config/
    models.py               配置模型（pydantic）：LLMConfig、AgentConfig、LoggingConfig、AppConfig
    loader.py               YAML 配置加载 + 环境变量覆盖 + 校验
  llm/client.py             LLM 客户端：封装 openai SDK，统一 LLMResponse 结构
  core/
    react_engine.py         ReAct 引擎：推理-工具调用-观察循环
    plan_engine.py          Plan 引擎：拆解-逐步执行(复用 ReAct)-汇总
    prompts.py              系统提示词定义
  tools/
    base.py                 Tool 抽象基类 + ToolProvider 接口（预留 MCP/skill）
    registry.py             ToolRegistry：注册、schema 输出、按名称执行
    file_tools.py           内置工具：read_file、write_file
    shell_tool.py           内置工具：run_shell
    __init__.py             build_default_registry() 构建默认工具集
  observability/
    logging_setup.py        JSON 结构化日志、按天滚动、trace_id 上下文关联
tests/
  conftest.py               公共 fixture：FakeLLMClient、make_tool_call、text_response
  unit/                     单元测试（配置、工具、注册表、Plan 解析，无需 LLM）
  engines/                  引擎测试（ReAct / Plan，用 FakeLLMClient 离线回放）
  evals/                    Agent 能力评测（离线回放 + 可选真实 LLM）
```

### 重要模块职责

- **`src/sagent/core/react_engine.py`**：ReAct 执行引擎，核心循环逻辑。`run()` 方法是主入口，调用 LLM → 判断是否工具调用 → 执行工具 → 追加观察 → 继续，直到得到最终答案或达到 `max_iterations` 上限。
- **`src/sagent/core/plan_engine.py`**：Plan 执行引擎。先调用 LLM 拆解任务为 JSON 步骤列表，每步复用 ReAct 引擎执行，最后汇总。
- **`src/sagent/tools/registry.py`**：工具注册表。**关键**：`execute()` 对未知工具、参数错误、执行异常均做容错处理，返回以"错误:"开头的字符串而不抛异常（避免 Agent 流程中断）。
- **`src/sagent/llm/client.py`**：LLM 客户端。封装 openai SDK，统一返回 `LLMResponse`（含 content、tool_calls）。记录请求/响应日志，默认仅摘要。
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

1. **单元测试**（`tests/unit/`）：不依赖 LLM，覆盖配置加载、工具执行与容错、注册表、Plan 步骤解析
2. **引擎测试**（`tests/engines/`）：使用 `FakeLLMClient` 按预设响应队列离线回放，验证 ReAct / Plan 多轮编排逻辑，快速且可复现
3. **能力评测**（`tests/evals/`）：以数据形式集中定义评测场景。默认走离线回放；设置 `RUN_LLM_EVALS=1` 调用真实模型并用 LLM-as-judge 打分

### FakeLLMClient 约定

`tests/conftest.py` 中的 `FakeLLMClient` 实现 `chat(messages, tools=None) -> LLMResponse` 接口，按队列返回预设响应。**关键**：预设响应数量必须与预期调用轮次匹配，否则会抛 `AssertionError`。使用 `make_fake_llm` fixture 工厂构造实例。

辅助构造函数：
- `make_tool_call(name, arguments, call_id)` - 构造 tool_call dict
- `text_response(content)` - 纯文本响应
- `tool_response(tool_calls, content)` - 带工具调用的响应

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

## 扩展说明

- **新增工具**：详见"代码规范 > 工具开发约定"章节
- **MCP / skill 接入**：实现 `sagent.tools.base.ToolProvider` 接口，通过 `ToolRegistry.register_provider()` 接入（当前仅预留接口）
- **新增执行模式**：参考 `ReActEngine` / `PlanEngine` 实现引擎类，在 `cli/app.py` 的 `build_engine()` 中添加分支

## 特殊限制

- ⚠️ `run_shell` 工具会执行任意系统命令，存在安全风险，仅在可信环境使用
- ⚠️ `config.yaml` 含密钥，已被 gitignore，不要提交到版本库
- 单次读取文件内容超过 20000 字符会被截断；Shell 输出超过 10000 字符会被截断
- 环境要求 Python 3.10+（使用 `str | None` 等现代类型语法）

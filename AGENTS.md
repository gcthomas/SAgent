# AGENTS.md

本文件为 AI Agent 与开发者提供 SAgent 项目级操作指南。只记录适用于全项目、长期稳定且无法从源码安全推断的约束；实现细节以当前源码、测试和配置为准。修改代码前先读取目标实现及其调用方。

## 项目概述

SAgent 是基于 Python 3.10+ 的 CLI Agent 应用，使用 OpenAI 兼容 API，支持 ReAct 与 Plan 执行模式、工具调用、MCP 扩展、会话与长期记忆，以及 OpenTelemetry 可观测性。

## 核心目录

```text
main.py                     程序入口
src/sagent/cli/             CLI 参数、交互循环与命令分发
src/sagent/config/          Pydantic 配置模型与配置加载
src/sagent/llm/             LLM 客户端封装
src/sagent/core/            ReAct / Plan 执行引擎
src/sagent/context/         上下文预算、Token 统计与压缩
src/sagent/session/         会话持久化与会话管理
src/sagent/memory/          长期记忆存储、安全扫描与管理
src/sagent/tools/           内置工具、注册表与 MCP 接入
tests/unit/                 单元测试
tests/engines/              ReAct / Plan 引擎测试
tests/evals/                Agent 能力评测
```

涉及某个模块时，先阅读该目录下的当前实现和对应测试；不要以本文替代源码或测试中的实现事实。

## 环境与命令

所有命令在 PowerShell 中执行，工作目录为项目根目录（包含 `main.py`）。

```powershell
# 安装运行时依赖
pip install -r requirements.txt

# 安装开发与测试依赖
pip install -r requirements-dev.txt

# 初始化本地配置后按需填写
Copy-Item config.example.yaml config.yaml

# 默认运行
python main.py

# 指定执行模式或配置
python main.py --mode react
python main.py --config config.yaml --mode plan

# 运行离线测试
python -m pytest
python -m pytest tests/unit
python -m pytest tests/engines

# 运行单个测试文件或用例
python -m pytest tests/unit/test_xxx.py
python -m pytest tests/unit/test_xxx.py::test_name

# 查看覆盖率
python -m pytest --cov=sagent --cov-report=term-missing

# 启用真实 LLM 评测
$env:RUN_LLM_EVALS = "1"
python -m pytest tests/evals
```

默认验证顺序是先运行受影响范围的测试，再运行 `python -m pytest`。未运行测试或测试失败时，完成说明中必须明确注明原因和结果。

## 修改原则

- 只修改需求直接涉及的代码、测试和必要配置，不做无关重构、格式化或清理。
- 修改函数实现前理解并保留原有逻辑；每一处改动都应对应明确需求。
- 优先遵循目标文件及相邻代码已有模式，不为假设需求增加抽象、配置项或兼容层。
- 新增或修改行为时同步补充或更新对应测试。
- 不虚构文件路径、接口、命令、测试结果或依赖；不确定时先读取源码或执行验证。

## 项目约定

### Python 与编码

- Python 文件使用 UTF-8 编码，代码注释使用中文，不使用 Emoji。
- 模块顶部添加 `from __future__ import annotations` 和中文模块 docstring。
- 模块、函数和变量使用 `snake_case`，类使用 `PascalCase`，常量使用大写下划线。
- 公开方法标注参数和返回类型；使用现代类型语法，如 `str | None`。
- 配置结构使用 Pydantic `BaseModel`；新增配置时同步检查示例配置、加载逻辑和测试。

### 日志

- 每个模块使用独立 logger：`logger = get_logger(__name__)`。
- 结构化事件字段通过 `extra={...}` 传递。
- `extra` 不得使用 `message`、`asctime`、`trace_id` 等日志保留字段。
- 需要堆栈时使用 `logger.exception()`；非阻塞路径的异常使用 `logger.debug(..., exc_info=True)`，不得影响主流程。

### 工具

- 新增工具继承 `sagent.tools.base.Tool`，定义 `name`、`description`、`args_schema` 并实现 `run(args)`。
- 工具 `run()` 返回字符串；错误情况返回以 `错误:` 开头的字符串，不向 Agent 主流程抛出预期工具错误。
- 默认工具需要在 `build_default_registry()` 中注册。

### 测试

- 使用 pytest；默认离线测试不得依赖真实 LLM。
- 引擎测试使用 `tests/conftest.py` 中的 FakeLLMClient 和辅助构造函数。
- FakeLLMClient 的预设响应数量必须与预期 LLM 调用轮次匹配。
- 真实 LLM 评测仅在显式设置 `RUN_LLM_EVALS=1` 且配置好密钥和地址时运行。

## 安全与边界

- 不读取、写入或提交真实密钥、令牌和凭证；不要提交 `config.yaml`。
- `run_shell` 可执行任意系统命令，仅在可信环境使用。
- 不执行破坏性 Git 操作，不修改与任务无关的已正确功能。
- 未经明确要求，不提交代码、不推送远程仓库、不修改 Git 配置。
- 新增生产依赖或涉及敏感数据、认证、生产环境的变更，应先确认影响范围。

## 维护 AGENTS.md

新增普通功能模块时，不需要更新本文的文件树、逐文件职责、配置字段清单或测试覆盖清单。只有以下内容发生变化时才更新本文：

- 全项目开发、测试或运行命令发生变化；
- 全项目约束、安全边界或完成标准发生变化；
- 目录级职责发生稳定且重要的变化；
- Agent 在实际任务中重复犯错，且新增规则能明确、可验证地避免该错误。

仅适用于某个子目录的规则，优先放在该目录下的 `AGENTS.md`，不要扩大根文件作用域。架构原理、专题调试流程和具体模块实现细节应由源码、测试或按需文档承载。

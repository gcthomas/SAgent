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
  cli/                      命令行应用
```

## 扩展说明

- 新增工具：继承 `sagent.tools.base.Tool`，定义 `name`、`description`、`args_schema` 与 `run`，再注册到 `ToolRegistry`。
- MCP / skill：实现 `sagent.tools.base.ToolProvider` 接口，通过 `ToolRegistry.register_provider` 接入（当前仅预留接口）。

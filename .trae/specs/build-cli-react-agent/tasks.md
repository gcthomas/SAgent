# Tasks

- [x] Task 1: 搭建项目骨架与依赖
  - [x] SubTask 1.1: 创建目录结构 `src/sagent/`（core / tools / llm / config / cli）
  - [x] SubTask 1.2: 创建 `requirements.txt`（openai、pydantic、pyyaml、mcp）
  - [x] SubTask 1.3: 创建 `.gitignore` 补充（已存在则不覆盖核心内容）与包 `__init__.py`

- [x] Task 2: 配置模块
  - [x] SubTask 2.1: 使用 pydantic 定义配置模型（llm: model/api_key/base_url/temperature；agent: mode/max_iterations 等）
  - [x] SubTask 2.2: 实现 YAML 加载函数，支持 `OPENAI_API_KEY` 环境变量覆盖，缺失必填项报错
  - [x] SubTask 2.3: 提供 `config.example.yaml` 示例配置

- [x] Task 3: LLM 客户端
  - [x] SubTask 3.1: 基于 openai SDK 封装 `LLMClient`，支持传入 messages 与 tools（function calling）
  - [x] SubTask 3.2: 统一返回结构（文本内容 / tool_calls）

- [x] Task 4: 工具系统
  - [x] SubTask 4.1: 定义 `Tool` 基类（name、description、pydantic 参数 schema、run 方法）与 `to_openai_schema()`
  - [x] SubTask 4.2: 实现 `ToolRegistry`（注册、列出 schema、按名执行、未知工具容错）
  - [x] SubTask 4.3: 实现内置工具「文件读写」（读/写，含路径与异常处理）
  - [x] SubTask 4.4: 实现内置工具「Shell/命令执行」（执行命令并返回输出，超时处理）
  - [x] SubTask 4.5: 定义 `ToolProvider` 抽象接口，为 MCP / skill 预留接入点（仅接口，不实现）

- [x] Task 5: ReAct 执行引擎
  - [x] SubTask 5.1: 实现 ReAct 循环（推理 -> tool_calls -> 执行 -> 观察 -> 继续），受 max_iterations 约束
  - [x] SubTask 5.2: 组织系统提示词与消息历史，处理最终回答

- [x] Task 6: Plan 执行引擎
  - [x] SubTask 6.1: 实现任务拆解（LLM 生成有序步骤列表）
  - [x] SubTask 6.2: 逐步执行（每步复用 ReAct 引擎）并汇总结果，展示进度

- [x] Task 7: CLI 入口
  - [x] SubTask 7.1: 使用 argparse 实现 `main.py`，支持 `--config`、`--mode react|plan`
  - [x] SubTask 7.2: 交互循环（读取输入、调用对应引擎、输出回答、支持退出命令）

- [x] Task 8: 联调与说明
  - [x] SubTask 8.1: 编写最小 README 使用说明（安装、配置、运行示例）
  - [x] SubTask 8.2: 本地静态检查（import / 语法）确保可运行

# Task Dependencies
- Task 2、3、4 依赖 Task 1
- Task 5 依赖 Task 3、Task 4
- Task 6 依赖 Task 5
- Task 7 依赖 Task 2、5、6
- Task 8 依赖 Task 7

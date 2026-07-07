# Checklist

- [x] 项目目录结构与 `requirements.txt`（openai、pydantic、pyyaml、mcp）已创建
- [x] 配置模块使用 pydantic 定义模型，能从 YAML 加载并支持 `LLM_API_KEY` 环境变量覆盖
- [x] 缺失必填配置项时给出明确错误提示
- [x] 提供 `config.example.yaml` 示例配置
- [x] LLMClient 基于 openai SDK，支持传入 messages 与 tools 并正确返回文本/tool_calls
- [x] Tool 基类使用 pydantic 定义参数 schema，并可转换为 OpenAI function calling 格式
- [x] ToolRegistry 支持注册、列出 schema、按名执行，且对未知工具容错不崩溃
- [x] 内置「文件读写」工具实现并可用
- [x] 内置「Shell/命令执行」工具实现并可用（含超时/异常处理）
- [x] 存在 ToolProvider 抽象接口作为 MCP / skill 预留接入点（不含具体实现）
- [x] ReAct 引擎实现思考-行动-观察循环，受 max_iterations 约束
- [x] Plan 引擎能拆解任务为步骤并逐步执行、汇总结果
- [x] CLI 入口支持 `--config` 与 `--mode react|plan`，具备交互循环与退出命令
- [x] 中文注释使用 UTF-8 且无乱码，代码中无 emoji
- [x] README 使用说明可指导安装、配置与运行

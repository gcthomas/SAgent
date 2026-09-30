# Context 摘要模型独立配置实施计划

## Summary

为 context 压缩摘要增加独立的 LLM 配置与客户端，使对话主模型和历史摘要模型解耦。推荐采用 `context.summary_llm` 配置块，复用现有 `LLMConfig` 字段结构，但不再把主 `llm` 作为摘要模型的隐式来源；启用摘要时必须显式配置摘要模型。CLI 负责创建两个 `LLMClient`，主客户端继续注入 ReAct/Plan 与长期记忆，摘要客户端仅注入 `ContextManager`。

方案目标：

- 摘要默认可使用更便宜、更快、低温度的模型，降低压缩成本和延迟。
- 摘要模型拥有独立的 `model`、`api_key`、`base_url`、`temperature`、`timeout`。
- 摘要 token 计数使用摘要模型名，避免主模型 tokenizer 与摘要模型不一致。
- 保持现有四层压缩顺序、摘要提示词、会话压缩事件和引擎行为不变。
- 不实现主模型回退，也不在一个 `LLMClient` 中动态切换模型。

## Current State Analysis

当前调用链为：

```text
AppConfig.llm
  -> CLI 创建一个 LLMClient
  -> 同一个客户端注入 Agent 引擎和 ContextManager
  -> ContextManager._compress()
  -> LLMSummaryCompression.summarize()
  -> 使用主对话模型生成历史摘要
```

关键实现位置：

- `src/sagent/config/models.py` 定义 `LLMConfig`，当前只有主模型配置。
- `src/sagent/config/models.py` 定义 `ContextConfig`，当前只控制摘要开关和摘要长度。
- `src/sagent/config/loader.py` 只处理 `LLM_API_KEY`、`LLM_API_URL`，并只校验主 LLM API Key。
- `src/sagent/cli/app.py` 创建共享 `LLMClient` 并同时注入 ContextManager 与 Agent 引擎。
- `src/sagent/context/context_manager.py` 将注入的 `llm` 实际用于摘要策略，将 `model` 用于 token 计数和摘要长度校验。
- `src/sagent/context/strategies.py` 已经通过构造函数接收摘要客户端，并且摘要请求固定不携带 tools，改动边界较小。
- `config.example.yaml` 是配置示例，需要增加摘要模型块和独立环境变量说明。
- `tests/unit/test_config.py` 覆盖现有配置读取和主模型环境变量，但没有摘要模型隔离测试。

现有架构已经采用“CLI 组装依赖、业务类消费依赖”的模式，因此无需修改 ReAct、Plan、Session 或 LLM 调用协议。

## Proposed Changes

### 1. 增加 Context 摘要模型配置模型

文件：`src/sagent/config/models.py`

实施内容：

- 在 `ContextConfig` 中增加 `summary_llm: LLMConfig | None`，配置结构放在 `context` 下，而不是新增顶层职责块。
- 复用 `LLMConfig` 的 `model`、`api_key`、`base_url`、`temperature`、`timeout` 字段，避免新增重复配置模型和不必要抽象。
- `summary_llm` 的 `model` 必须由用户显式提供；当 `enable_summary=True` 时，配置校验必须要求 `summary_llm` 存在。
- 当 `enable_summary=False` 时允许不配置 `summary_llm`，保证关闭摘要时不需要创建摘要客户端。
- 不把主 `llm` 自动复制为 `summary_llm`，不提供隐式回退。
- 为摘要模型建议使用低温度配置，例如示例配置设为 `0.2`；具体值由 YAML 显式控制。
- 保留现有 `summary_max_tokens`、`keep_recent_messages` 等语义不变。

### 2. 增加摘要模型环境变量覆盖和校验

文件：`src/sagent/config/loader.py`

实施内容：

- 增加明确的摘要环境变量常量：
  - `CONTEXT_SUMMARY_LLM_API_KEY` 覆盖 `context.summary_llm.api_key`
  - `CONTEXT_SUMMARY_LLM_API_URL` 覆盖 `context.summary_llm.base_url`
- 保持环境变量优先于 YAML 的现有规则。
- 在解析前安全地创建/读取 `context` 和 `summary_llm` 映射，仅在摘要配置块存在时应用摘要环境变量。
- 在最终配置校验后：
  - 主模型 API Key 继续按现有逻辑校验。
  - `enable_summary=True` 时要求 `summary_llm` 存在且其 API Key 非空，并在错误信息中指出摘要配置块和对应环境变量。
  - `enable_summary=False` 时不校验摘要 API Key，也不要求初始化摘要客户端。
- 不增加复用主 API Key、主 URL 或主模型的 fallback 逻辑。

### 3. 在 CLI 中组装独立摘要客户端

文件：`src/sagent/cli/app.py`（约第 256-298 行）

实施内容：

- 继续用 `config.llm` 创建主 `LLMClient`。
- 仅当 `config.context.enable_summary` 为真时，用 `config.context.summary_llm` 创建第二个 `LLMClient`。
- 将摘要客户端和摘要模型名注入 `ContextManager`。
- 主 `llm` 仍注入 `MemoryManager` 与 Agent 引擎，避免改变已有主模型调用路径。
- 摘要关闭时向 `ContextManager` 传入 `None`，保持不创建摘要策略的现有行为。
- 不修改 `build_engine`、ReActEngine、PlanEngine、SessionManager 的接口和逻辑。

### 4. 明确 ContextManager 的摘要依赖语义

文件：`src/sagent/context/context_manager.py`（约第 31-70 行）

实施内容：

- 将构造参数命名从泛化的 `llm`、`model` 调整为 `summary_llm`、`summary_model`，使其与真实用途一致。
- 更新构造函数文档、内部字段和 `LLMSummaryCompression` 初始化调用。
- 保留 token 校准使用的模型字段、四层压缩顺序、摘要回调、摘要异常处理以及滑动窗口兜底逻辑。
- 同步更新所有现有测试和构造调用；不增加主模型 fallback。

文件：`src/sagent/context/strategies.py`（约第 347-465 行）

实施内容：

- 保持 `LLMSummaryCompression` 的注入方式和摘要请求格式不变。
- 确认传入的 `model` 改为摘要模型名，用于 `_enforce_summary_limit()` 的 token 计数。
- 不让策略直接读取 `AppConfig`，不新增动态模型切换。
- 保持 `tools=None`，并保留现有摘要内容规则和硬性 token 限制。

### 5. 更新示例配置

文件：`config.example.yaml`（约第 43-62 行）

实施内容：

在 `context` 下增加：

```yaml
  summary_llm:
    model: "gpt-4o-mini"
    api_key: ""
    base_url: ""
    temperature: 0.2
    timeout: 60
```

同时补充：

- `CONTEXT_SUMMARY_LLM_API_KEY` 和 `CONTEXT_SUMMARY_LLM_API_URL` 的说明。
- 摘要模型建议使用更小、更快、更便宜的模型。
- `enable_summary=true` 时摘要模型配置为必填；关闭摘要时可省略该块。
- 不在示例中填写任何真实凭证。

### 6. 补充配置和上下文测试

文件：`tests/unit/test_config.py`

增加测试：

- 正确读取 `context.summary_llm` 的全部字段。
- 摘要模型和主模型的 model、API Key、URL、temperature、timeout 互不覆盖。
- 摘要环境变量覆盖 YAML 值。
- `enable_summary=True` 且缺少 `summary_llm` 时失败。
- `enable_summary=True` 且摘要 API Key 缺失时失败。
- `enable_summary=False` 时允许省略摘要模型配置。

文件：`tests/unit/test_context_manager.py`

调整现有构造参数命名，并新增验证：

- 摘要 FakeLLM 被用于摘要请求，主 FakeLLM 不会收到摘要调用。
- 摘要模型名用于摘要 token 限制计数。
- 摘要请求仍使用 `tools=None`。
- 既有 reset、压缩回调、tool call 配对和滑动窗口行为保持不变。

文件：`tests/engines/test_context_integration.py`

- 更新 ContextManager 的构造方式。
- 保证 ReAct/Plan 的主模型调用与摘要模型调用分别使用独立 FakeLLM 队列。
- 保证关闭摘要时不产生摘要模型调用。

如 CLI 装配层已有可直接测试的入口，则补充最小装配验证；否则不为测试新增生产抽象。

## Assumptions & Decisions

### 配置层级选择

采用 `context.summary_llm`，因为摘要模型是 context 压缩能力的实现依赖，不是 Agent 对话能力的第二个公共角色。这样与现有 `context.enable_summary`、`summary_max_tokens` 聚合，用户也能在一个区块内理解摘要行为。

### 模型选择策略

独立摘要模型默认应偏向小型、快速、低成本模型，temperature 建议低于主 Agent；但不在代码中写死具体供应商或模型名，示例只给出可替换值。摘要模型必须具备足够的事实保真能力，尤其要保留文件路径、命令、错误、数字、版本号和工具结果。

### 不做主模型 fallback

按照本任务约束，不配置摘要模型时不自动复用对话模型；启用摘要却缺少摘要配置应在加载阶段明确报错。这样可以避免用户误以为已实现成本/延迟隔离，也避免摘要请求意外消耗主模型配额。

### 保留已有压缩架构

业界主流方案普遍采用“接近上下文上限触发、保留系统消息与最近消息、摘要替换旧历史、工具结果先清理/卸载”的组合。当前项目已有四层策略和 tool-call 边界保护，本次只替换摘要模型依赖，不重构压缩算法。

### 业界依据

- LangChain 的上下文工程文档描述了使用单独 LLM 调用摘要旧消息、永久替换状态中的旧历史并保留最近消息的方案；Deep Agents 还将卸载和摘要组合为上下文管理层。
- Letta 的 compaction 配置支持独立 summarizer model、模型参数、摘要长度和 sliding-window 比例，并明确将更便宜/更快模型作为独立摘要模型的典型用途。
- Microsoft Agent Framework 的 compaction 文档明确要求单独的 LLM client 进行摘要，并建议使用更小、更快的模型，同时保护 system 消息、最近消息和完整 tool-call 原子组。
- Anthropic 当前文档优先推荐服务端 compaction；对于客户端自管历史的场景，仍是“旧历史生成摘要并替换”的模式。SAgent 使用 OpenAI 兼容客户端并本地持久化消息，因此当前客户端摘要架构仍然适用。

参考资料：

- [LangChain Context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
- [LangChain Deep Agents context compression](https://docs.langchain.com/oss/python/deepagents/context-engineering#context-compression)
- [Letta Compaction](https://docs.letta.com/guides/core-concepts/messages/compaction/)
- [Microsoft Agent Framework Compaction](https://learn.microsoft.com/en-gb/agent-framework/agents/conversations/compaction)
- [Anthropic Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)

## Verification Steps

1. 运行受影响的配置和 context 单元测试：

```powershell
python -m pytest tests/unit/test_config.py tests/unit/test_context_manager.py
```

2. 运行 context 引擎集成测试：

```powershell
python -m pytest tests/engines/test_context_integration.py
```

3. 运行全量离线测试：

```powershell
python -m pytest
```

4. 检查测试结果至少覆盖以下行为：

- 主 Agent 请求只走主模型客户端。
- 摘要请求只走摘要模型客户端。
- 摘要客户端使用独立 URL、API Key、temperature 和 timeout。
- 摘要 token 限制按摘要模型计数。
- 缺失摘要配置时没有静默 fallback。
- 四层压缩、tool-call 配对、会话压缩事件和既有引擎行为没有回归。

5. 检查修改后的 Python 与 YAML 文件均为 UTF-8，中文注释和配置说明没有乱码，且未读取、写入或提交 `config.yaml` 等真实凭证文件。

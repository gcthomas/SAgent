"""配置模型定义。

使用 pydantic 定义程序运行所需的配置结构，包含 LLM 配置与 Agent 配置。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    """大模型相关配置。

    支持任意 OpenAI 兼容的 API：通过 base_url 指向兼容服务地址。
    """

    # 模型名称，例如 gpt-4o-mini、deepseek-chat 等
    model: str = Field(..., description="要调用的模型名称")
    # API Key，可为空并由环境变量 LLM_API_KEY 覆盖
    api_key: str = Field(default="", description="LLM API Key")
    # API 基础地址，可为空并由环境变量 LLM_API_URL 覆盖；用于 OpenAI 兼容服务
    base_url: str = Field(default="", description="OpenAI 兼容 API 的基础地址")
    # 采样温度
    temperature: float = Field(default=0.7, description="采样温度")
    # 单次请求超时时间（秒）
    timeout: float = Field(default=60.0, description="请求超时时间（秒）")


class AgentConfig(BaseModel):
    """Agent 运行相关配置。"""

    # 默认执行模式：react 或 plan
    mode: Literal["react", "plan"] = Field(default="react", description="默认执行模式")
    # ReAct 循环的最大迭代次数
    max_iterations: int = Field(default=10, description="ReAct 循环最大迭代次数")


class LoggingConfig(BaseModel):
    """日志相关配置。"""

    # 是否启用日志系统
    enabled: bool = Field(default=True, description="是否启用日志系统")
    # 文件日志级别
    level: str = Field(default="DEBUG", description="文件日志级别")
    # 控制台日志级别
    console_level: str = Field(default="INFO", description="控制台日志级别")
    # 日志目录（相对运行目录）
    dir: str = Field(default="logs", description="日志文件所在目录")
    # 日志主文件名（按天滚动，归档形如 sagent.log.2026-07-10）
    file: str = Field(default="sagent.log", description="日志主文件名")
    # 保留的历史日志天数（滚动归档份数）
    backup_count: int = Field(default=7, description="按天滚动保留的历史份数")
    # 是否记录 LLM 完整请求/响应内容（关闭时仅记录摘要）
    log_llm_content: bool = Field(
        default=False, description="是否记录 LLM 完整请求/响应内容"
    )


class ContextConfig(BaseModel):
    """上下文管理配置。

    控制消息历史的 token 预算、压缩触发阈值与分层压缩策略参数。
    """

    # 模型上下文窗口大小（token 数）
    max_context_tokens: int = Field(default=128000, description="模型上下文窗口大小（token 数）")
    # 触发压缩的阈值占比（实际 token / max_context_tokens 超过此值时触发压缩）
    compression_threshold: float = Field(default=0.7, description="触发压缩的阈值占比（0-1）")
    # 安全线：压缩持续执行直到 token 降至 max_context_tokens * safe_threshold 以下
    safe_threshold: float = Field(default=0.5, description="压缩停止的安全线占比（0-1），需低于 compression_threshold")
    # 始终保留的最近消息条数（第四层滑动窗口裁剪使用）
    keep_recent_messages: int = Field(default=20, description="始终保留的最近消息条数")
    # 单条工具结果的最大 token 数，超过则截断（第一层工具输出截断使用）
    max_tool_output_tokens: int = Field(default=2000, description="单条工具结果的最大 token 数")
    # token 计数方式：auto / tiktoken / heuristic
    token_counter_method: Literal["auto", "tiktoken", "heuristic"] = Field(
        default="auto", description="token 计数方式：auto 自动选择、tiktoken 强制精确、heuristic 字符启发式"
    )
    # 是否使用 LLM API 返回的 prompt_tokens 做混合校准（启用后每次 LLM 调用自动记录精确 token 数，
    # 新增消息仅估算 delta，精度更高且不依赖 tiktoken）
    use_api_calibration: bool = Field(
        default=True, description="是否使用 LLM API 返回的 prompt_tokens 做混合校准"
    )
    # 是否启用第三层 LLM 摘要压缩
    enable_summary: bool = Field(default=True, description="是否启用 LLM 摘要压缩")
    # 摘要的最大 token 数（注入摘要提示词，约束 LLM 生成的摘要长度）
    summary_max_tokens: int = Field(default=500, description="生成的摘要最大 token 数，注入提示词约束 LLM 输出长度")


class SessionConfig(BaseModel):
    """会话管理配置。

    控制会话历史的持久化行为，包括数据库路径、全文索引与自动保存。
    """

    # 是否启用会话管理
    enabled: bool = Field(default=True, description="是否启用会话管理")
    # 会话数据库文件路径（相对运行目录）
    db_path: str = Field(default="sessions.db", description="会话数据库文件路径")
    # 是否启用 FTS5 全文索引（用于历史消息检索，不支持时自动降级为 LIKE 查询）
    enable_fts: bool = Field(default=True, description="是否启用 FTS5 全文索引")
    # 是否每轮问答后自动增量保存会话消息
    auto_save: bool = Field(default=True, description="是否每轮自动增量保存")


class MemoryConfig(BaseModel):
    """长期记忆配置。

    控制基于本地 Markdown 文件的长期记忆功能，包括开关、目录路径与字符上限。
    """

    # 是否启用长期记忆
    enabled: bool = Field(default=True, description="是否启用长期记忆")
    # 记忆文件所在目录（相对运行目录），两个 Markdown 文件均存于此目录
    dir: str = Field(default="memory", description="记忆文件所在目录（相对运行目录）")
    # 用户文件字符上限，超限触发反思整理
    user_max_chars: int = Field(default=2000, description="用户文件字符上限")
    # 记忆文件字符上限，超限触发反思整理
    memory_max_chars: int = Field(default=4000, description="记忆文件字符上限")


class ToolFilterConfig(BaseModel):
    """MCP 工具过滤配置。

    通过 allow / deny 两个列表控制白名单/黑名单，无需 mode 参数：
    - 均为空：不过滤，所有工具通过
    - allow 非空：只有 allow 列表中的工具通过（白名单）
    - deny 非空：deny 列表中的工具被拦截（黑名单）
    - 均非空：先白名单过滤，再从结果中移除 deny 中的工具
    """

    allow: list[str] = Field(default_factory=list, description="白名单工具名列表，非空时仅允许这些工具")
    deny: list[str] = Field(default_factory=list, description="黑名单工具名列表，非空时排除这些工具")


class MCPServerConfig(BaseModel):
    """单个 MCP 服务器配置。

    支持 stdio / sse / streamable_http 三种传输方式。
    """

    # 服务器名称（唯一标识，用于工具名前缀 mcp_{name}_{tool}）
    name: str = Field(..., description="服务器名称（唯一标识，用于工具名前缀）")
    # 传输方式
    transport: Literal["stdio", "sse", "streamable_http"] = Field(default="stdio", description="传输方式")
    # stdio 模式：命令与参数
    command: str = Field(default="", description="stdio 模式下要执行的命令")
    args: list[str] = Field(default_factory=list, description="stdio 模式下命令的参数列表")
    env: dict[str, str] = Field(default_factory=dict, description="stdio 模式下子进程的环境变量")
    cwd: str = Field(default="", description="stdio 模式下子进程的工作目录")
    # sse / streamable_http 模式
    url: str = Field(default="", description="sse / streamable_http 模式的服务器地址")
    # 服务器级开关
    enabled: bool = Field(default=True, description="是否启用该服务器（禁用则不连接）")
    # 工具过滤
    tool_filter: ToolFilterConfig = Field(default_factory=ToolFilterConfig, description="工具过滤配置")
    # 超时控制
    connect_timeout: float = Field(default=30.0, description="连接超时（秒，含握手），默认 30 秒")
    call_timeout: float = Field(default=60.0, description="工具调用超时（秒），默认 60 秒")

    @model_validator(mode="after")
    def validate_transport_fields(self) -> MCPServerConfig:
        """校验传输方式对应的必填字段：stdio 需 command，sse/streamable_http 需 url。"""
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio 传输方式需要配置 command 字段")
        if self.transport in ("sse", "streamable_http") and not self.url:
            raise ValueError(f"{self.transport} 传输方式需要配置 url 字段")
        return self


class MCPConfig(BaseModel):
    """MCP 配置。

    控制是否启用 MCP 工具提供者及其服务器列表。
    """

    enabled: bool = Field(default=False, description="是否启用 MCP 工具提供者")
    servers: list[MCPServerConfig] = Field(default_factory=list, description="MCP 服务器配置列表")


class ObservabilityConfig(BaseModel):
    """可观测性配置。

    控制 Span 树、指标聚合、脱敏、本地导出与可选 OTLP 导出。
    基于 OpenTelemetry SDK 引擎，安装 requirements.txt 即包含全部依赖。
    """
    # 是否启用增强观测（关闭时仅保留现有 trace_id 日志）
    enabled: bool = Field(default=True, description="是否启用增强观测")
    # 本地 Trace 文件目录（相对运行目录）
    trace_dir: str = Field(default="logs", description="本地 Trace 文件目录")
    # 本地 Trace 文件名（按天滚动）
    trace_file: str = Field(default="sagent_trace.jsonl", description="本地 Trace 文件名")
    # 本地 Metric 文件名（按天滚动）
    metric_file: str = Field(default="sagent_metrics.jsonl", description="本地 Metric 文件名")
    # 是否采集 LLM 内容（prompt/completion 等），最终有效值需同时满足此开关与 logging.log_llm_content
    capture_content: bool = Field(default=False, description="是否采集 LLM 诊断内容")
    # 采集内容的最大字符长度（超过则截断）
    content_max_length: int = Field(default=500, description="采集内容的最大字符长度")
    # 模型价格表：模型名 -> {input_price_per_million, output_price_per_million}（单位：元/百万 token）
    model_pricing: dict[str, dict[str, float]] = Field(default_factory=dict, description="模型价格表")
    # 是否启用 OTLP 导出（控制 OTLP 导出器是否加入 provider 链）
    otlp_enabled: bool = Field(default=False, description="控制 OTLP 导出器是否加入 provider 链")
    # OTLP endpoint 地址
    otlp_endpoint: str = Field(default="http://localhost:4318", description="OTLP endpoint 地址")
    # OTLP 协议
    otlp_protocol: Literal["http", "grpc"] = Field(default="http", description="OTLP 协议")
    # OTLP 导出超时（秒）
    otlp_timeout: float = Field(default=10.0, description="OTLP 导出超时（秒）")
    # 指标刷新周期（秒），控制本地 Metric 快照写入频率
    metrics_flush_interval: float = Field(default=60.0, description="指标刷新周期（秒）")
    # 是否启用 OpenAI SDK 自动埋点（实验性，默认关闭，首期禁止与手动 gen_ai.chat Span 同时启用）
    enable_openai_auto_instrumentation: bool = Field(default=False, description="是否启用 OpenAI SDK 自动埋点（实验性，默认关闭）")


class PermissionConfig(BaseModel):
    """工具调用权限控制配置。

    控制执行期权限闸门：三态决策（allow / ask / deny）与审批行为，以及
    覆盖内置默认规则的用户规则列表。规则格式为 "工具名" 或 "工具名:参数模式"
    （fnmatch 通配，如 "run_shell:git push*"），同一请求命中多条规则时最后
    一条生效（last-match-wins），用户显式 allow 可覆盖内置 deny。
    """

    # 是否启用权限控制（关闭后工具全自动执行，回到旧行为）
    enabled: bool = Field(default=True, description="是否启用工具调用权限控制")
    # 审批等待超时（秒），超时按 fail-safe 默认拒绝；缺省 600 秒对齐业界询问等待尺度
    ask_timeout: float = Field(
        default=600.0, ge=0, description="审批等待超时（秒），超时默认拒绝（fail-safe），须为非负数"
    )
    # 非交互终端（无 TTY）或审批超时时的动作：deny 默认拒绝 / allow 显式放行
    non_interactive: Literal["deny", "allow"] = Field(
        default="deny", description="非交互终端或审批超时时的动作"
    )
    # 放行规则列表（工具名或 "工具名:参数模式"）
    allow: list[str] = Field(default_factory=list, description="放行规则列表")
    # 拒绝规则列表
    deny: list[str] = Field(default_factory=list, description="拒绝规则列表")
    # 需确认规则列表
    ask: list[str] = Field(default_factory=list, description="需确认规则列表")

    @model_validator(mode="after")
    def validate_rules(self) -> PermissionConfig:
        """校验规则列表格式："工具名" 或 "工具名:参数模式"，工具名非空。"""
        for texts in (self.allow, self.deny, self.ask):
            for text in texts:
                stripped = text.strip()
                if not stripped or not stripped.partition(":")[0].strip():
                    raise ValueError(
                        f"权限规则格式非法: {text!r}，应为 \"工具名\" 或 \"工具名:参数模式\""
                    )
        return self


class AppConfig(BaseModel):
    """应用总配置。"""

    llm: LLMConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig, description="上下文管理配置")
    session: SessionConfig = Field(default_factory=SessionConfig, description="会话管理配置")
    memory: MemoryConfig = Field(default_factory=MemoryConfig, description="长期记忆配置")
    mcp: MCPConfig = Field(default_factory=MCPConfig, description="MCP 配置")
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig, description="可观测性配置")
    permissions: PermissionConfig = Field(default_factory=PermissionConfig, description="权限控制配置")

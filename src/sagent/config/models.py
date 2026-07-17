"""配置模型定义。

使用 pydantic 定义程序运行所需的配置结构，包含 LLM 配置与 Agent 配置。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class AppConfig(BaseModel):
    """应用总配置。"""

    llm: LLMConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig, description="上下文管理配置")

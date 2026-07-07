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


class AppConfig(BaseModel):
    """应用总配置。"""

    llm: LLMConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)

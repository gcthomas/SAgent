"""工具基础定义。

- Tool: 工具抽象基类，参数使用 pydantic 模型定义 schema，可转换为 OpenAI function calling 格式。
- ToolProvider: 工具提供者抽象接口，为未来 MCP / skill 等外部工具来源预留接入点。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Type

from pydantic import BaseModel


class Tool(ABC):
    """工具抽象基类。

    子类需要指定:
        name: 工具名称（唯一）。
        description: 工具用途描述，供 LLM 理解。
        args_schema: 参数的 pydantic 模型类，用于生成 JSON schema 与校验参数。
    并实现 run 方法执行具体逻辑。
    """

    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel]

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 所需的 tool schema。"""
        parameters = self.args_schema.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def validate_args(self, arguments: dict[str, Any]) -> BaseModel:
        """使用 pydantic 校验并解析参数，返回参数模型实例。"""
        return self.args_schema(**arguments)

    @abstractmethod
    def run(self, args: BaseModel) -> str:
        """执行工具逻辑，返回结果字符串。

        参数:
            args: 已通过 pydantic 校验的参数模型实例。
        """
        raise NotImplementedError


class ToolProvider(ABC):
    """工具提供者抽象接口（预留）。

    未来的 MCP 客户端、skill 加载器可实现该接口，以统一方式向注册表提供工具。
    本阶段仅定义接口，不包含具体实现。
    """

    @abstractmethod
    def provide_tools(self) -> list[Tool]:
        """返回该提供者可提供的工具列表。"""
        raise NotImplementedError

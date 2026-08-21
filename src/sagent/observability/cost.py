"""成本估算模块。

使用配置的模型单价表计算 LLM 调用的估算成本。
缺少价格或 usage 时标记为不可用，不伪造数值。
"""

from __future__ import annotations

# 模块级价格表（由 setup_cost_estimator 初始化）
_pricing: dict[str, dict[str, float]] = {}


def setup_cost_estimator(pricing: dict[str, dict[str, float]]) -> None:
    """初始化成本估算器，传入模型价格表。

    参数:
        pricing: 模型价格表，格式为
            {"model_name": {"input_price_per_million": float, "output_price_per_million": float}}，
            单位为元/百万 token。
    """
    global _pricing
    _pricing = pricing


def calculate_cost(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """根据模型单价表和 token 用量计算估算成本。

    参数:
        model: 模型名称，用于在 pricing 表中查找单价。
        input_tokens: 输入 token 数，为 None 时返回 None。
        output_tokens: 输出 token 数，为 None 时返回 None。
        pricing: 模型价格表，为 None 时使用模块级 _pricing（由 setup_cost_estimator 设置）。
            格式为 {"model_name": {"input_price_per_million": float, "output_price_per_million": float}}，
            单位为元/百万 token。

    返回:
        估算成本（float，保留 6 位小数）；缺少价格或 usage 时返回 None。
    """
    # 缺少 usage 数据时标记为不可用
    if input_tokens is None or output_tokens is None:
        return None
    # 选择价格表：优先使用传入参数，其次使用模块级价格表
    actual_pricing = pricing if pricing is not None else _pricing
    # 模型不在价格表中时标记为不可用
    if model not in actual_pricing:
        return None
    model_pricing = actual_pricing[model]
    input_price = model_pricing.get("input_price_per_million")
    output_price = model_pricing.get("output_price_per_million")
    # 价格字段缺失时标记为不可用
    if input_price is None or output_price is None:
        return None
    cost = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
    return round(cost, 6)

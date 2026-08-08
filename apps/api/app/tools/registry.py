from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    adapter_method: str
    risk_level: Literal["read", "write"]
    required_params: tuple[str, ...]
    timeout_seconds: float
    max_retries: int


TOOL_REGISTRY = {
    "pdd.get_order": ToolDefinition("pdd.get_order", "get_order", "read", ("order_id",), 5.0, 2),
    "pdd.get_logistics": ToolDefinition("pdd.get_logistics", "get_logistics", "read", ("order_id",), 5.0, 2),
    "pdd.get_stock": ToolDefinition("pdd.get_stock", "get_stock", "read", ("product_id",), 5.0, 2),
    "pdd.create_after_sales": ToolDefinition("pdd.create_after_sales", "create_after_sales", "write", ("order_id", "action", "reason"), 5.0, 0),
}

ACTION_TO_TOOL = {
    "order_query": "pdd.get_order",
    "logistics_query": "pdd.get_logistics",
    "stock_query": "pdd.get_stock",
    "refund_only": "pdd.get_order",
    "return_refund": "pdd.get_order",
    "exchange_request": "pdd.get_order",
}
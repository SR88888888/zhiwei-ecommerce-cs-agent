from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolExecutionResult(BaseModel):
    execution_id: str
    tool_name: str
    status: Literal["success", "business_error", "retryable_error", "system_error"]
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    duration_ms: int = 0


class ToolExecutionAudit(BaseModel):
    execution_id: str
    run_id: str
    tool_name: str
    risk_level: Literal["read", "write"]
    status: str
    retry_count: int
    duration_ms: int
    params_summary: dict[str, str]
    result_summary: dict[str, str]
    error_code: str | None = None


class PersistedAfterSalesPreview(BaseModel):
    preview_id: str
    user_id: str
    session_id: str
    order_id: str
    action: str
    reason: str
    amount: float
    status: Literal["pending", "submitted", "expired"] = "pending"
    expires_at: str
    idempotency_key_hash: str | None = None
    after_sales_id: str | None = None
class _PlatformOutput(BaseModel):
    model_config = {"extra": "allow"}


class OrderOutput(_PlatformOutput):
    order_id: str
    status: str


class LogisticsOutput(_PlatformOutput):
    order_id: str
    status: str


class StockOutput(_PlatformOutput):
    product_id: str
    stock: int


class AfterSalesOutput(_PlatformOutput):
    after_sales_id: str
    status: str


OUTPUT_MODELS = {
    "pdd.get_order": OrderOutput,
    "pdd.get_logistics": LogisticsOutput,
    "pdd.get_stock": StockOutput,
    "pdd.create_after_sales": AfterSalesOutput,
}
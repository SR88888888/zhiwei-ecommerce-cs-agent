from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    CHITCHAT = "chitchat"
    KNOWLEDGE_QUERY = "knowledge_query"
    ORDER_SERVICE = "order_service"
    AFTER_SALES = "after_sales"
    HUMAN_HANDOFF = "human_handoff"


class NextStep(str, Enum):
    DIRECT_REPLY = "direct_reply"
    RETRIEVE_KNOWLEDGE = "retrieve_knowledge"
    CALL_PLATFORM_API = "call_platform_api"
    COLLECT_SLOTS = "collect_slots"
    COLLECT_CLARIFICATION = "collect_clarification"
    PREPARE_AFTER_SALES = "prepare_after_sales"
    TRANSFER_TO_HUMAN = "transfer_to_human"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str
    session_id: str
    attachment_text: str | None = Field(default=None, max_length=12000)
    attachment_id: str | None = None


class RouteDecision(BaseModel):
    intent: Intent
    action: str
    slots: dict[str, str] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    next_step: NextStep
    reason_code: str


class Citation(BaseModel):
    source_id: str
    title: str
    content: str
    document_id: str | None = None
    chunk_id: str | None = None
    source_type: str | None = None
    score: float | None = None
    model: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class ChatResponse(BaseModel):
    run_id: str
    answer: str
    route: RouteDecision
    citations: list[Citation] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    handoff_ticket_id: str | None = None


class EvaluationTrace(BaseModel):
    retrieved_source_ids: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_params: dict[str, str] = Field(default_factory=dict)
    tool_ok: bool | None = None
    tool_status: str | None = None
    tool_error_code: str | None = None
    tool_retry_count: int = 0
    tool_execution_id: str | None = None
    handoff_created: bool = False
    after_sales_preview_created: bool = False
    terminal_state: str = "reply"
    react_steps: list[dict[str, Any]] = Field(default_factory=list)
    react_stop_reason: str | None = None
    route_duration_ms: int = 0
    react_duration_ms: int = 0
    response_safety_blocked: bool = False


class TracedChatRun(BaseModel):
    response: ChatResponse
    trace: EvaluationTrace


class AfterSalesPreviewRequest(BaseModel):
    user_id: str
    session_id: str
    order_id: str
    reason: str = Field(min_length=2, max_length=500)
    action: Literal["refund_only", "return_refund", "exchange_request"]


class AfterSalesPreview(BaseModel):
    preview_id: str
    order_id: str
    action: str
    reason: str
    amount: float
    requires_confirmation: bool = True


class AfterSalesConfirmRequest(BaseModel):
    user_id: str
    preview_id: str
    idempotency_key: str = Field(min_length=8, max_length=128)


class TicketUpdate(BaseModel):
    status: Literal["open", "in_progress", "closed"]
    agent_reply: str | None = Field(default=None, max_length=2000)
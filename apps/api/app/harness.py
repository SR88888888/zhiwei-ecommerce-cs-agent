import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.intent_router import router
from app.controlled_react import ControlledReActAgent
from app.memory import memory
from app.multimodal import attachment_service
from app.pdd_adapter import PddPlatformAdapter, get_pdd_adapter
from app.rag import rag_service
from app.schemas import AfterSalesPreview, ChatRequest, ChatResponse, Citation, EvaluationTrace, Intent, NextStep, TracedChatRun
from app.tools.audit import ToolAuditStore, tool_audit_store
from app.tools.executor import ToolExecutor
from app.tools.registry import ACTION_TO_TOOL

SLOT_LABELS = {
    "order_id": "\u8ba2\u5355\u53f7",
    "product_id": "\u5546\u54c1\u7f16\u53f7",
}


@dataclass
class HandoffTicket:
    ticket_id: str
    user_id: str
    session_id: str
    reason: str
    status: str = "open"
    agent_reply: str | None = None
    handoff_package: dict | None = None


class AgentHarness:
    def __init__(self, adapter_factory: Callable[[], PddPlatformAdapter] = get_pdd_adapter, audit_store: ToolAuditStore = tool_audit_store) -> None:
        self.audit_store = audit_store
        self.executor = ToolExecutor(adapter_factory, audit_store)
        self.react_agent = ControlledReActAgent(self.executor)
        self.tickets: dict[str, HandoffTicket] = {}

    async def run(self, request: ChatRequest) -> ChatResponse:
        return (await self._run(request)).response

    async def run_with_trace(self, request: ChatRequest) -> TracedChatRun:
        return await self._run(request)

    async def _run(self, request: ChatRequest) -> TracedChatRun:
        if request.attachment_id:
            attachment = attachment_service.get(request.attachment_id, request.user_id, request.session_id)
            if attachment and attachment["status"] == "ready":
                request = request.model_copy(update={"attachment_text": attachment["ocr_text"]})
        run_id = str(uuid.uuid4())
        trace = EvaluationTrace()
        router_context = await memory.load_router_context(request.user_id, request.session_id)
        route_started = time.perf_counter()
        route_state = {**router_context.workflow_state, "conversation_summary": router_context.conversation_summary}
        route = await router.route(request.message, route_state, request.attachment_text)
        trace.route_duration_ms = int((time.perf_counter() - route_started) * 1000)
        await memory.append_message(request.user_id, request.session_id, "user", request.message)
        await memory.capture_explicit_memory(request.user_id, request.message)
        agent_context = await memory.build_agent_context(request.user_id, request.session_id, request.message, route.intent.value)
        citations: list[Citation] = []
        suggested_actions: list[str] = []
        ticket_id: str | None = None

        if route.next_step == NextStep.DIRECT_REPLY:
            if route.action == "temporarily_unavailable":
                answer = "\u5f53\u524d\u6682\u65f6\u65e0\u6cd5\u56de\u7b54\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"
            else:
                answer = "您好，我可以帮您查询拼多多订单、物流、售后和商品规则。"
            trace.terminal_state = "direct_reply"
        elif route.next_step == NextStep.COLLECT_CLARIFICATION:
            await memory.save_workflow_state(
                request.user_id,
                request.session_id,
                phase="awaiting_after_sales_scope",
                action=route.action,
                **route.slots,
            )
            answer = "\u60a8\u662f\u60f3\u4e86\u89e3\u9000\u8d27\u89c4\u5219\uff0c\u8fd8\u662f\u8981\u4e3a\u67d0\u7b14\u8ba2\u5355\u7533\u8bf7\u552e\u540e\uff1f\u5982\u9700\u7533\u8bf7\u552e\u540e\uff0c\u8bf7\u63d0\u4f9b\u8ba2\u5355\u53f7\u3002"
            trace.terminal_state = "clarification"
        elif route.next_step == NextStep.COLLECT_SLOTS:
            await memory.save_workflow_state(
                request.user_id,
                request.session_id,
                phase="awaiting_required_slots",
                action=route.action,
                intent=route.intent.value,
                **route.slots,
            )
            labels = [SLOT_LABELS.get(slot, slot) for slot in route.missing_slots]
            answer = "\u8bf7\u8865\u5145\u4ee5\u4e0b\u4fe1\u606f：{}。".format("\u3001".join(dict.fromkeys(labels)))
            trace.terminal_state = "slot_collection"
        elif route.intent == Intent.KNOWLEDGE_QUERY:
            answer, citations = await rag_service.answer(request.message)
            trace.retrieved_source_ids = [item.source_id for item in citations]
            if agent_context.semantic_memories:
                answer += "\n本次回答已参考您授权保存的偏好。"
            trace.terminal_state = "knowledge_answered"
        elif route.intent == Intent.HUMAN_HANDOFF:
            ticket_id = self.create_ticket(request.user_id, request.session_id, route.reason_code, {
                "user_question": request.message,
                "collected_slots": route.slots,
                "tool_observations": [],
                "recommended_action": "?????????",
            })
            answer = "您的请求已转交人工客服，请稍候。"
            trace.handoff_created = True
            trace.terminal_state = "handoff_created"
        elif route.intent == Intent.ORDER_SERVICE:
            tool_name = ACTION_TO_TOOL.get(route.action)
            react_started = time.perf_counter()
            react_run = await self.react_agent.run(run_id, request.user_id, request.message, tool_name or "", route.slots)
            trace.react_duration_ms = int((time.perf_counter() - react_started) * 1000)
            trace.react_stop_reason = react_run.stop_reason
            trace.react_steps = [
                {"tool_name": item.tool_name, "status": item.status, "ok": item.ok, "execution_id": item.execution_id}
                for item in react_run.steps
            ]
            if react_run.steps:
                result = react_run.steps[-1]
                self._apply_tool_trace(trace, result, {"user_id": request.user_id, **route.slots})
                trace.terminal_state = "platform_result" if result.ok else "platform_error"
                answer = react_run.answer or self.format_platform_result(result.data, result.error_message)
            elif react_run.handoff_requested:
                ticket_id = self.create_ticket(request.user_id, request.session_id, "react_handoff", {
                    "user_question": request.message,
                    "collected_slots": route.slots,
                    "tool_observations": trace.react_steps,
                    "recommended_action": "?????????????",
                })
                answer = "????????????????"
                trace.handoff_created = True
                trace.terminal_state = "handoff_created"
            else:
                answer = "??????????????????"
                trace.terminal_state = "platform_error"
        elif route.intent == Intent.AFTER_SALES:
            if route.action == "after_sales_status":
                answer = "请提供订单号，我才能查询售后状态。"
                trace.terminal_state = "after_sales_status_prompt"
            else:
                preview, result = await self.prepare_preview_with_result(run_id, request.user_id, request.session_id, route.slots["order_id"], route.action, "Customer after-sales request")
                self._apply_tool_trace(trace, result, {"user_id": request.user_id, "order_id": route.slots["order_id"]})
                trace.after_sales_preview_created = preview is not None
                trace.terminal_state = "after_sales_preview" if preview else "platform_error"
                if preview:
                    answer = f"订单 {preview.order_id} 的售后预览已生成，请确认后提交。"
                    suggested_actions = ["confirm_after_sales"]
                else:
                    answer = self.format_platform_result({}, result.error_message)
        else:
            ticket_id = self.create_ticket(request.user_id, request.session_id, "unsupported_route")
            answer = "当前请求暂时无法自动处理，已为您转交人工客服。"
            trace.handoff_created = True
            trace.terminal_state = "handoff_created"

        await memory.append_message(request.user_id, request.session_id, "assistant", answer)
        response = ChatResponse(run_id=run_id, answer=answer, route=route, citations=citations, suggested_actions=suggested_actions, handoff_ticket_id=ticket_id)
        return TracedChatRun(response=response, trace=trace)

    @staticmethod
    def _apply_tool_trace(trace: EvaluationTrace, result, params: dict[str, str]) -> None:
        trace.tool_name = result.tool_name
        trace.tool_params = {key: str(value) for key, value in params.items() if key in {"user_id", "order_id", "product_id", "action"}}
        trace.tool_ok = result.ok
        trace.tool_status = result.status
        trace.tool_error_code = result.error_code
        trace.tool_retry_count = result.retry_count
        trace.tool_execution_id = result.execution_id

    @staticmethod
    def format_platform_result(data: dict, error: str | None) -> str:
        if error:
            return f"查询失败：{error}，请稍后重试。"
        if "latest_trace" in data:
            return f"Order {data['order_id']} is {data['status']}. {data['carrier']} {data['tracking_no']}; latest trace: {data['latest_trace']}."
        if "stock" in data:
            return f"{data['name']}: price {data['price']}, stock {data['stock']}."
        return f"Order {data['order_id']} is {data['status']}; item {data.get('item', '')}; amount {data.get('amount', '')}."

    async def prepare_preview_with_result(self, run_id: str, user_id: str, session_id: str, order_id: str, action: str, reason: str):
        result = await self.executor.execute(run_id, "pdd.get_order", user_id, {"order_id": order_id})
        if not result.ok:
            return None, result
        preview = AfterSalesPreview(preview_id=str(uuid.uuid4()), order_id=order_id, action=action, reason=reason, amount=float(result.data["amount"]))
        await self.audit_store.save_preview(self.audit_store.new_preview(preview.preview_id, user_id, session_id, order_id, action, reason, preview.amount))
        await memory.save_workflow_state(user_id, session_id, phase="awaiting_after_sales_confirmation", order_id=order_id, action=action, reason=reason)
        return preview, result

    async def prepare_preview(self, user_id: str, session_id: str, order_id: str, action: str, reason: str) -> AfterSalesPreview:
        preview, result = await self.prepare_preview_with_result(str(uuid.uuid4()), user_id, session_id, order_id, action, reason)
        if not preview:
            raise ValueError(result.error_message or "暂时无法生成售后预览")
        return preview

    async def confirm_preview(self, user_id: str, preview_id: str, idempotency_key: str) -> dict:
        preview = await self.audit_store.load_preview(preview_id)
        if not preview or preview.user_id != user_id:
            raise ValueError("售后预览不存在，或不属于当前用户")
        if datetime.fromisoformat(preview.expires_at) <= datetime.now(timezone.utc) and preview.status == "pending":
            preview.status = "expired"
            raise ValueError("售后预览已过期，请重新发起申请")
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        if preview.status == "submitted":
            if preview.idempotency_key_hash == key_hash and preview.after_sales_id:
                return {"after_sales_id": preview.after_sales_id, "status": "accepted", "idempotent": True}
            raise ValueError("该售后预览已经提交过了")
        result = await self.executor.execute(str(uuid.uuid4()), "pdd.create_after_sales", user_id, {"order_id": preview.order_id, "action": preview.action, "reason": preview.reason})
        if not result.ok:
            raise ValueError(result.error_message or "售后申请提交失败")
        after_sales_id = str(result.data["after_sales_id"])
        await self.audit_store.mark_preview_submitted(preview_id, idempotency_key, after_sales_id)
        await memory.clear_workflow_state(user_id, preview.session_id)
        return result.data

    def create_ticket(self, user_id: str, session_id: str, reason: str, handoff_package: dict | None = None) -> str:
        ticket_id = f"HT-{uuid.uuid4().hex[:8].upper()}"
        self.tickets[ticket_id] = HandoffTicket(ticket_id, user_id, session_id, reason, handoff_package=handoff_package)
        return ticket_id


harness = AgentHarness()
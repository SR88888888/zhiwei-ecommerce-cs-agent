import asyncio
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.harness import harness
from app.rag import rag_service
from app.pgvector_store import pgvector_store
from app.memory import memory
from app.memory_api import router as memory_router
from app.knowledge_api import router as knowledge_router
from app.schemas import AfterSalesConfirmRequest, AfterSalesPreviewRequest, ChatRequest, TicketUpdate
from app.tools.audit import tool_audit_store

app = FastAPI(title="Zhiwei PDD Customer Service API", version="2.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(memory_router)
app.include_router(knowledge_router)

DEMO_USERS = [
    {"id": "buyer_001", "name": "Buyer One", "orders": ["PDD20260806001"]},
    {"id": "buyer_002", "name": "Buyer Two", "orders": ["PDD20260806002"]},
]


@app.on_event("startup")
async def startup_memory() -> None:
    await memory.startup()
    await tool_audit_store.startup()
    await rag_service.startup()


@app.on_event("shutdown")
async def shutdown_memory() -> None:
    await rag_service.shutdown()
    await tool_audit_store.shutdown()
    await memory.shutdown()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "pgvector_available": pgvector_store.available, "pgvector_error": pgvector_store.last_error}


@app.get("/api/v1/demo-users")
async def demo_users() -> list[dict]:
    return DEMO_USERS


@app.post("/api/v1/sessions")
async def create_session() -> dict:
    return {"session_id": str(uuid.uuid4())}


@app.post("/api/v1/sessions/{session_id}/messages")
async def send_message(session_id: str, request: ChatRequest):
    if session_id != request.session_id:
        raise HTTPException(400, "session_id mismatch")
    return await harness.run(request)


@app.post("/api/v1/sessions/{session_id}/messages:stream")
async def stream_message(session_id: str, request: ChatRequest):
    if session_id != request.session_id:
        raise HTTPException(400, "session_id mismatch")

    async def event_stream():
        yield "event: status\ndata: " + json.dumps({"phase": "routing"}) + "\n\n"
        result = await harness.run(request)
        yield "event: route\ndata: " + result.route.model_dump_json() + "\n\n"
        for chunk in result.answer:
            yield "event: message_delta\ndata: " + json.dumps({"delta": chunk}) + "\n\n"
            await asyncio.sleep(0.005)
        yield "event: completed\ndata: " + result.model_dump_json() + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/v1/after-sales/preview")
async def preview_after_sales(request: AfterSalesPreviewRequest):
    try:
        return await harness.prepare_preview(request.user_id, request.session_id, request.order_id, request.action, request.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/after-sales/confirm")
async def confirm_after_sales(request: AfterSalesConfirmRequest):
    try:
        return await harness.confirm_preview(request.user_id, request.preview_id, request.idempotency_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/agent/tickets")
async def list_tickets() -> list[dict]:
    return [ticket.__dict__ for ticket in harness.tickets.values()]


@app.patch("/api/v1/agent/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, update: TicketUpdate) -> dict:
    ticket = harness.tickets.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    ticket.status = update.status
    ticket.agent_reply = update.agent_reply
    return ticket.__dict__
@app.get("/api/v1/agent/runs/{run_id}/tool-executions")
async def list_tool_executions(run_id: str) -> list[dict]:
    records = await tool_audit_store.list_executions(run_id)
    return [record.model_dump() for record in records]
@app.get("/api/v1/evaluation/latest")
async def latest_evaluation() -> dict:
    report_path = Path("artifacts/evaluation/latest.json")
    if not report_path.exists():
        raise HTTPException(404, "evaluation report not found")
    return json.loads(report_path.read_text(encoding="utf-8"))
@app.post("/api/v1/evaluation/run")
async def run_latest_evaluation() -> dict:
    from app.evaluation.dataset import load_dataset
    from app.evaluation.runner import run_evaluation, write_reports
    dataset_path = Path(__file__).parents[1] / "evals" / "customer_service_v1.jsonl"
    result = await run_evaluation(load_dataset(dataset_path))
    write_reports(result, Path(__file__).parents[1] / "artifacts" / "evaluation")
    return result
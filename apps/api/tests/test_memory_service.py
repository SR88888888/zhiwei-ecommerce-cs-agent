import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.memory.service import MemoryService


def test_explicit_consent_creates_semantic_memory():
    async def check() -> None:
        service = MemoryService()
        await service.set_consent("buyer_001", True)
        record = await service.capture_explicit_memory("buyer_001", "记住我喜欢黑色")
        assert record is not None
        assert record.content == "喜欢黑色"
        assert len(await service.list_memories("buyer_001")) == 1

    asyncio.run(check())


def test_workflow_checkpoint_resumes_after_sales_state():
    async def check() -> None:
        service = MemoryService()
        await service.save_workflow_state(
            "buyer_001",
            "session-memory",
            phase="awaiting_after_sales_confirmation",
            order_id="PDD20260806001",
        )
        context = await service.load_router_context("buyer_001", "session-memory")
        assert context.workflow_state["order_id"] == "PDD20260806001"

    asyncio.run(check())

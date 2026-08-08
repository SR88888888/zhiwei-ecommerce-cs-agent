import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.harness import AgentHarness
from app.pdd_adapter import MockPddPlatformAdapter
from app.tools.audit import ToolAuditStore
from app.tools.executor import ToolExecutor


class RetryOnceAdapter(MockPddPlatformAdapter):
    calls = 0

    async def get_order(self, user_id: str, order_id: str):
        type(self).calls += 1
        if type(self).calls == 1:
            raise TimeoutError()
        return await super().get_order(user_id, order_id)


class FailingWriteAdapter(MockPddPlatformAdapter):
    calls = 0

    async def create_after_sales(self, user_id: str, order_id: str, action: str, reason: str):
        type(self).calls += 1
        raise TimeoutError()


def test_unregistered_tool_is_rejected_and_audited():
    async def check():
        store = ToolAuditStore()
        result = await ToolExecutor(MockPddPlatformAdapter, store).execute("11111111-1111-1111-1111-111111111111", "pdd.delete_order", "buyer_001", {})
        records = await store.list_executions("11111111-1111-1111-1111-111111111111")
        assert result.ok is False
        assert result.error_code == "tool_not_allowed"
        assert len(records) == 1
    asyncio.run(check())


def test_read_tool_retries_but_write_tool_does_not():
    async def check():
        RetryOnceAdapter.calls = 0
        read = await ToolExecutor(RetryOnceAdapter, ToolAuditStore()).execute(
            "22222222-2222-2222-2222-222222222222", "pdd.get_order", "buyer_001", {"order_id": "PDD20260806001"}
        )
        assert read.ok is True
        assert read.retry_count == 1
        FailingWriteAdapter.calls = 0
        write = await ToolExecutor(FailingWriteAdapter, ToolAuditStore()).execute(
            "33333333-3333-3333-3333-333333333333", "pdd.create_after_sales", "buyer_001",
            {"order_id": "PDD20260806001", "action": "refund_only", "reason": "test"},
        )
        assert write.ok is False
        assert write.retry_count == 0
        assert FailingWriteAdapter.calls == 1
    asyncio.run(check())


def test_after_sales_confirmation_is_idempotent():
    async def check():
        harness = AgentHarness(adapter_factory=MockPddPlatformAdapter, audit_store=ToolAuditStore())
        preview = await harness.prepare_preview("buyer_001", "tool-test", "PDD20260806001", "refund_only", "test")
        first = await harness.confirm_preview("buyer_001", preview.preview_id, "idempotency-key-001")
        second = await harness.confirm_preview("buyer_001", preview.preview_id, "idempotency-key-001")
        assert first["after_sales_id"] == second["after_sales_id"]
        assert second["idempotent"] is True
    asyncio.run(check())
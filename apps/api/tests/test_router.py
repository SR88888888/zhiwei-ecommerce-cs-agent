import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.intent_router import router
from app.schemas import Intent, NextStep


def test_routes_pdd_logistics_to_readonly_platform_call():
    result = asyncio.run(router.route("帮我查 PDD20260806001 的物流", {}))
    assert result.intent == Intent.ORDER_SERVICE
    assert result.action == "logistics_query"
    assert result.next_step == NextStep.CALL_PLATFORM_API
    assert result.slots["order_id"] == "PDD20260806001"


def test_routes_refund_to_confirmation_workflow():
    result = asyncio.run(router.route("我要仅退款 PDD20260806001", {}))
    assert result.intent == Intent.AFTER_SALES
    assert result.action == "refund_only"
    assert result.next_step == NextStep.PREPARE_AFTER_SALES
    assert result.risk_level == "high"


def test_explicit_handoff_overrides_other_routes():
    result = asyncio.run(router.route("我要投诉并转人工", {}))
    assert result.intent == Intent.HUMAN_HANDOFF
    assert result.next_step == NextStep.TRANSFER_TO_HUMAN

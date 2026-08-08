import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import asyncio

from app.evaluation.dataset import EvaluationCase, load_dataset
from app.evaluation.judge import DeepSeekRagJudge
from app.evaluation.scorers import score_case
from app.harness import AgentHarness
from app.schemas import ChatRequest, ChatResponse, Citation, EvaluationTrace, Intent, NextStep, RouteDecision, TracedChatRun


def test_dataset_has_one_hundred_balanced_cases():
    cases = load_dataset("evals/customer_service_v1.jsonl")
    assert len(cases) == 100
    counts = {intent: sum(case.expected_intent == intent for case in cases) for intent in {"chitchat", "knowledge_query", "order_service", "after_sales", "human_handoff"}}
    assert all(counts[intent] > 0 for intent in counts)
    assert counts["knowledge_query"] == 60


def test_recall_and_rag_points_are_scored_without_judge():
    case = EvaluationCase(
        case_id="unit-rag", message="question", session_id="unit-rag", expected_intent="knowledge_query",
        expected_source_id="group_coupon_rules", required_points=["coupon"], forbidden_points=["forbidden"],
    )
    route = RouteDecision(intent=Intent.KNOWLEDGE_QUERY, action="faq", confidence=1, next_step=NextStep.RETRIEVE_KNOWLEDGE, reason_code="unit")
    response = ChatResponse(run_id="unit", answer="coupon is available", route=route, citations=[Citation(source_id="group_coupon_rules", title="rule", content="coupon")])
    run = TracedChatRun(response=response, trace=EvaluationTrace(retrieved_source_ids=["group_coupon_rules"], terminal_state="knowledge_answered"))
    scores = asyncio.run(score_case(case, run, DeepSeekRagJudge()))
    assert scores.intent_passed is True
    assert scores.recall_passed is True
    assert scores.rag_passed is True


def test_normal_harness_response_does_not_expose_trace():
    response = asyncio.run(AgentHarness().run(ChatRequest(message="hi", user_id="buyer_001", session_id="normal-contract")))
    assert "trace" not in response.model_dump()
class _JudgeWithScore:
    async def score(self, question, answer, required_points, forbidden_points):
        return 4


def test_rag_ambiguous_answer_uses_judge_fallback():
    case = EvaluationCase(
        case_id="unit-judge", message="question", session_id="unit-judge", expected_intent="knowledge_query",
        expected_source_id="group_coupon_rules", required_points=["missing-point"],
    )
    route = RouteDecision(intent=Intent.KNOWLEDGE_QUERY, action="faq", confidence=1, next_step=NextStep.RETRIEVE_KNOWLEDGE, reason_code="unit")
    response = ChatResponse(run_id="unit", answer="partial answer", route=route)
    run = TracedChatRun(response=response, trace=EvaluationTrace(retrieved_source_ids=["group_coupon_rules"], terminal_state="knowledge_answered"))
    scores = asyncio.run(score_case(case, run, _JudgeWithScore()))
    assert scores.rag_passed is True


def test_tool_and_task_scores_require_expected_trace():
    case = EvaluationCase(
        case_id="unit-tool", message="order", session_id="unit-tool", expected_intent="order_service",
        expected_tool="pdd.get_order", expected_tool_params={"user_id": "buyer_001", "order_id": "PDD20260806001"},
        expected_terminal_state="platform_result",
    )
    route = RouteDecision(intent=Intent.ORDER_SERVICE, action="order_query", confidence=1, slots={"order_id": "PDD20260806001"}, next_step=NextStep.CALL_PLATFORM_API, reason_code="unit")
    response = ChatResponse(run_id="unit", answer="ok", route=route)
    trace = EvaluationTrace(tool_name="pdd.get_order", tool_params={"user_id": "buyer_001", "order_id": "PDD20260806001"}, tool_ok=True, terminal_state="platform_result")
    scores = asyncio.run(score_case(case, TracedChatRun(response=response, trace=trace), _JudgeWithScore()))
    assert scores.tool_passed is True
    assert scores.task_passed is True

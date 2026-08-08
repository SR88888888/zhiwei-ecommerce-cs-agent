from dataclasses import dataclass

from app.evaluation.dataset import EvaluationCase
from app.evaluation.judge import DeepSeekRagJudge
from app.schemas import TracedChatRun


@dataclass
class CaseScores:
    intent_passed: bool
    tool_applicable: bool
    tool_passed: bool | None
    recall_applicable: bool
    recall_passed: bool | None
    rag_applicable: bool
    rag_passed: bool | None
    task_applicable: bool
    task_passed: bool | None
    rag_judge_score: int | None
    rag_judge_reason: str | None
    reasons: list[str]


async def score_case(
    case: EvaluationCase,
    run: TracedChatRun,
    judge: DeepSeekRagJudge,
    first_run: TracedChatRun | None = None,
) -> CaseScores:
    reasons: list[str] = []
    intent_passed = run.response.route.intent.value == case.expected_intent
    if not intent_passed:
        reasons.append(f"intent expected={case.expected_intent} actual={run.response.route.intent.value}")

    tool_applicable = case.expected_tool is not None
    tool_passed: bool | None = None
    if tool_applicable:
        tool_passed = (
            run.trace.tool_name == case.expected_tool
            and run.trace.tool_ok is True
            and (run.trace.tool_status in (None, "success"))
        )
        for key, expected in case.expected_tool_params.items():
            tool_passed = tool_passed and run.trace.tool_params.get(key) == expected
        if not tool_passed:
            reasons.append(
                f"tool expected={case.expected_tool} actual={run.trace.tool_name} "
                f"ok={run.trace.tool_ok} execution={run.trace.tool_execution_id}"
            )

    recall_applicable = case.expected_source_id is not None or case.expected_model is not None
    recall_passed: bool | None = None
    if recall_applicable:
        top_citations = run.response.citations[:2]
        source_matched = (
            case.expected_source_id in run.trace.retrieved_source_ids[:2]
            if case.expected_source_id
            else any(item.model == case.expected_model for item in top_citations)
        )
        evidence = "\n".join(f"{item.title}\n{item.content}" for item in top_citations).lower()
        point_matched = any(point.lower() in evidence for point in case.required_points) if case.required_points else True
        recall_passed = source_matched and point_matched
        if not recall_passed:
            reasons.append(
                f"recall missing_model_or_point model={case.expected_source_id or case.expected_model} "
                f"points={case.required_points} actual={run.trace.retrieved_source_ids}"
            )
    rag_applicable = case.expected_source_id is not None or case.expected_model is not None
    rag_passed: bool | None = None
    rag_judge_score: int | None = None
    rag_judge_reason: str | None = None
    if rag_applicable:
        try:
            judgment = await judge.score(
                case.message,
                run.response.answer,
                case.required_points,
                case.reference_answer,
                case.forbidden_points,
                [item.title for item in run.response.citations],
            )
        except TypeError:
            judgment = await judge.score(
                case.message,
                run.response.answer,
                case.required_points,
                case.forbidden_points,
            )
        if isinstance(judgment, int):
            rag_judge_score = judgment
            rag_judge_reason = "unit judge"
            rag_passed = judgment >= 4
        elif judgment is None:
            answer_text = run.response.answer.lower()
            required_ok = all(point.lower() in answer_text for point in case.required_points)
            forbidden_ok = not any(point.lower() in answer_text for point in case.forbidden_points)
            rag_passed = required_ok and forbidden_ok
            rag_judge_reason = "local rule fallback"
            if not rag_passed:
                reasons.append("rag judge unavailable")
        else:
            rag_judge_score = judgment.score
            rag_judge_reason = judgment.reason
            rag_passed = judgment.score >= 4
            if not rag_passed:
                reasons.append(f"rag judge score={judgment.score}: {judgment.reason}")

    task_applicable = case.expected_terminal_state is not None
    task_passed: bool | None = None
    if task_applicable:
        task_passed = run.trace.terminal_state == case.expected_terminal_state
        if not task_passed:
            reasons.append(f"terminal expected={case.expected_terminal_state} actual={run.trace.terminal_state}")

    first_turn_passed = True
    if case.expected_first_terminal_state:
        first_turn_passed = first_run is not None and first_run.trace.terminal_state == case.expected_first_terminal_state
        if not first_turn_passed:
            actual = first_run.trace.terminal_state if first_run else None
            reasons.append(f"first terminal expected={case.expected_first_terminal_state} actual={actual}")
    if case.expected_first_no_tool:
        no_tool = first_run is not None and first_run.trace.tool_name is None and first_run.trace.tool_execution_id is None
        first_turn_passed = first_turn_passed and no_tool
        if not no_tool:
            actual = first_run.trace.tool_name if first_run else None
            reasons.append(f"first turn should not execute a tool actual={actual}")
    if task_passed is not None:
        task_passed = task_passed and first_turn_passed

    return CaseScores(
        intent_passed,
        tool_applicable,
        tool_passed,
        recall_applicable,
        recall_passed,
        rag_applicable,
        rag_passed,
        task_applicable,
        task_passed,
        rag_judge_score,
        rag_judge_reason,
        reasons,
    )



import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.dataset import EvaluationCase, load_dataset
from app.evaluation.judge import DeepSeekRagJudge
from app.evaluation.scorers import CaseScores, score_case
from app.harness import AgentHarness
from app.pdd_adapter import DatabasePddPlatformAdapter
from app.schemas import ChatRequest
from app.usage import start_tracking, stop_tracking

THRESHOLDS = {
    "intent_routing_accuracy": 0.90,
    "tool_execution_success_rate": 0.95,
    "recall_at_2": 0.90,
    "rag_answer_accuracy": 0.85,
    "task_completion_rate": 0.75,
}
METRIC_APPLICABILITY = {
    "intent_routing_accuracy": "intent_passed",
    "tool_execution_success_rate": "tool_applicable",
    "recall_at_2": "recall_applicable",
    "rag_answer_accuracy": "rag_applicable",
    "task_completion_rate": "task_applicable",
}


def metric(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "score": round(passed / total, 4) if total else 0.0}


def duration_summary(records: list[dict]) -> dict:
    durations = [item["duration_ms"] for item in records]
    return {
        "average_ms": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "min_ms": min(durations) if durations else 0.0,
        "max_ms": max(durations) if durations else 0.0,
    }


def cost_summary(records: list[dict]) -> dict:
    known = [item["cost_cny"] for item in records if item["cost_cny"] is not None]
    return {
        "currency": "CNY",
        "total": round(sum(known), 6) if known else None,
        "priced_records": len(known),
        "unpriced_records": len(records) - len(known),
        "status": "available" if len(known) == len(records) else "partial" if known else "unavailable",
        "message": "仅汇总供应商返回的实际用量费用；未返回 usage 的本地或第三方调用不估算成本。",
    }


async def run_evaluation(cases: list[EvaluationCase]) -> dict:
    harness = AgentHarness(adapter_factory=DatabasePddPlatformAdapter)
    judge = DeepSeekRagJudge()
    records: list[dict] = []
    started = time.perf_counter()
    for case in cases:
        if case.initial_workflow_state:
            from app.memory import memory
            await memory.save_workflow_state(case.user_id, case.session_id, **case.initial_workflow_state)
        case_started = time.perf_counter()
        tracker, token = start_tracking()
        turns = []
        try:
            for message in case.messages():
                request = ChatRequest(message=message, user_id=case.user_id, session_id=case.session_id, attachment_text=case.attachment_text)
                turns.append(await harness.run_with_trace(request))
            run = turns[-1]
            agent_duration_ms = round((time.perf_counter() - case_started) * 1000, 2)
            judge_started = time.perf_counter()
            scores: CaseScores = await score_case(case, run, judge, turns[0] if len(turns) > 1 else None)
            judge_duration_ms = round((time.perf_counter() - judge_started) * 1000, 2)
        finally:
            stop_tracking(token)
        duration_ms = agent_duration_ms
        known_costs = [item["cost_cny"] for item in tracker.records if item["cost_cny"] is not None]
        record_cost = round(sum(known_costs), 8) if known_costs else None
        records.append({
            "case_id": case.case_id,
            "message": case.message,
            "expected": case.model_dump(exclude={"message", "user_id", "session_id", "attachment_text", "initial_workflow_state"}),
            "scores": asdict(scores),
            "route": run.response.route.model_dump(mode="json"),
            "trace": run.trace.model_dump(mode="json"),
            "answer": run.response.answer,
            "citations": [item.model_dump(mode="json") for item in run.response.citations],
            "duration_ms": duration_ms,
            "judge_duration_ms": judge_duration_ms,
            "cost_cny": record_cost,
            "cost_status": "available" if record_cost is not None else "unavailable",
            "usage": tracker.records,
            "turns": [{"message": message, "answer": item.response.answer, "trace": item.trace.model_dump(mode="json")} for message, item in zip(case.messages(), turns)],
        })
    metric_records = {
        "intent_routing_accuracy": records,
        "tool_execution_success_rate": [item for item in records if item["scores"]["tool_applicable"]],
        "recall_at_2": [item for item in records if item["scores"]["recall_applicable"]],
        "rag_answer_accuracy": [item for item in records if item["scores"]["rag_applicable"]],
        "task_completion_rate": [item for item in records if item["scores"]["task_applicable"]],
    }
    score_fields = {
        "intent_routing_accuracy": "intent_passed",
        "tool_execution_success_rate": "tool_passed",
        "recall_at_2": "recall_passed",
        "rag_answer_accuracy": "rag_passed",
        "task_completion_rate": "task_passed",
    }
    metrics = {name: metric(sum(item["scores"][score_fields[name]] is True for item in items), len(items)) for name, items in metric_records.items()}
    coverage = {name: len(items) for name, items in metric_records.items()}
    gates = {name: values["score"] >= THRESHOLDS[name] for name, values in metrics.items()}
    gates["metric_coverage"] = all(coverage.values())
    failures = [{"case_id": item["case_id"], "reasons": item["scores"]["reasons"]} for item in records if item["scores"]["reasons"]]
    total_duration_ms = round((time.perf_counter() - started) * 1000, 2)
    duration = duration_summary(records)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "total_cases": len(cases), "metrics": metrics, "coverage": coverage, "thresholds": THRESHOLDS, "gates": gates, "passed": all(gates.values()), "failures": failures, "records": records, "duration_ms": total_duration_ms, "average_duration_ms": duration["average_ms"], "duration": duration, "cost": cost_summary(records)}


def write_reports(result: dict, output_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(output_dir); directory.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = directory / "latest.json", directory / "latest.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Customer Service Agent Evaluation", "", f"Total cases: {result['total_cases']}", f"Duration: {result['duration_ms']} ms", f"Average duration: {result['average_duration_ms']} ms", f"Cost: {result['cost']['total'] if result['cost']['total'] is not None else 'unavailable'} {result['cost']['currency']}", "", "| Metric | Score | Coverage | Threshold | Gate |", "|---|---:|---:|---:|---|"]
    for name, values in result["metrics"].items():
        lines.append(f"| {name} | {values['score']:.2%} ({values['passed']}/{values['total']}) | {result['coverage'][name]} | {result['thresholds'][name]:.0%} | {'PASS' if result['gates'][name] else 'FAIL'} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run customer-service evaluation")
    parser.add_argument("--dataset", required=True); parser.add_argument("--output-dir", default="artifacts/evaluation"); parser.add_argument("--gate", action="store_true")
    args = parser.parse_args(); result = asyncio.run(run_evaluation(load_dataset(args.dataset))); write_reports(result, args.output_dir)
    print(f"Evaluation completed: {result['total_cases']} cases")
    return 1 if args.gate and not result["passed"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
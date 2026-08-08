import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class EvaluationCase(BaseModel):
    case_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    user_id: str = "buyer_001"
    session_id: str = Field(min_length=1)
    attachment_text: str | None = None
    initial_workflow_state: dict[str, str] = Field(default_factory=dict)
    expected_intent: str
    expected_tool: str | None = None
    expected_tool_params: dict[str, str] = Field(default_factory=dict)
    turns: list[str] = Field(default_factory=list)
    expected_first_terminal_state: str | None = None
    expected_first_no_tool: bool = False
    expected_source_id: str | None = None
    expected_model: str | None = None
    required_points: list[str] = Field(default_factory=list)
    reference_answer: str = ""
    forbidden_points: list[str] = Field(default_factory=list)
    expected_terminal_state: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> "EvaluationCase":
        if (self.expected_source_id or self.expected_model) and self.expected_intent != "knowledge_query":
            raise ValueError("expected knowledge source is only valid for knowledge_query cases")
        if self.expected_tool and not self.expected_terminal_state:
            raise ValueError("tool cases must declare expected_terminal_state")
        if self.turns and self.turns[0] != self.message:
            raise ValueError("the first turn must match message")
        if (self.expected_first_terminal_state or self.expected_first_no_tool) and len(self.turns) < 2:
            raise ValueError("first-turn assertions require a multi-turn case")
        return self

    def messages(self) -> list[str]:
        return self.turns or [self.message]


def load_dataset(path: str | Path) -> list[EvaluationCase]:
    source = Path(path)
    if not source.exists() and not source.is_absolute():
        source = Path(__file__).resolve().parents[2] / source
    cases: list[EvaluationCase] = []
    case_ids: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(line)
        except Exception as exc:
            raise ValueError(f"Invalid evaluation case at line {line_number}: {exc}") from exc
        if case.case_id in case_ids:
            raise ValueError(f"Duplicate evaluation case_id: {case.case_id}")
        case_ids.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    return cases


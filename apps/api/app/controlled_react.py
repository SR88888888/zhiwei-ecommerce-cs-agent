import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings
from app.safety import mask_sensitive, plain_customer_text, safe_observation, validate_customer_answer
from app.tools.executor import ToolExecutor
from app.tools.registry import TOOL_REGISTRY
from app.tools.schemas import ToolExecutionResult
from app.usage import record_usage


MAX_REACT_STEPS = 3
READ_TOOL_NAMES = ("pdd.get_order", "pdd.get_logistics")
FUNCTION_NAME_TO_TOOL = {"pdd_get_order": "pdd.get_order", "pdd_get_logistics": "pdd.get_logistics"}
TOOL_TO_FUNCTION_NAME = {tool_name: function_name for function_name, tool_name in FUNCTION_NAME_TO_TOOL.items()}


@dataclass
class ReActRun:
    steps: list[ToolExecutionResult]
    answer: str | None
    stop_reason: str
    handoff_requested: bool = False


class ControlledReActAgent:
    def __init__(self, executor: ToolExecutor) -> None:
        self.executor = executor

    @staticmethod
    def _observations(steps: list[ToolExecutionResult]) -> list[dict[str, Any]]:
        return [
            {
                "tool": item.tool_name,
                "ok": item.ok,
                "status": item.status,
                "data": safe_observation(item.data),
                "error": item.error_message,
            }
            for item in steps
        ]

    @staticmethod
    def _tool_definitions(allowed_tools: tuple[str, ...]) -> list[dict[str, Any]]:
        descriptions = {
            "pdd.get_order": "Query one order that belongs to the current buyer.",
            "pdd.get_logistics": "Query logistics for one order that belongs to the current buyer.",
        }
        definitions: list[dict[str, Any]] = []
        for tool_name in allowed_tools:
            definition = TOOL_REGISTRY[tool_name]
            definitions.append({
                "type": "function",
                "function": {
                    "name": TOOL_TO_FUNCTION_NAME[tool_name],
                    "description": descriptions[tool_name],
                    "parameters": {
                        "type": "object",
                        "properties": {name: {"type": "string"} for name in definition.required_params},
                        "required": list(definition.required_params),
                        "additionalProperties": False,
                    },
                },
            })
        return definitions

    @staticmethod
    def _tool_call_params(raw_arguments: str | None) -> dict[str, str]:
        try:
            data = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return {key: value.strip() for key, value in data.items() if isinstance(key, str) and isinstance(value, str) and value.strip()}

    async def _function_call_decision(
        self,
        question: str,
        observations: list[dict[str, Any]],
        allowed_tools: tuple[str, ...],
        force_tool: str | None = None,
    ) -> tuple[str, dict[str, str]] | None:
        settings = get_settings()
        if not settings.deepseek_api_key or not settings.llm_enabled:
            return None
        payload = {
            "question": mask_sensitive(question),
            "observations": observations,
            "instruction": "Use a function only when its result is needed. Never invent parameters. If observations are sufficient, do not call a function.",
        }
        tool_choice: str | dict[str, Any] = "auto"
        if force_tool:
            tool_choice = {"type": "function", "function": {"name": TOOL_TO_FUNCTION_NAME[force_tool]}}
        client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=settings.llm_timeout_seconds)
        for _ in range(2):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=settings.deepseek_model,
                        messages=[
                            {"role": "system", "content": "Use only the supplied functions when a tool is needed."},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        tools=self._tool_definitions(allowed_tools),
                        tool_choice=tool_choice,
                        temperature=0,
                        max_tokens=80,
                    ),
                    timeout=settings.llm_timeout_seconds,
                )
                record_usage(settings.deepseek_model, response.usage)
                calls = response.choices[0].message.tool_calls or []
                if not calls:
                    return None
                call = calls[0]
                tool_name = FUNCTION_NAME_TO_TOOL.get(call.function.name)
                if tool_name in allowed_tools:
                    return tool_name, self._tool_call_params(call.function.arguments)
            except Exception:
                continue
        return None

    async def _final_answer(self, question: str, observations: list[dict[str, Any]]) -> str | None:
        settings = get_settings()
        if not settings.deepseek_api_key or not settings.llm_enabled:
            return None
        payload = {"question": mask_sensitive(question), "observations": observations}
        system = (
            "?????????????????? Observation ???????????????"
            "???????????????????????????Observation??????????"
            "??????????????????????"
        )
        client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=settings.llm_timeout_seconds)
        for _ in range(3):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=settings.deepseek_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        temperature=0,
                    ),
                    timeout=settings.llm_timeout_seconds,
                )
                record_usage(settings.deepseek_model, response.usage)
                answer = (response.choices[0].message.content or "").strip()
                if answer:
                    safe_answer = validate_customer_answer(question, answer)
                    return safe_answer or plain_customer_text(answer)
            except Exception:
                continue
        return None

    async def run(self, run_id: str, user_id: str, question: str, initial_tool: str, params: dict[str, str]) -> ReActRun:
        initial_definition = TOOL_REGISTRY.get(initial_tool)
        if not initial_definition or initial_definition.risk_level != "read":
            return ReActRun([], None, "initial_tool_not_allowed")
        allowed_tools = tuple(name for name in READ_TOOL_NAMES if name in TOOL_REGISTRY)
        if initial_tool not in allowed_tools:
            allowed_tools = (initial_tool,)
        steps: list[ToolExecutionResult] = []
        executed: set[str] = set()
        next_tool = initial_tool
        selected_params = dict(params)
        stop_reason = "max_steps"
        handoff_requested = False
        initial_call = await self._function_call_decision(question, [], allowed_tools, force_tool=initial_tool)
        if initial_call:
            next_tool, model_params = initial_call
            selected_params = {**model_params, **params}

        for _ in range(MAX_REACT_STEPS):
            if next_tool not in allowed_tools or next_tool in executed:
                stop_reason = "tool_not_allowed_or_repeated"
                break
            definition = TOOL_REGISTRY[next_tool]
            safe_params = {name: selected_params[name] for name in definition.required_params if name in selected_params}
            result = await self.executor.execute(run_id, next_tool, user_id, safe_params)
            steps.append(result)
            executed.add(next_tool)
            if not result.ok:
                stop_reason = "tool_error"
                break
            decision = await self._function_call_decision(question, self._observations(steps), allowed_tools)
            if not decision:
                stop_reason = "model_finished"
                break
            next_tool, model_params = decision
            selected_params = {**model_params, **params}
        answer = await self._final_answer(question, self._observations(steps)) if steps and not handoff_requested else None
        return ReActRun(steps=steps, answer=answer, stop_reason=stop_reason, handoff_requested=handoff_requested)

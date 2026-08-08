import asyncio
import time
import uuid
from typing import Callable

from app.pdd_adapter import PddPlatformAdapter
from app.tools.audit import ToolAuditStore
from app.tools.registry import TOOL_REGISTRY, ToolDefinition
from app.tools.schemas import OUTPUT_MODELS, ToolExecutionAudit, ToolExecutionResult


class ToolExecutor:
    def __init__(self, adapter_factory: Callable[[], PddPlatformAdapter], audit_store: ToolAuditStore) -> None:
        self.adapter_factory = adapter_factory
        self.audit_store = audit_store

    async def execute(self, run_id: str, tool_name: str, user_id: str, params: dict[str, str]) -> ToolExecutionResult:
        definition = TOOL_REGISTRY.get(tool_name)
        if not definition:
            return await self._result(run_id, tool_name, "read", "system_error", False, params, {}, "tool_not_allowed", "Tool is not registered", 0, 0)
        missing = [name for name in definition.required_params if not params.get(name)]
        if missing:
            return await self._result(run_id, tool_name, definition.risk_level, "business_error", False, params, {}, "missing_parameter", ",".join(missing), 0, 0)
        safe_params = {name: str(params[name]) for name in definition.required_params}
        attempts = definition.max_retries + 1
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(self._invoke(definition, user_id, safe_params), timeout=definition.timeout_seconds)
                duration_ms = int((time.perf_counter() - started) * 1000)
                if result.ok:
                    try:
                        output = OUTPUT_MODELS[tool_name].model_validate(result.data).model_dump()
                    except Exception as exc:
                        return await self._result(run_id, tool_name, definition.risk_level, "system_error", False, safe_params, {}, "output_validation_error", type(exc).__name__, attempt, duration_ms)
                    return await self._result(run_id, tool_name, definition.risk_level, "success", True, safe_params, output, None, None, attempt, duration_ms)
                return await self._result(run_id, tool_name, definition.risk_level, "business_error", False, safe_params, {}, "platform_business_error", result.error or "平台请求失败", attempt, duration_ms)
            except (TimeoutError, asyncio.TimeoutError):
                duration_ms = int((time.perf_counter() - started) * 1000)
                if attempt + 1 == attempts:
                    return await self._result(run_id, tool_name, definition.risk_level, "retryable_error", False, safe_params, {}, "tool_timeout", "平台请求超时", attempt, duration_ms)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                if attempt + 1 == attempts:
                    return await self._result(run_id, tool_name, definition.risk_level, "system_error", False, safe_params, {}, "tool_system_error", type(exc).__name__, attempt, duration_ms)
        raise RuntimeError("Tool executor reached an invalid state")

    async def _invoke(self, definition: ToolDefinition, user_id: str, params: dict[str, str]):
        adapter = self.adapter_factory()
        if definition.name == "pdd.get_order":
            return await adapter.get_order(user_id, params["order_id"])
        if definition.name == "pdd.get_logistics":
            return await adapter.get_logistics(user_id, params["order_id"])
        if definition.name == "pdd.get_stock":
            return await adapter.get_stock(params["product_id"])
        if definition.name == "pdd.create_after_sales":
            return await adapter.create_after_sales(user_id, params["order_id"], params["action"], params["reason"])
        raise ValueError("Unreachable tool mapping")

    async def _result(self, run_id: str, tool_name: str, risk_level: str, status: str, ok: bool, params: dict[str, str], data: dict, error_code: str | None, error_message: str | None, retry_count: int, duration_ms: int) -> ToolExecutionResult:
        execution_id = str(uuid.uuid4())
        result = ToolExecutionResult(
            execution_id=execution_id, tool_name=tool_name, status=status, ok=ok, data=data,
            error_code=error_code, error_message=error_message, retry_count=retry_count, duration_ms=duration_ms,
        )
        summary = {key: str(value)[:120] for key, value in data.items() if key in {"order_id", "product_id", "status", "after_sales_id", "stock"}}
        await self.audit_store.record_execution(ToolExecutionAudit(
            execution_id=execution_id, run_id=run_id, tool_name=tool_name, risk_level=risk_level, status=status,
            retry_count=retry_count, duration_ms=duration_ms, params_summary=params, result_summary=summary, error_code=error_code,
        ))
        return result
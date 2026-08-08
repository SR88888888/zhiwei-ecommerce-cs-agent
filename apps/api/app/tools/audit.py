import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.core import get_settings
from app.tools.schemas import PersistedAfterSalesPreview, ToolExecutionAudit


def _as_dict(value: object) -> dict[str, object]:
    """兼容 asyncpg 在不同 JSONB 编解码配置下返回的值。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_datetime(value: str) -> datetime:
    """将接口层保存的 ISO 时间转换为 PostgreSQL 所需的时间对象。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ToolAuditStore:
    def __init__(self) -> None:
        self.pool = None
        self.executions: dict[str, list[ToolExecutionAudit]] = {}
        self.previews: dict[str, PersistedAfterSalesPreview] = {}

    async def startup(self) -> None:
        database_url = get_settings().database_url
        if not database_url:
            return
        try:
            import asyncpg
            self.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
        except Exception:
            self.pool = None

    async def shutdown(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def record_execution(self, audit: ToolExecutionAudit) -> None:
        self.executions.setdefault(audit.run_id, []).append(audit)
        if not self.pool:
            return
        await self.pool.execute(
            """INSERT INTO tool_executions (execution_id, run_id, tool_name, risk_level, status, retry_count, duration_ms, params_summary, result_summary, error_code)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)""",
            audit.execution_id, audit.run_id, audit.tool_name, audit.risk_level, audit.status, audit.retry_count,
            audit.duration_ms, json.dumps(audit.params_summary), json.dumps(audit.result_summary), audit.error_code,
        )

    async def list_executions(self, run_id: str) -> list[ToolExecutionAudit]:
        if not self.pool:
            return list(self.executions.get(run_id, []))
        rows = await self.pool.fetch("""SELECT execution_id, run_id, tool_name, risk_level, status, retry_count, duration_ms,
            params_summary, result_summary, error_code FROM tool_executions WHERE run_id=$1::uuid ORDER BY created_at""", run_id)
        return [ToolExecutionAudit(
            execution_id=str(row["execution_id"]), run_id=str(row["run_id"]), tool_name=row["tool_name"], risk_level=row["risk_level"],
            status=row["status"], retry_count=row["retry_count"], duration_ms=row["duration_ms"],
            params_summary=_as_dict(row["params_summary"]), result_summary=_as_dict(row["result_summary"]), error_code=row["error_code"],
        ) for row in rows]

    async def save_preview(self, preview: PersistedAfterSalesPreview) -> None:
        self.previews[preview.preview_id] = preview
        if not self.pool:
            return
        await self.pool.execute(
            """INSERT INTO after_sales_previews (preview_id, user_id, session_id, order_id, action, reason, amount, status, expires_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9::timestamptz)""",
            preview.preview_id, preview.user_id, preview.session_id, preview.order_id, preview.action, preview.reason,
            preview.amount, preview.status, _as_datetime(preview.expires_at),
        )

    async def load_preview(self, preview_id: str) -> PersistedAfterSalesPreview | None:
        preview = self.previews.get(preview_id)
        if preview:
            return preview
        if not self.pool:
            return None
        row = await self.pool.fetchrow("""SELECT preview_id, user_id, session_id, order_id, action, reason, amount, status, expires_at,
            idempotency_key_hash, after_sales_id FROM after_sales_previews WHERE preview_id=$1::uuid""", preview_id)
        if not row:
            return None
        return PersistedAfterSalesPreview(
            preview_id=str(row["preview_id"]), user_id=row["user_id"], session_id=row["session_id"], order_id=row["order_id"],
            action=row["action"], reason=row["reason"], amount=float(row["amount"]), status=row["status"],
            expires_at=row["expires_at"].isoformat(), idempotency_key_hash=row["idempotency_key_hash"], after_sales_id=row["after_sales_id"],
        )

    async def mark_preview_submitted(self, preview_id: str, idempotency_key: str, after_sales_id: str) -> None:
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        preview = await self.load_preview(preview_id)
        if preview:
            preview.status = "submitted"
            preview.idempotency_key_hash = key_hash
            preview.after_sales_id = after_sales_id
        if self.pool:
            await self.pool.execute("""UPDATE after_sales_previews SET status='submitted', idempotency_key_hash=$2,
                after_sales_id=$3, submitted_at=NOW() WHERE preview_id=$1::uuid""", preview_id, key_hash, after_sales_id)

    @staticmethod
    def new_preview(preview_id: str, user_id: str, session_id: str, order_id: str, action: str, reason: str, amount: float) -> PersistedAfterSalesPreview:
        return PersistedAfterSalesPreview(
            preview_id=preview_id, user_id=user_id, session_id=session_id, order_id=order_id, action=action,
            reason=reason, amount=amount, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        )


tool_audit_store = ToolAuditStore()
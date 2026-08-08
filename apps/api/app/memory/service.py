import re
import uuid
from datetime import datetime, timezone

from app.core import get_settings
from app.memory.repository import InMemoryMemoryRepository, PostgresMemoryRepository
from app.memory.schemas import AgentMemoryContext, MemoryRecord, RouterMemoryContext


class MemoryService:
    def __init__(self) -> None:
        self._fallback = InMemoryMemoryRepository()
        self._repository = self._fallback
        self._postgres: PostgresMemoryRepository | None = None

    async def startup(self) -> None:
        settings = get_settings()
        database_url = getattr(settings, "database_url", "")
        if not database_url:
            return
        try:
            postgres = PostgresMemoryRepository(database_url)
            await postgres.connect()
            self._postgres = postgres
            self._repository = postgres
        except Exception:
            self._repository = self._fallback

    async def shutdown(self) -> None:
        if self._postgres:
            await self._postgres.close()

    async def load_router_context(self, user_id: str, session_id: str) -> RouterMemoryContext:
        return RouterMemoryContext(
            workflow_state=await self._repository.load_checkpoint(user_id, session_id),
            recent_messages=await self._repository.recent_messages(user_id, session_id, limit=24),
            conversation_summary=await self._repository.load_summary(user_id, session_id),
        )

    async def build_agent_context(self, user_id: str, session_id: str, query: str, intent: str, shop_id: str = "default") -> AgentMemoryContext:
        router_context = await self.load_router_context(user_id, session_id)
        if not await self._repository.has_consent(user_id):
            return AgentMemoryContext(router_context.workflow_state, router_context.recent_messages, router_context.conversation_summary, [], [])
        semantic = await self._repository.search_memories(user_id, shop_id, "semantic", query, limit=3)
        episodic = [] if intent in {"order_service", "human_handoff"} else await self._repository.search_memories(user_id, shop_id, "episodic", query, limit=2)
        return AgentMemoryContext(router_context.workflow_state, router_context.recent_messages, router_context.conversation_summary, semantic, episodic)

    async def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        await self._repository.append_message(user_id, session_id, role, content)
        if role == "assistant":
            await self._refresh_summary(user_id, session_id)

    async def _refresh_summary(self, user_id: str, session_id: str) -> None:
        messages = await self._repository.recent_messages(user_id, session_id, limit=200)
        if len(messages) <= 24:
            return
        older = messages[:-24]
        lines = []
        for item in older[-32:]:
            content = re.sub(r"\s+", " ", item["content"]).strip()
            if content:
                lines.append(("??" if item["role"] == "user" else "??") + "?" + content[:180])
        summary = "?".join(lines)[-4000:]
        await self._repository.save_summary(user_id, session_id, summary, len(older))

    async def save_workflow_state(self, user_id: str, session_id: str, **state: str) -> None:
        current = await self._repository.load_checkpoint(user_id, session_id)
        current.update(state)
        await self._repository.save_checkpoint(user_id, session_id, current)

    async def clear_workflow_state(self, user_id: str, session_id: str) -> None:
        await self._repository.clear_checkpoint(user_id, session_id)

    async def set_consent(self, user_id: str, enabled: bool) -> None:
        await self._repository.set_consent(user_id, enabled)
        if not enabled:
            await self._repository.retract_all_memories(user_id)

    async def list_memories(self, user_id: str) -> list[MemoryRecord]:
        return await self._repository.list_memories(user_id)

    async def forget_memory(self, user_id: str, memory_id: str) -> bool:
        return await self._repository.retract_memory(user_id, memory_id)

    async def capture_explicit_memory(self, user_id: str, text: str, shop_id: str = "default") -> MemoryRecord | None:
        if not await self._repository.has_consent(user_id):
            return None
        match = re.search(r"(?:\u8bf7)?\u8bb0\u4f4f(?:\u6211)?(.+)", text.strip())
        if not match:
            return None
        content = match.group(1).strip("锛屻€傦紒! ")
        if not content or self._contains_sensitive_value(content):
            return None
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()), user_id=user_id, shop_id=shop_id, kind="semantic", content=content,
            source="user_explicit", importance=0.8, confidence=1.0, created_at=datetime.now(timezone.utc), valid_from=datetime.now(timezone.utc),
        )
        await self._repository.add_memory(record)
        return record

    @staticmethod
    def _contains_sensitive_value(content: str) -> bool:
        patterns = [r"1[3-9]\d{9}", r"\d{15,18}[0-9Xx]", r"(?:\u5730\u5740|\u8eab\u4efd\u8bc1|\u94f6\u884c\u5361|\u5bc6\u7801|\u9a8c\u8bc1\u7801)"]
        return any(re.search(pattern, content) for pattern in patterns)


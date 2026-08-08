import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Protocol

from app.memory.schemas import MemoryRecord


def _as_json_dict(value: object) -> dict[str, object]:
    """Normalize JSON values returned by memory stores."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class MemoryRepository(Protocol):
    async def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None: ...
    async def recent_messages(self, user_id: str, session_id: str, limit: int) -> list[dict[str, str]]: ...
    async def load_summary(self, user_id: str, session_id: str) -> str: ...
    async def save_summary(self, user_id: str, session_id: str, summary: str, summarized_count: int) -> None: ...
    async def load_checkpoint(self, user_id: str, session_id: str) -> dict[str, str]: ...
    async def save_checkpoint(self, user_id: str, session_id: str, state: dict[str, str]) -> None: ...
    async def clear_checkpoint(self, user_id: str, session_id: str) -> None: ...
    async def has_consent(self, user_id: str) -> bool: ...
    async def set_consent(self, user_id: str, enabled: bool) -> None: ...
    async def add_memory(self, record: MemoryRecord) -> None: ...
    async def search_memories(self, user_id: str, shop_id: str, kind: str, query: str, limit: int) -> list[MemoryRecord]: ...
    async def list_memories(self, user_id: str) -> list[MemoryRecord]: ...
    async def retract_memory(self, user_id: str, memory_id: str) -> bool: ...
    async def retract_all_memories(self, user_id: str) -> None: ...


class InMemoryMemoryRepository:
    """In-memory fallback repository."""
    """In-memory fallback repository."""

    def __init__(self) -> None:
        self.messages: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.summaries: dict[str, tuple[str, int]] = {}
        self.checkpoints: dict[str, dict[str, str]] = defaultdict(dict)
        self.consents: dict[str, bool] = defaultdict(bool)
        self.memories: dict[str, list[MemoryRecord]] = defaultdict(list)

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        return f"{user_id}:{session_id}"

    async def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        key = self._key(user_id, session_id)
        self.messages[key].append({"role": role, "content": content})
        self.messages[key] = self.messages[key][-40:]

    async def recent_messages(self, user_id: str, session_id: str, limit: int) -> list[dict[str, str]]:
        return self.messages[self._key(user_id, session_id)][-limit:]

    async def load_summary(self, user_id: str, session_id: str) -> str:
        return self.summaries.get(self._key(user_id, session_id), ("", 0))[0]

    async def save_summary(self, user_id: str, session_id: str, summary: str, summarized_count: int) -> None:
        self.summaries[self._key(user_id, session_id)] = (summary, summarized_count)


    async def load_checkpoint(self, user_id: str, session_id: str) -> dict[str, str]:
        return dict(self.checkpoints[self._key(user_id, session_id)])

    async def save_checkpoint(self, user_id: str, session_id: str, state: dict[str, str]) -> None:
        self.checkpoints[self._key(user_id, session_id)] = dict(state)

    async def clear_checkpoint(self, user_id: str, session_id: str) -> None:
        self.checkpoints.pop(self._key(user_id, session_id), None)

    async def has_consent(self, user_id: str) -> bool:
        return self.consents[user_id]

    async def set_consent(self, user_id: str, enabled: bool) -> None:
        self.consents[user_id] = enabled

    async def add_memory(self, record: MemoryRecord) -> None:
        self.memories[record.user_id].append(record)

    async def search_memories(self, user_id: str, shop_id: str, kind: str, query: str, limit: int) -> list[MemoryRecord]:
        query_tokens = set(query.lower().split())
        records = [item for item in self.memories[user_id] if item.status == "active" and item.shop_id == shop_id and item.kind == kind]
        ranked = sorted(records, key=lambda item: sum(token in item.content.lower() for token in query_tokens) + item.importance, reverse=True)
        return ranked[:limit]

    async def list_memories(self, user_id: str) -> list[MemoryRecord]:
        return [item for item in self.memories[user_id] if item.status == "active"]

    async def retract_memory(self, user_id: str, memory_id: str) -> bool:
        for item in self.memories[user_id]:
            if item.memory_id == memory_id and item.status == "active":
                item.status = "retracted"
                return True
        return False

    async def retract_all_memories(self, user_id: str) -> None:
        for item in self.memories[user_id]:
            item.status = "retracted"


class PostgresMemoryRepository:
    """PostgreSQL repository."""
    """PostgreSQL repository."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool = None

    async def connect(self) -> None:
        import asyncpg
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        await self.pool.execute("INSERT INTO messages (user_id, session_id, role, content) VALUES ($1, $2, $3, $4)", user_id, session_id, role, content)

    async def recent_messages(self, user_id: str, session_id: str, limit: int) -> list[dict[str, str]]:
        rows = await self.pool.fetch("SELECT role, content FROM messages WHERE user_id=$1 AND session_id=$2 ORDER BY created_at DESC LIMIT $3", user_id, session_id, limit)
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def load_summary(self, user_id: str, session_id: str) -> str:
        row = await self.pool.fetchrow("SELECT summary FROM conversation_summaries WHERE user_id=$1 AND session_id=$2", user_id, session_id)
        return str(row["summary"]) if row else ""

    async def save_summary(self, user_id: str, session_id: str, summary: str, summarized_count: int) -> None:
        await self.pool.execute("""INSERT INTO conversation_summaries (user_id, session_id, summary, summarized_count, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id, session_id) DO UPDATE SET summary=EXCLUDED.summary, summarized_count=EXCLUDED.summarized_count, updated_at=NOW()""", user_id, session_id, summary, summarized_count)

    async def load_checkpoint(self, user_id: str, session_id: str) -> dict[str, str]:
        row = await self.pool.fetchrow("SELECT state FROM workflow_checkpoints WHERE user_id=$1 AND session_id=$2 AND expires_at > NOW()", user_id, session_id)
        if not row:
            return {}
        state = row["state"]
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except json.JSONDecodeError:
                return {}
        return {str(key): str(item) for key, item in _as_json_dict(state).items()}

    async def save_checkpoint(self, user_id: str, session_id: str, state: dict[str, str]) -> None:
        await self.pool.execute("""INSERT INTO workflow_checkpoints (user_id, session_id, state, expires_at)
            VALUES ($1, $2, $3::jsonb, NOW() + INTERVAL '7 days')
            ON CONFLICT (user_id, session_id) DO UPDATE SET state=EXCLUDED.state, version=workflow_checkpoints.version + 1, expires_at=EXCLUDED.expires_at, updated_at=NOW()""", user_id, session_id, json.dumps(state, ensure_ascii=False))

    async def clear_checkpoint(self, user_id: str, session_id: str) -> None:
        await self.pool.execute("DELETE FROM workflow_checkpoints WHERE user_id=$1 AND session_id=$2", user_id, session_id)

    async def has_consent(self, user_id: str) -> bool:
        value = await self.pool.fetchval("SELECT enabled FROM memory_consents WHERE user_id=$1", user_id)
        return bool(value)

    async def set_consent(self, user_id: str, enabled: bool) -> None:
        await self.pool.execute("""INSERT INTO memory_consents (user_id, enabled, updated_at) VALUES ($1, $2, NOW())
            ON CONFLICT (user_id) DO UPDATE SET enabled=EXCLUDED.enabled, updated_at=NOW()""", user_id, enabled)

    async def add_memory(self, record: MemoryRecord) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO memory_events (event_id, memory_id, user_id, shop_id, event_type, content, source, created_at)
                    VALUES (gen_random_uuid(), $1, $2, $3, 'add', $4, $5, NOW())""",
                    record.memory_id, record.user_id, record.shop_id, record.content, record.source,
                )
                await connection.execute(
                    """INSERT INTO memory_items (memory_id, user_id, shop_id, kind, content, source, importance, confidence, attributes, status, valid_from)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'active', NOW())""",
                    record.memory_id, record.user_id, record.shop_id, record.kind, record.content,
                    record.source, record.importance, record.confidence,
                    json.dumps(record.attributes, ensure_ascii=False),
                )

    async def search_memories(self, user_id: str, shop_id: str, kind: str, query: str, limit: int) -> list[MemoryRecord]:
        rows = await self.pool.fetch("""SELECT memory_id, user_id, shop_id, kind, content, source, importance, confidence, attributes, created_at, valid_from, valid_to, status
            FROM memory_items WHERE user_id=$1 AND shop_id=$2 AND kind=$3 AND status='active'
            ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('simple', $4)) DESC, importance DESC, valid_from DESC LIMIT $5""", user_id, shop_id, kind, query, limit)
        return [MemoryRecord(memory_id=str(row["memory_id"]), user_id=row["user_id"], shop_id=row["shop_id"], kind=row["kind"], content=row["content"], source=row["source"], importance=row["importance"], confidence=row["confidence"], attributes=_as_json_dict(row["attributes"]), created_at=row["created_at"], valid_from=row["valid_from"], valid_to=row["valid_to"], status=row["status"]) for row in rows]

    async def list_memories(self, user_id: str) -> list[MemoryRecord]:
        return await self.search_memories(user_id, "default", "semantic", "", 100)

    async def retract_memory(self, user_id: str, memory_id: str) -> bool:
        result = await self.pool.execute("UPDATE memory_items SET status='retracted', valid_to=NOW() WHERE memory_id=$1 AND user_id=$2 AND status='active'", memory_id, user_id)
        return result.endswith("1")

    async def retract_all_memories(self, user_id: str) -> None:
        await self.pool.execute("UPDATE memory_items SET status='retracted', valid_to=NOW() WHERE user_id=$1 AND status='active'", user_id)



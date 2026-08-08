from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


MemoryKind = Literal["semantic", "episodic"]


@dataclass
class MemoryRecord:
    memory_id: str
    user_id: str
    shop_id: str
    kind: MemoryKind
    content: str
    source: str
    importance: float
    confidence: float
    created_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None
    status: Literal["active", "retracted", "expired"] = "active"
    embedding: list[float] | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class RouterMemoryContext:
    workflow_state: dict[str, str]
    recent_messages: list[dict[str, str]]
    conversation_summary: str = ""


@dataclass
class AgentMemoryContext:
    workflow_state: dict[str, str]
    recent_messages: list[dict[str, str]]
    conversation_summary: str
    semantic_memories: list[MemoryRecord]
    episodic_memories: list[MemoryRecord]


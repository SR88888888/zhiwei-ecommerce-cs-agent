from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.memory import memory


router = APIRouter(prefix="/api/v1/me", tags=["memory"])


class MemoryConsentRequest(BaseModel):
    user_id: str
    enabled: bool


@router.put("/memory-consent")
async def update_memory_consent(request: MemoryConsentRequest) -> dict:
    await memory.set_consent(request.user_id, request.enabled)
    return {"enabled": request.enabled}


@router.get("/memories")
async def list_memories(user_id: str) -> list[dict]:
    records = await memory.list_memories(user_id)
    return [
        {
            "memory_id": item.memory_id,
            "kind": item.kind,
            "content": item.content,
            "source": item.source,
            "created_at": item.created_at.isoformat(),
        }
        for item in records
    ]


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, user_id: str) -> dict:
    if not await memory.forget_memory(user_id, memory_id):
        raise HTTPException(404, "记忆不存在")
    return {"deleted": True}


@router.delete("/memories")
async def delete_all_memories(user_id: str) -> dict:
    await memory.set_consent(user_id, False)
    return {"deleted": True, "enabled": False}

import io
import asyncio
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.multimodal import attachment_service
from app.rag import chunk_text, rag_service
from app.knowledge_ingestion import ingestion_service
from app.pgvector_store import pgvector_store

router = APIRouter(prefix="/api/v1", tags=["knowledge"])
async def process_ingestion_job(job_id: str) -> None:
    await asyncio.to_thread(ingestion_service.process, job_id)
    job = ingestion_service.state["jobs"].get(job_id)
    if job:
        await rag_service.index_ingestion_document(job["document_id"])


@router.post("/attachments/ocr")
async def upload_attachment(background_tasks: BackgroundTasks, user_id: str = Form(...), session_id: str = Form(...), file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        attachment_id = attachment_service.create(user_id, session_id, file.content_type or "", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    background_tasks.add_task(attachment_service.process, attachment_id, user_id, session_id, file.content_type or "", data)
    return {"attachment_id": attachment_id, "status": "processing"}


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: str, user_id: str, session_id: str) -> dict:
    item = attachment_service.get(attachment_id, user_id, session_id)
    if not item:
        raise HTTPException(404, "attachment not found")
    return {key: value for key, value in item.items() if key not in {"sha256", "user_id", "session_id"}}


def extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[\u7b2c{page_number}\u9875]\n{text}")
        content = "\n\n".join(pages)
        if not content:
            raise ValueError("pdf_has_no_extractable_text")
        return content
    except ImportError as exc:
        raise ValueError("pypdf_not_installed") from exc


@router.post("/knowledge/documents")
async def upload_knowledge(file: UploadFile = File(...), title: str = Form("")) -> dict:
    filename = file.filename or "untitled"
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "Knowledge file must be smaller than 50MB")
    try:
        if suffix in {"md", "txt"}:
            content = data.decode("utf-8")
            source_type = "text"
        elif suffix == "pdf" or file.content_type == "application/pdf":
            content = extract_pdf(data)
            source_type = "pdf"
        else:
            raise HTTPException(400, "Only markdown, text, and PDF files are supported")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Knowledge file must be UTF-8") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    document_id = rag_service.add_document(title or filename, content, source_type)
    document = rag_service.documents[document_id]
    document["page_count"] = content.count("[\u7b2c") if source_type == "pdf" else None
    return {"document_id": document_id, "title": title or filename, "source_type": source_type, "status": "ready", "chunk_count": len([item for item in rag_service.chunks.values() if item.document_id == document_id])}


@router.get("/knowledge/documents")
async def list_knowledge() -> list[dict]:
    return ingestion_service.documents() + list(rag_service.documents.values())


@router.delete("/knowledge/documents/{document_id}")
async def delete_knowledge(document_id: str) -> dict:
    if ingestion_service.delete(document_id):
        await pgvector_store.delete_document(document_id)
        return {"deleted": True, "source": "ingestion"}
    if rag_service.delete_document(document_id):
        return {"deleted": True, "source": "rag"}
    raise HTTPException(404, "document not found")


@router.post("/knowledge/documents/{document_id}/reindex")
async def reindex_knowledge(document_id: str, background_tasks: BackgroundTasks) -> dict:
    document = ingestion_service.state["documents"].get(document_id)
    if document:
        storage_path = Path(document.get("storage_path", ""))
        if not storage_path.exists():
            raise HTTPException(409, "原始 PDF 不存在，请重新上传后再建立父子块索引")
        job = ingestion_service.retry(document_id)
        if not job:
            raise HTTPException(404, "document not found")
        background_tasks.add_task(process_ingestion_job, job["job_id"])
        return {"document_id": document_id, "job_id": job["job_id"], "status": "pending"}
    if document_id in rag_service.documents:
        return {"document_id": document_id, "status": "ready"}
    raise HTTPException(404, "document not found")
@router.post("/knowledge/ingestion-jobs")
async def create_ingestion_job(background_tasks: BackgroundTasks, file: UploadFile = File(...), title: str = Form("")) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF manuals are supported by asynchronous ingestion")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "Knowledge file must be smaller than 50MB")
    result = ingestion_service.create(file.filename or "untitled.pdf", data, title)
    if result["job_id"]:
        background_tasks.add_task(process_ingestion_job, result["job_id"])
    return result

@router.get("/knowledge/ingestion-jobs/{job_id}")
async def get_ingestion_job(job_id: str) -> dict:
    job = ingestion_service.state["jobs"].get(job_id)
    if not job:
        raise HTTPException(404, "ingestion job not found")
    return {**job, "document": ingestion_service.state["documents"].get(job["document_id"])}

@router.post("/knowledge/documents/{document_id}/approve")
async def approve_knowledge(document_id: str) -> dict:
    document = ingestion_service.approve(document_id)
    if not document:
        raise HTTPException(404, "document not found")
    return document

@router.post("/knowledge/documents/{document_id}/retry")
async def retry_knowledge(background_tasks: BackgroundTasks, document_id: str) -> dict:
    job = ingestion_service.retry(document_id)
    if not job:
        raise HTTPException(404, "document not found")
    background_tasks.add_task(process_ingestion_job, job["job_id"])
    return job
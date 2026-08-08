import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KEYWORDS = {
    "basic": ("\u4ea7\u54c1\u7b80\u4ecb", "\u53c2\u6570", "\u89c4\u683c"),
    "hardware": ("\u6309\u952e", "\u63a5\u53e3", "\u5c4f\u5e55"),
    "shooting": ("\u62cd\u6444", "\u5f55\u50cf", "\u7167\u7247", "\u6a21\u5f0f"),
    "power_storage": ("\u5145\u7535", "\u7535\u6c60", "\u5b58\u50a8", "microSD"),
    "accessories": ("\u914d\u4ef6", "\u517c\u5bb9"),
    "connection": ("\u8fde\u63a5", "DJI Mimo", "Wi-Fi"),
    "firmware": ("\u56fa\u4ef6", "\u5347\u7ea7"),
    "troubleshooting": ("\u6545\u969c", "\u65e0\u6cd5", "\u5f02\u5e38"),
    "safety": ("\u5b89\u5168", "\u7ef4\u62a4", "\u4fdd\u517b"),
}

class IngestionService:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[2] / "data" / "knowledge"
        self.uploads = self.root / "uploads"; self.uploads.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"; self.state: dict[str, Any] = {"documents": {}, "jobs": {}, "chunks": {}}
        if self.path.exists():
            try: self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): pass
    @staticmethod
    def now() -> str: return datetime.now(timezone.utc).isoformat()
    def save(self) -> None:
        temp = self.path.with_suffix(".tmp"); temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"); temp.replace(self.path)
    @staticmethod
    def classify(filename: str) -> tuple[str, str, str]:
        name = filename.lower().replace("_", " ").replace("-", " ")
        model = "Pocket 4 Pro" if "4p" in name or "4 pro" in name else "Pocket 4" if "pocket 4" in name else "Pocket 3" if "pocket 3" in name else "Pocket 2" if "pocket 2" in name else "Pocket"
        version = re.search(r"v([\d.]+)", name)
        return model, version.group(1) if version else "unknown", "zh-CN" if any(x in name for x in ("chs", "zh", "cn", "??")) else "unknown"
    def create(self, filename: str, data: bytes, title: str = "") -> dict:
        digest = hashlib.sha256(data).hexdigest(); found = next((d for d in self.state["documents"].values() if d["content_hash"] == digest and d["status"] not in {"deleted", "failed"}), None)
        if found: return {"job_id": None, "status": "ready", "duplicate": True, "document": found}
        document_id, job_id, now = str(uuid.uuid4()), str(uuid.uuid4()), self.now(); storage = self.uploads / f"{document_id}.pdf"; storage.write_bytes(data); model, version, language = self.classify(filename)
        document = {"document_id": document_id, "filename": filename, "title": title or filename, "product_family": "Osmo Pocket", "model": model, "language": language, "manual_version": version, "page_count": 0, "chapter_count": 0, "chunk_count": 0, "content_hash": digest, "parser_version": "pending", "status": "pending", "created_at": now, "updated_at": now, "storage_path": str(storage), "markdown_preview": "", "structured_data": {}, "error": None}
        self.state["documents"][document_id] = document; self.state["jobs"][job_id] = {"job_id": job_id, "document_id": document_id, "status": "pending", "progress": 0, "error": None, "created_at": now, "updated_at": now}; self.save()
        return {"job_id": job_id, "status": "pending", "duplicate": False, "document": document}
    def process(self, job_id: str) -> None:
        job = self.state["jobs"].get(job_id)
        if not job: return
        doc = self.state["documents"][job["document_id"]]; job.update(status="parsing", progress=15, updated_at=self.now()); doc["status"] = "parsing"
        try:
            text, pages, parser = self.parse(Path(doc["storage_path"])); job.update(status="indexing", progress=60, updated_at=self.now()); clean = self.clean(text); chunks, chapters = self.chunk(clean, doc["document_id"], doc["model"])
            doc.update(page_count=pages, chapter_count=chapters, parent_chunk_count=sum(1 for chunk in chunks if chunk.get("chunk_role") == "parent"), child_chunk_count=sum(1 for chunk in chunks if chunk.get("chunk_role") == "child"), chunk_count=sum(1 for chunk in chunks if chunk.get("chunk_role") == "child"), parser_version=parser, markdown_preview=clean[:12000], structured_data=self.structured(chunks), status="ready", updated_at=self.now()); self.state["chunks"][doc["document_id"]] = chunks; job.update(status="ready", progress=100, updated_at=self.now()); self.save()
        except Exception as exc:
            doc.update(status="failed", error=str(exc), updated_at=self.now()); job.update(status="failed", progress=100, error=str(exc), updated_at=self.now()); self.save()
    def parse(self, pdf: Path) -> tuple[str, int, str]:
        candidate = Path(sys.executable).parent / "mineru.exe"
        mineru = shutil.which("mineru") or (str(candidate) if candidate.exists() else None)
        if not mineru:
            raise RuntimeError("MinerU is not available in the backend Python environment")
        output = self.root / "mineru" / pdf.stem / uuid.uuid4().hex
        output.mkdir(parents=True, exist_ok=True)
        command = [mineru, "-p", str(pdf), "-o", str(output), "-b", "pipeline", "-m", "auto", "-l", "ch"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("MinerU timed out after 600 seconds") from exc
        files = list(output.rglob("*.md"))
        if result.returncode != 0 or not files:
            detail = (result.stderr or result.stdout or "no Markdown output").strip().replace("\x00", " ")
            raise RuntimeError(f"MinerU failed (exit={result.returncode}): {detail[-1200:]}")
        text = max(files, key=lambda item: item.stat().st_size).read_text(encoding="utf-8", errors="ignore")
        content_lists = list(output.rglob("*_content_list.json"))
        page_count = 0
        for content_list in content_lists:
            try:
                entries = json.loads(content_list.read_text(encoding="utf-8"))
                page_count = max(page_count, max((int(item.get("page_idx", -1)) + 1 for item in entries if isinstance(item, dict)), default=0))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        shutil.rmtree(output, ignore_errors=True)
        return text, page_count, "mineru-cli"
    @staticmethod
    def clean(text: str) -> str:
        """保留 MinerU 的表格内容和图片说明，移除无意义 HTML 标签。"""
        text = re.sub(r"!\[([^]]*)\]\([^)]*\)", lambda match: f"\n[image] {match.group(1)}\n", text)
        text = re.sub(r"</?(?:table|thead|tbody)[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?tr[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?(?:td|th)[^>]*>", " | ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    @staticmethod
    def _split_children(content: str, size: int = 500, overlap: int = 80) -> list[str]:
        content = content.strip()
        if not content:
            return []
        parts: list[str] = []
        start = 0
        minimum_boundary = max(0, size - 160)
        while start < len(content):
            end = min(start + size, len(content))
            if end < len(content):
                candidates = [content.rfind(marker, start + minimum_boundary, end) for marker in ("\n", "。", "！", "？")]
                boundary = max(candidates)
                if boundary >= start + minimum_boundary:
                    end = boundary + 1
            part = content[start:end].strip()
            if len(part) >= 20:
                parts.append(part)
            if end >= len(content):
                break
            start = max(end - overlap, start + 1)
        return parts

    @staticmethod
    def chunk(text: str, document_id: str, model: str) -> tuple[list[dict], int]:
        page = 1
        heading_stack: list[str] = []
        current_lines: list[str] = []
        current_start = page
        sections: list[dict[str, Any]] = []

        def flush_section() -> None:
            content = "\n".join(current_lines).strip()
            if not content:
                return
            section_path = heading_stack[:] or ["Manual"]
            sections.append({"title": section_path[-1], "path": section_path, "page_start": current_start, "page_end": page, "content": content})

        for line in text.splitlines():
            marker = re.search(r"\[第(\d+)页\]", line)
            if marker:
                flush_section()
                current_lines = []
                page = int(marker.group(1))
                current_start = page
                continue
            markdown_heading = re.match(r"^(#{1,4})\s+(.+)$", line)
            numeric_heading = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", line) if len(line) < 80 else None
            if markdown_heading or numeric_heading:
                flush_section()
                current_lines = []
                if markdown_heading:
                    level = len(markdown_heading.group(1))
                    title = markdown_heading.group(2).strip()
                else:
                    level = len(numeric_heading.group(1).split("."))
                    title = f"{numeric_heading.group(1)} {numeric_heading.group(2).strip()}"
                heading_stack = heading_stack[: level - 1]
                heading_stack.append(title)
                current_start = page
                current_lines = [line]
                continue
            current_lines.append(line)
        flush_section()

        chunks: list[dict[str, Any]] = []
        for section_index, section in enumerate(sections):
            parent_id = f"{document_id}:parent:{section_index}"
            common = {
                "document_id": document_id,
                "model": model,
                "chapter": section["title"],
                "section_path": " > ".join([model, *section["path"]]),
                "page_start": section["page_start"],
                "page_end": section["page_end"],
            }
            chunks.append({
                "chunk_id": parent_id,
                "parent_id": None,
                "chunk_role": "parent",
                "content_type": "parent",
                "content": section["content"],
                **common,
            })
            for child_index, child_content in enumerate(IngestionService._split_children(section["content"])):
                child_id = f"{parent_id}:child:{child_index}"
                child_common = {
                    "chunk_id": child_id,
                    "parent_id": parent_id,
                    "chunk_role": "child",
                    "content_type": "text",
                    "content": child_content,
                    **common,
                }
                chunks.append(child_common)
                table_lines = [line for line in child_content.splitlines() if line.count("|") >= 2]
                if table_lines:
                    chunks.append({**child_common, "chunk_id": f"{child_id}:table", "content_type": "table", "content": "\n".join(table_lines)})
                image_lines = [line for line in child_content.splitlines() if "[image]" in line.lower()]
                if image_lines:
                    chunks.append({**child_common, "chunk_id": f"{child_id}:image", "content_type": "image", "content": "\n".join(image_lines)})
        return chunks, len(sections)

    @staticmethod
    def structured(chunks: list[dict]) -> dict:
        result = {key: [] for key in KEYWORDS}
        for chunk in chunks:
            text = f"{chunk['chapter']}\n{chunk['content']}".lower()
            for group, words in KEYWORDS.items():
                if any(word.lower() in text for word in words): result[group].append({"section": chunk["chapter"], "page_start": chunk["page_start"], "content": chunk["content"][:600]})
        return {key: value[:8] for key, value in result.items() if value}
    def approve(self, document_id: str) -> dict | None:
        doc = self.state["documents"].get(document_id)
        if not doc or doc["status"] == "deleted": return None
        doc.update(status="ready", error=None, updated_at=self.now())
        for job in self.state["jobs"].values():
            if job["document_id"] == document_id: job.update(status="ready", progress=100, updated_at=self.now())
        self.save(); return doc
    def retry(self, document_id: str) -> dict | None:
        doc = self.state["documents"].get(document_id)
        if not doc: return None
        job_id, now = str(uuid.uuid4()), self.now(); job = {"job_id": job_id, "document_id": document_id, "status": "pending", "progress": 0, "error": None, "created_at": now, "updated_at": now}; self.state["jobs"][job_id] = job; doc.update(status="pending", error=None, updated_at=now); self.save(); return job
    def delete(self, document_id: str) -> bool:
        doc = self.state["documents"].get(document_id)
        if not doc: return False
        doc["status"] = "deleted"; self.state["chunks"].pop(document_id, None); self.save(); return True
    def documents(self) -> list[dict]:
        documents = [doc for doc in self.state["documents"].values() if doc["status"] != "deleted"]
        successful_hashes = {doc["content_hash"] for doc in documents if doc["status"] == "ready"}
        visible = [doc for doc in documents if not (doc["status"] == "failed" and doc["content_hash"] in successful_hashes)]
        return sorted(visible, key=lambda doc: doc["updated_at"], reverse=True)
    def release_parsed_content(self, document_id: str) -> None:
        document = self.state["documents"].get(document_id)
        if not document:
            return
        self.state["chunks"].pop(document_id, None)
        document["markdown_preview"] = ""
        document["structured_data"] = {}
        self.save()

    def ready_chunks(self) -> list[dict]: return [chunk for doc_id, chunks in self.state["chunks"].items() if self.state["documents"].get(doc_id, {}).get("status") == "ready" for chunk in chunks]

ingestion_service = IngestionService()


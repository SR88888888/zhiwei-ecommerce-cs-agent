import hashlib
import math
import os
import re
import uuid
from dataclasses import dataclass, field

from app.schemas import Citation
from app.usage import record_usage
from app.safety import plain_customer_text
from app.pgvector_store import pgvector_store


@dataclass
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    source_id: str
    title: str
    content: str
    source_type: str
    embedding: list[float] = field(default_factory=list)


class LocalEmbedder:
    def __init__(self) -> None:
        self._model = None
        self._failed = False
        self.model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if os.getenv("LOCAL_EMBEDDINGS_ENABLED", "true").lower() == "true" and not self._failed and self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, local_files_only=True)
            except Exception:
                self._failed = True
        if self._model:
            return self._model.encode(texts, normalize_embeddings=True).tolist()
        return [self._fallback(text) for text in texts]

    @staticmethod
    def _fallback(text: str) -> list[float]:
        vector = [0.0] * 512
        for char in text.lower():
            vector[ord(char) % 512] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class CrossEncoderReranker:
    """Optional cross-encoder reranker with a safe fallback."""

    def __init__(self) -> None:
        self._model = None
        self._failed = False
        self.model_path = os.getenv("RERANKER_MODEL_PATH", os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub", "bge-reranker-base"))

    def rerank(self, query: str, citations: list[Citation]) -> list[Citation] | None:
        if not citations or self._failed or os.getenv("RERANKER_ENABLED", "false").lower() != "true":
            return None
        try:
            if self._model is None:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_path, local_files_only=True, max_length=512)
            scores = self._model.predict([(query, item.content) for item in citations], show_progress_bar=False)
            ranked = sorted(zip(scores, citations), key=lambda item: float(item[0]), reverse=True)
            for score, citation in ranked:
                citation.score = round(float(score), 6)
            return [citation for _, citation in ranked]
        except Exception:
            self._failed = True
            return None

def chunk_text(text: str, size: int = 300, overlap: int = 50) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [text[start:start + size] for start in range(0, len(text), max(1, size - overlap))]


class RagService:
    def __init__(self) -> None:
        self.embedder = LocalEmbedder()
        self.reranker = CrossEncoderReranker()
        self.chunks: dict[str, KnowledgeChunk] = {}
        self.documents: dict[str, dict] = {}
        self._seed()

    async def startup(self) -> None:
        await pgvector_store.startup()
        if not pgvector_store.available:
            return
        from app.knowledge_ingestion import ingestion_service
        for document in ingestion_service.documents():
            if document.get("status") == "ready" and document.get("parser_version") == "mineru-cli":
                await self.index_ingestion_document(document["document_id"])

    async def shutdown(self) -> None:
        await pgvector_store.shutdown()

    async def index_ingestion_document(self, document_id: str) -> None:
        from app.knowledge_ingestion import ingestion_service

        document = ingestion_service.state["documents"].get(document_id)
        chunks = ingestion_service.state["chunks"].get(document_id, [])
        child_chunks = [chunk for chunk in chunks if chunk.get("chunk_role") == "child"]
        if not document or document.get("status") != "ready" or document.get("parser_version") != "mineru-cli" or not child_chunks:
            return
        embeddings = self.embedder.embed([chunk["content"] for chunk in child_chunks])
        index_document = {**document, "embedding_model": self.embedder.model_name}
        await pgvector_store.sync_document(index_document, chunks, embeddings)
        ingestion_service.release_parsed_content(document_id)

    @staticmethod
    def _expand_query(query: str) -> str:
        normalized = query.lower()
        expansions: list[str] = []
        rules = [
            (("\u5b58\u50a8\u5361", "microsd", "sd\u5361", "\u683c\u5f0f\u5316"), ["microSD", "SDXC", "UHS-I", "\u5b58\u50a8", "\u683c\u5f0f\u5316"]),
            (("dji mimo", "\u8fde\u63a5", "\u6fc0\u6d3b"), ["DJI Mimo", "\u624b\u673a\u8fde\u63a5", "Wi-Fi", "\u84dd\u7259", "\u6fc0\u6d3b"]),
            (("\u5f55\u50cf", "\u5f00\u59cb\u5f55\u5236", "\u5f00\u59cb\u62cd\u6444"), ["\u5f55\u50cf", "\u5f55\u5236", "\u62cd\u6444\u754c\u9762", "\u5feb\u901f\u83dc\u5355"]),
            (("\u62cd\u6444\u6a21\u5f0f", "\u6a21\u5f0f\u5207\u6362"), ["\u62cd\u6444\u6a21\u5f0f", "\u76f8\u673a\u754c\u9762", "\u5411\u5de6\u6ed1\u52a8", "\u5411\u4e0b\u6ed1\u52a8"]),
            (("\u5bfc\u51fa", "\u4f20\u8f93", "\u7d20\u6750"), ["DJI Mimo", "\u56de\u653e", "\u5bfc\u51fa", "\u624b\u673a"]),
            (("\u56fa\u4ef6", "\u5347\u7ea7"), ["\u56fa\u4ef6\u5347\u7ea7", "DJI Mimo", "microSD", "\u7535\u91cf"]),
        ]
        for triggers, terms in rules:
            if any(trigger in normalized for trigger in triggers):
                expansions.extend(terms)
        return query if not expansions else f"{query} {' '.join(dict.fromkeys(expansions))}"
    async def retrieve_for_answer(self, query: str, limit: int = 3) -> list[Citation]:
        requested = next((model for model in ("Pocket 4 Pro", "Pocket 4", "Pocket 3", "Pocket 2", "Pocket") if model.lower() in query.lower()), None)
        if not pgvector_store.available:
            return []
        expanded_query = self._expand_query(query)
        try:
            database_results = await pgvector_store.search(
                expanded_query,
                self.embedder.embed([expanded_query])[0],
                requested,
                30,
                self.embedder.model_name,
                score_query=query,
            )
        except Exception:
            return []
        rerank_candidates = database_results[:15]
        reranked = self.reranker.rerank(query, rerank_candidates)
        return (reranked or rerank_candidates)[:limit]
    def _seed(self) -> None:
        items = [
            ("group_coupon_rules", "\u62fc\u5355\u4e0e\u4f18\u60e0\u5238\u89c4\u5219", "\u62fc\u5355\u662f\u5426\u6210\u529f\u3001\u4f18\u60e0\u5238\u4f7f\u7528\u8303\u56f4\u548c\u6d3b\u52a8\u65f6\u95f4\u4ee5\u8ba2\u5355\u53ca\u6d3b\u52a8\u9875\u9762\u5c55\u793a\u4e3a\u51c6\u3002", "seed"),
            ("after_sales_rules", "\u552e\u540e\u89c4\u5219", "\u552e\u540e\u7533\u8bf7\u9700\u7ed3\u5408\u8ba2\u5355\u72b6\u6001\u3001\u5546\u54c1\u7c7b\u578b\u548c\u5546\u5bb6\u89c4\u5219\u5224\u65ad\u3002", "seed"),
            ("logistics_help", "\u7269\u6d41\u8bf4\u660e", "\u8ba2\u5355\u53d1\u8d27\u540e\u53ef\u901a\u8fc7\u8ba2\u5355\u8be6\u60c5\u67e5\u770b\u7269\u6d41\u516c\u53f8\u3001\u8fd0\u5355\u53f7\u548c\u6700\u65b0\u8f68\u8ff9\u3002", "seed"),
            ("product_help", "\u5546\u54c1\u54a8\u8be2\u8bf4\u660e", "\u5546\u54c1\u89c4\u683c\u3001\u53c2\u6570\u548c\u5e93\u5b58\u4ee5\u5546\u54c1\u8be6\u60c5\u9875\u53ca\u5b9e\u65f6\u5e93\u5b58\u63a5\u53e3\u8fd4\u56de\u4e3a\u51c6\u3002", "seed"),
        ]
        for source_id, title, content, source_type in items:
            self.add_document(title, content, source_type, source_id=source_id)

    def add_document(self, title: str, content: str, source_type: str, source_id: str | None = None) -> str:
        document_id = source_id or str(uuid.uuid4())
        digest = hashlib.sha256(content.encode()).hexdigest()
        self.documents[document_id] = {"document_id": document_id, "title": title, "status": "ready", "source_type": source_type, "content_hash": digest}
        parts = chunk_text(content)
        vectors = self.embedder.embed(parts)
        for index, part in enumerate(parts):
            chunk_id = f"{document_id}:{index}"
            self.chunks[chunk_id] = KnowledgeChunk(chunk_id, document_id, document_id, title, part, source_type, vectors[index])
        return document_id

    def delete_document(self, document_id: str) -> bool:
        document = self.documents.get(document_id)
        if not document:
            return False
        document["status"] = "deleted"
        return True

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        words = set(re.findall(r"[A-Za-z0-9]+", query.lower()))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", query))
        words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        words.update(char for char in chinese if len(chinese) <= 2)
        return {word for word in words if word}

    def retrieve(self, query: str, limit: int = 3) -> list[Citation]:
        query_embedding = self.embedder.embed([query])[0]
        query_terms = self._query_terms(query)
        active_chunks = self._active_chunks()
        vector_ranked = sorted(active_chunks, key=lambda item: self._cosine(query_embedding, item.embedding), reverse=True)[:30]
        text_ranked = sorted(active_chunks, key=lambda item: self._lexical(query_terms, item.content.lower()), reverse=True)[:30]
        scores: dict[str, float] = {}
        for rank, item in enumerate(vector_ranked, 1):
            scores[item.chunk_id] = scores.get(item.chunk_id, 0) + 1 / (60 + rank)
        for rank, item in enumerate(text_ranked, 1):
            scores[item.chunk_id] = scores.get(item.chunk_id, 0) + 1 / (60 + rank)
        results = [
            Citation(source_id=self.chunks[key].source_id, title=self.chunks[key].title, content=self.chunks[key].content, document_id=self.chunks[key].document_id, chunk_id=key, source_type=self.chunks[key].source_type, score=round(scores[key], 6))
            for key in sorted(scores, key=scores.get, reverse=True)[:limit]
            if scores[key] > 0
        ]

        from app.knowledge_ingestion import ingestion_service

        requested = next((model for model in ("Pocket 4 Pro", "Pocket 4", "Pocket 3", "Pocket 2", "Pocket") if model.lower() in query.lower()), None)
        external_scores: list[tuple[float, dict]] = []
        for chunk in ingestion_service.ready_chunks():
            lexical = self._lexical(query_terms, chunk["content"].lower())
            if requested and chunk["model"] == requested:
                lexical += 3
            if lexical:
                external_scores.append((lexical, chunk))
        for score, chunk in sorted(external_scores, key=lambda item: item[0], reverse=True)[:limit]:
            title = f"{chunk['model']} - {chunk['chapter']} - \u7b2c{chunk['page_start']}\u9875"
            results.append(Citation(source_id=chunk["document_id"], title=title, content=chunk["content"], document_id=chunk["document_id"], chunk_id=chunk["chunk_id"], source_type="pdf", score=round(score, 6), model=chunk["model"], section_path=chunk["section_path"], page_start=chunk["page_start"], page_end=chunk["page_end"]))
        return sorted(results, key=lambda item: item.score or 0, reverse=True)[:limit]

    def _active_chunks(self) -> list[KnowledgeChunk]:
        return [chunk for chunk in self.chunks.values() if self.documents[chunk.document_id]["status"] == "ready"]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _lexical(terms: set[str], content: str) -> float:
        return float(sum(term in content for term in terms if term))

    @staticmethod
    def _is_unavailable_answer(answer: str) -> bool:
        normalized = re.sub(r"\s+", "", answer)
        return not normalized or "\u6682\u65f6\u6ca1\u6709\u76f8\u5173\u5185\u5bb9" in normalized or normalized == "?????????"

    def _evidence_answer(self, query: str, citations: list[Citation]) -> str:
        terms = self._query_terms(query)
        candidates: list[tuple[float, str]] = []
        for citation in citations:
            content = re.sub(r"\[\d+\]", "", citation.content).strip()
            for sentence in re.split(r"(?<=[\u3002\uff01\uff1f.!?])\s*|\n+", content):
                sentence = re.sub(r"^#{1,6}\s*", "", sentence).strip(" -")
                if len(sentence) >= 6:
                    candidates.append((self._lexical(terms, sentence.lower()), sentence))
        if not candidates:
            return "\u6682\u65f6\u6ca1\u6709\u76f8\u5173\u5185\u5bb9\u3002"
        selected: list[str] = []
        for _, sentence in sorted(candidates, key=lambda item: item[0], reverse=True):
            if sentence not in selected:
                selected.append(sentence[:240])
            if len(selected) == 3:
                break
        return "\u6839\u636e\u8bf4\u660e\u4e66\uff1a" + " ".join(selected)

    @staticmethod
    def _without_citation_markers(answer: str) -> str:
        return re.sub(r"\s*\[\d+\]", "", answer).strip()
    async def answer(self, query: str) -> tuple[str, list[Citation]]:
        citations = await self.retrieve_for_answer(query)
        if not citations:
            return "\u6682\u65f6\u6ca1\u6709\u76f8\u5173\u5185\u5bb9\u3002", []
        if os.getenv("RAG_LLM_ENABLED", "true").lower() != "true":
            return plain_customer_text(self._evidence_answer(query, citations)), citations
        try:
            from openai import AsyncOpenAI
            from app.core import get_settings

            settings = get_settings()
            if not settings.deepseek_api_key:
                raise RuntimeError("missing_key")
            evidence = "\n".join(f"[{index}] {item.content}" for index, item in enumerate(citations, 1))
            system_prompt = (
                "You are a Chinese customer-service assistant. Answer in simplified Chinese using only the supplied manual evidence. "
                "The evidence below has been retrieved for this question. You must give a direct, concise answer whenever it is non-empty. "
                "Do not say that there is no relevant content when evidence is supplied. Do not invent facts, mix product models, "
                "or mention citations, pages, sources, routing, or tools."
            )
            client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=settings.llm_timeout_seconds)
            for attempt in range(2):
                instruction = f"Question: {query}\nManual evidence:\n{evidence}"
                if attempt:
                    instruction += "\nYour previous response incorrectly refused to answer. Rewrite a direct answer from the evidence."
                response = await client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": instruction}],
                    temperature=0,
                )
                record_usage(settings.deepseek_model, response.usage)
                answer = self._without_citation_markers((response.choices[0].message.content or "").strip())
                if not self._is_unavailable_answer(answer):
                    return plain_customer_text(answer), citations
        except Exception:
            pass
        return plain_customer_text(self._evidence_answer(query, citations)), citations

rag_service = RagService()


def retrieve(query: str, limit: int = 3) -> list[Citation]:
    return rag_service.retrieve(query, limit)

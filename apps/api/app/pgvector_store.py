import json
import logging
import math
import re
import uuid
from typing import Any

from app.core import get_settings
from app.schemas import Citation

logger = logging.getLogger(__name__)


class PgVectorKnowledgeStore:
    def __init__(self) -> None:
        self.pool = None
        self.last_error: str | None = None

    async def startup(self) -> None:
        dsn = get_settings().database_url
        if not dsn:
            self.last_error = "鏈厤缃?DATABASE_URL"
            return
        try:
            import asyncpg

            self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            self.last_error = None
        except Exception as exc:
            self.pool = None
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("pgvector connection unavailable: %s", self.last_error)

    async def shutdown(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    @property
    def available(self) -> bool:
        return self.pool is not None

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in values) + "]"

    @staticmethod
    def _metadata(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        english = set(re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", query.lower()))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", query))
        chinese_terms = {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}
        if len(chinese) == 1:
            chinese_terms.add(chinese)
        ignored = {"pocket", "dji", "mimo", "app", "osmo", "pro"}
        return {item for item in english | chinese_terms if len(item) > 1 and item not in ignored}

    @staticmethod
    def _bm25_tokens(text: str) -> list[str]:
        english = re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", text.lower())
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        chinese_terms = [chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))]
        return [token for token in [*english, *chinese_terms] if len(token) > 1]

    @classmethod
    def _bm25_rank(cls, query: str, rows: list[Any], limit: int) -> list[Any]:
        query_terms = set(cls._bm25_tokens(query))
        if not query_terms or not rows:
            return []
        tokenized = [(row, cls._bm25_tokens(str(row["content"]))) for row in rows]
        document_count = len(tokenized)
        average_length = sum(len(tokens) for _, tokens in tokenized) / max(document_count, 1)
        document_frequency: dict[str, int] = {}
        for _, tokens in tokenized:
            for term in set(tokens):
                document_frequency[term] = document_frequency.get(term, 0) + 1
        k1 = 1.2
        b = 0.75
        ranked: list[tuple[float, Any]] = []
        for row, tokens in tokenized:
            term_frequency: dict[str, int] = {}
            for token in tokens:
                term_frequency[token] = term_frequency.get(token, 0) + 1
            score = 0.0
            for term in query_terms:
                frequency = term_frequency.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency.get(term, 0)
                idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
                normalization = 1 - b + b * len(tokens) / max(average_length, 1)
                score += idf * (frequency * (k1 + 1)) / (frequency + k1 * normalization)
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in ranked[:limit]]

    @classmethod
    def _lexical_score(cls, query: str, content: str) -> float:
        normalized = content.lower()
        return float(sum(1 for term in cls._query_terms(query) if term in normalized))

    @staticmethod
    def _entity_alignment_score(query: str, content: str) -> float:
        """Apply object-consistency checks for commonly confused manual entities."""
        question = query.lower()
        evidence = content.lower()
        score = 0.0
        storage_question = any(term in question for term in ("\u5b58\u50a8\u5361", "microsd", "sd\u5361", "\u683c\u5f0f\u5316"))
        mimo_question = "dji mimo" in question and any(term in question for term in ("\u8fde\u63a5", "\u6fc0\u6d3b", "\u5bfc\u51fa", "\u4f20\u8f93"))
        tutorial_question = any(term in question for term in ("\u6559\u7a0b\u89c6\u9891", "\u6559\u5b66\u89c6\u9891", "\u89c6\u9891\u5728\u54ea\u91cc", "\u89c6\u9891\u5728\u54ea"))
        if storage_question:
            if any(term in evidence for term in ("microsd", "sdxc", "uhs-i", "\u5b58\u50a8\u5361", "\u683c\u5f0f\u5316")):
                score += 3.0
            if any(term in evidence for term in ("dji mic", "\u53d1\u5c04\u5668", "tx1", "tx2")):
                score -= 8.0
        if mimo_question:
            if "dji mimo" in evidence and any(term in evidence for term in ("\u8fde\u63a5", "\u6fc0\u6d3b", "wi-fi", "\u84dd\u7259", "\u624b\u673a")):
                score += 3.0
            if any(term in evidence for term in ("dji mic", "\u53d1\u5c04\u5668", "tx1", "tx2")):
                score -= 8.0
        if tutorial_question and any(term in evidence for term in ("\u6559\u7a0b\u89c6\u9891", "\u6559\u5b66\u89c6\u9891", "\u4e8c\u7ef4\u7801", "dji.com")):
            score += 3.0
        return score
    async def sync_document(self, document: dict[str, Any], chunks: list[dict[str, Any]], embeddings: list[list[float]]) -> None:
        if not self.pool or document.get("status") != "ready":
            return
        document_id = uuid.UUID(document["document_id"])
        child_chunks = [chunk for chunk in chunks if chunk.get("chunk_role", "child") == "child"]
        parent_chunks = [chunk for chunk in chunks if chunk.get("chunk_role") == "parent"]
        if len(child_chunks) != len(embeddings):
            raise ValueError("child chunks and embeddings must have the same length")
        vector_size = len(embeddings[0]) if embeddings else 512
        zero_vector = self._vector_literal([0.0] * vector_size)
        child_embeddings = {chunk["chunk_id"]: embedding for chunk, embedding in zip(child_chunks, embeddings)}
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO knowledge_documents (document_id, title, source_type, status, content_hash, parse_status, error_message)
                    VALUES ($1, $2, 'pdf', 'ready', $3, $4, NULL)
                    ON CONFLICT (document_id) DO UPDATE SET title=EXCLUDED.title, status='ready', content_hash=EXCLUDED.content_hash, parse_status=EXCLUDED.parse_status, error_message=NULL""",
                    document_id,
                    document.get("title") or document.get("filename") or "Manual",
                    document.get("content_hash") or "",
                    document.get("parser_version") or "mineru-cli",
                )
                await connection.execute("DELETE FROM knowledge_chunks WHERE document_id=$1", document_id)
                records = []
                for chunk in [*parent_chunks, *child_chunks]:
                    metadata = {
                        "source_chunk_id": chunk["chunk_id"],
                        "source_parent_id": chunk.get("parent_id"),
                        "chunk_role": chunk.get("chunk_role", "child"),
                        "filename": document.get("filename"),
                        "model": chunk.get("model"),
                        "chapter": chunk.get("chapter"),
                        "section_path": chunk.get("section_path"),
                        "page_start": chunk.get("page_start"),
                        "page_end": chunk.get("page_end"),
                        "content_type": chunk.get("content_type", "text"),
                        "embedding_model": document.get("embedding_model", "legacy-hash-512"),
                    }
                    vector = child_embeddings.get(chunk["chunk_id"])
                    records.append((
                        uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"]),
                        document_id,
                        chunk["content"],
                        json.dumps(metadata, ensure_ascii=False),
                        self._vector_literal(vector) if vector else zero_vector,
                    ))
                if records:
                    await connection.executemany(
                        "INSERT INTO knowledge_chunks (chunk_id, document_id, content, metadata, embedding) VALUES ($1, $2, $3, $4::jsonb, $5::vector)",
                        records,
                    )

    async def delete_document(self, document_id: str) -> None:
        if not self.pool:
            return
        try:
            await self.pool.execute("UPDATE knowledge_documents SET status='deleted' WHERE document_id=$1::uuid", uuid.UUID(document_id))
        except (ValueError, TypeError):
            return

    async def search(
        self,
        query: str,
        embedding: list[float],
        model: str | None,
        limit: int,
        embedding_model: str,
        score_query: str | None = None,
    ) -> list[Citation]:
        if not self.pool:
            return []
        candidate_limit = max(1, limit)
        filters = """
            c.enabled AND d.status='ready'
            AND COALESCE(c.metadata->>'chunk_role', 'child')='child'
            AND ($2::text IS NULL OR c.metadata->>'model'=$2)
            AND COALESCE(c.metadata->>'embedding_model', 'legacy-hash-512')=$3
        """
        vector_sql = f"""
            SELECT c.document_id, c.chunk_id, c.content, c.metadata
            FROM knowledge_chunks c JOIN knowledge_documents d ON d.document_id = c.document_id
            WHERE {filters}
            ORDER BY c.embedding <=> $1::vector
            LIMIT $4
        """
        lexical_sql = f"""
            SELECT c.document_id, c.chunk_id, c.content, c.metadata
            FROM knowledge_chunks c JOIN knowledge_documents d ON d.document_id = c.document_id
            WHERE {filters}
        """
        vector_rows = await self.pool.fetch(
            vector_sql, self._vector_literal(embedding), model, embedding_model, candidate_limit
        )
        lexical_rows = await self.pool.fetch(lexical_sql, model, embedding_model)
        text_rows = self._bm25_rank(query, lexical_rows, candidate_limit)
        ranking_query = score_query or query
        fused: dict[str, dict[str, Any]] = {}
        rrf_k = 60
        for rank, row in enumerate(vector_rows, start=1):
            fused[str(row["chunk_id"])] = {"row": row, "rrf_score": 1 / (rrf_k + rank)}
        for rank, row in enumerate(text_rows, start=1):
            key = str(row["chunk_id"])
            if key not in fused:
                fused[key] = {"row": row, "rrf_score": 0.0}
            fused[key]["rrf_score"] += 1 / (rrf_k + rank)

        ranked: list[tuple[float, Citation, str | None]] = []
        for item in fused.values():
            row = item["row"]
            metadata = self._metadata(row["metadata"])
            content = row["content"]
            chapter = str(metadata.get("chapter", ""))
            lexical_score = self._lexical_score(ranking_query, f"{chapter}\n{content}")
            entity_score = self._entity_alignment_score(ranking_query, f"{chapter}\n{content}")
            rrf_score = float(item["rrf_score"])
            citation = Citation(
                source_id=str(row["document_id"]),
                document_id=str(row["document_id"]),
                chunk_id=str(row["chunk_id"]),
                source_type="pdf",
                title=f"{metadata.get('model', 'Manual')} - {chapter or 'Manual'} - page {metadata.get('page_start', 0)}",
                content=content,
                score=round(rrf_score, 6),
                model=metadata.get("model"),
                section_path=metadata.get("section_path"),
                page_start=metadata.get("page_start"),
                page_end=metadata.get("page_end"),
            )
            tie_breaker = (lexical_score * 0.0001) + (entity_score * 0.0001)
            ranked.append((rrf_score + tie_breaker, citation, metadata.get("source_parent_id")))
        selected = sorted(ranked, key=lambda pair: pair[0], reverse=True)[:candidate_limit]
        parent_ids = sorted({parent_id for _, _, parent_id in selected if parent_id})
        parent_content: dict[str, str] = {}
        if parent_ids:
            parent_rows = await self.pool.fetch(
                """SELECT metadata->>'source_chunk_id' AS source_chunk_id, content
                FROM knowledge_chunks
                WHERE document_id = ANY($1::uuid[]) AND metadata->>'source_chunk_id' = ANY($2::text[])""",
                [uuid.UUID(item.document_id) for _, item, _ in selected if item.document_id],
                parent_ids,
            )
            parent_content = {str(row["source_chunk_id"]): str(row["content"]) for row in parent_rows}
        citations: list[Citation] = []
        for _, citation, parent_id in selected:
            parent = parent_content.get(parent_id or "")
            if parent:
                citation.content = f"{citation.content}\n\n????????\n{parent[:2400]}"
            citations.append(citation)
        return citations


pgvector_store = PgVectorKnowledgeStore()

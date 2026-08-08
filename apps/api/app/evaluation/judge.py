import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings
from app.usage import record_usage


@dataclass(frozen=True)
class RagJudgment:
    score: int
    reason: str


class DeepSeekRagJudge:
    @staticmethod
    def _strict_base_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        return normalized if normalized.endswith("/beta") else f"{normalized}/beta"

    @staticmethod
    def _parse_judgment(payload: str | dict[str, Any] | None) -> RagJudgment | None:
        try:
            data = json.loads(payload or "{}") if isinstance(payload, str) else (payload or {})
            score = data.get("score")
            if isinstance(score, bool):
                return None
            score = int(score)
            if not 1 <= score <= 5:
                return None
            reason = str(data.get("reason") or "").strip()
            return RagJudgment(score=score, reason=reason or "?????????")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _prompt(question: str, answer: str, required_points: list[str], reference_answer: str, forbidden_points: list[str], citation_titles: list[str]) -> str:
        payload = {
            "question": question,
            "answer": answer,
            "reference_points": required_points,
            "reference_answer": reference_answer,
            "forbidden_points": forbidden_points,
            "retrieved_evidence_titles": citation_titles,
            "instruction": (
                "Judge semantic correctness, completeness, consistency with reference points, and forbidden facts. The reason must be concise Chinese. "
                "Accept equivalent wording, units, and expanded explanations. Do not require literal keyword matches. "
                "Score 4 or 5 only when the answer is correct; score 3 or below when it has a material omission, "
                "is unsupported, does not answer the question, or contradicts the references."
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _strict_function_call(self, client: AsyncOpenAI, model: str, prompt: str):
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Evaluate the RAG answer and call the required function exactly once."},
                {"role": "user", "content": prompt},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "submit_rag_judgment",
                    "description": "Submit the final RAG evaluation score.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "reason"],
                        "additionalProperties": False,
                    },
                },
            }],
            tool_choice="auto",
            temperature=0,
        )
        record_usage(model, response.usage)
        tool_calls = response.choices[0].message.tool_calls or []
        for call in tool_calls:
            if call.function.name == "submit_rag_judgment":
                return self._parse_judgment(call.function.arguments)
        return None

    async def _json_fallback(self, client: AsyncOpenAI, model: str, prompt: str):
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return one valid JSON object only, with integer score and Chinese reason."},
                {"role": "user", "content": f"{prompt}\nReturn JSON: {{\"score\": 1, \"reason\": \"????\"}}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=240,
        )
        record_usage(model, response.usage)
        return self._parse_judgment(response.choices[0].message.content)

    async def score(
        self,
        question: str,
        answer: str,
        required_points: list[str],
        reference_answer: str,
        forbidden_points: list[str],
        citation_titles: list[str],
    ) -> RagJudgment | None:
        settings = get_settings()
        if not settings.deepseek_api_key:
            return None
        prompt = self._prompt(question, answer, required_points, reference_answer, forbidden_points, citation_titles)
        strict_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=self._strict_base_url(settings.deepseek_base_url),
            timeout=settings.llm_timeout_seconds,
        )
        for _ in range(3):
            try:
                judgment = await asyncio.wait_for(
                    self._strict_function_call(strict_client, settings.deepseek_model, prompt),
                    timeout=settings.llm_timeout_seconds,
                )
                if judgment:
                    return judgment
            except Exception:
                continue
        client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=settings.llm_timeout_seconds)
        for _ in range(3):
            try:
                judgment = await asyncio.wait_for(
                    self._json_fallback(client, settings.deepseek_model, prompt),
                    timeout=settings.llm_timeout_seconds,
                )
                if judgment:
                    return judgment
            except Exception:
                continue
        return None

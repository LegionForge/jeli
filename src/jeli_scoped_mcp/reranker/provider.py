"""Re-ranking providers: score (query, candidate) relevance post vector-search."""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)


class RerankerProvider(ABC):
    @abstractmethod
    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """Score candidates against query. Returns same list sorted by
        relevance_score DESC, with 'relevance_score' float added to each item."""

    @classmethod
    def from_settings(cls, settings: "Settings") -> "RerankerProvider":
        if not settings.reranker_enabled or not settings.litellm_base_url:
            return NullReranker()
        return LiteLLMReranker(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            model=settings.reranker_model,
            timeout=settings.reranker_timeout,
            candidate_limit=settings.reranker_candidate_limit,
        )


class NullReranker(RerankerProvider):
    """No-op reranker — converts vector distance to a relevance score without LLM."""

    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        for c in candidates:
            c["relevance_score"] = round(1.0 - c.get("distance", 0.5), 4)
        return candidates


class LiteLLMReranker(RerankerProvider):
    """Batch re-ranker via a LiteLLM proxy (one prompt call for all candidates)."""

    _PROMPT_TEMPLATE = (
        "You are a relevance judge for a personal memory system.\n\n"
        "Query: {query}\n\n"
        "Rate each memory's relevance to the query on a scale of 0.00 to 1.00.\n"
        "Return ONLY a JSON array of numbers in the same order as the memories, nothing else.\n"
        "Example: [0.95, 0.12, 0.87, 0.03]\n\n"
        "Memories:\n{numbered_list}"
    )

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "local-chat",
        timeout: float = 30.0,
        candidate_limit: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.candidate_limit = candidate_limit

    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return candidates

        candidates = candidates[: self.candidate_limit]
        numbered = "\n".join(
            f"{i + 1}. {c['content'][:300]}" for i, c in enumerate(candidates)
        )
        prompt = self._PROMPT_TEMPLATE.format(query=query, numbered_list=numbered)

        scores = await self._call_llm(prompt, expected=len(candidates))

        for i, c in enumerate(candidates):
            c["relevance_score"] = scores[i] if i < len(scores) else 0.0

        return sorted(candidates, key=lambda x: x["relevance_score"], reverse=True)

    async def _call_llm(self, prompt: str, expected: int) -> list[float]:
        import aiohttp

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 256,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return self._parse_scores(text, expected)
        except Exception:
            logger.warning("reranker call failed — falling back to vector order", exc_info=True)
            return []

    def _parse_scores(self, text: str, expected: int) -> list[float]:
        match = re.search(r"\[[-\d.,\s]+\]", text)
        if not match:
            logger.warning("reranker: could not parse JSON array from response: %r", text[:200])
            return []
        try:
            raw = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("reranker: JSON decode failed for: %r", match.group()[:200])
            return []

        if len(raw) != expected:
            logger.warning(
                "reranker: got %d scores for %d candidates — padding with 0.0",
                len(raw),
                expected,
            )

        scores = [max(0.0, min(1.0, float(s))) for s in raw]
        # Pad missing entries with 0.0
        while len(scores) < expected:
            scores.append(0.0)
        return scores[:expected]


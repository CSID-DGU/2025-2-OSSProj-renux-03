from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import List

from openai import OpenAI

from src.config import OPENAI_API_KEY, QUERY_REWRITE_ENABLED, QUERY_REWRITE_MAX_VARIANTS, QUERY_REWRITE_MODEL


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)


def generate_query_variants(query: str) -> List[str]:
    if not query or not QUERY_REWRITE_ENABLED or not OPENAI_API_KEY:
        return [query]

    variants: List[str] = [query]

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=QUERY_REWRITE_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 대학 챗봇 retrieval query rewriter입니다. "
                        "원문 질문을 더 잘 검색되는 한국어 질의로 정규화하세요. "
                        "답변은 JSON만 반환하며, variants 배열에는 중복 없는 짧은 검색 질의만 넣으세요."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": query,
                            "instructions": [
                                "첫 번째 질의는 원문 의도를 보존한 대표 검색 질의",
                                "추가 질의는 용어 정규화, 날짜/학사 표현 보정, 복합 질문 분해 중심",
                                f"최대 {QUERY_REWRITE_MAX_VARIANTS}개",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        raw_variants = payload.get("variants", [])
        for item in raw_variants:
            if isinstance(item, str):
                normalized = item.strip()
                if normalized and normalized not in variants:
                    variants.append(normalized)
    except Exception as exc:
        logger.warning("Query rewrite failed, falling back to original query: %s", exc)

    return variants[:QUERY_REWRITE_MAX_VARIANTS]

"""대화 이력을 보존하며 LLM 답변을 생성하는 헬퍼입니다.

함수명은 기존 `api.rag_service` 호출부와 호환되도록 유지합니다.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx
import redis
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_redis import RedisChatMessageHistory

from src.config import (
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_NUM_PREDICT,
    OLLAMA_TEMPERATURE,
    OLLAMA_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    REDIS_URL,
)

logger = logging.getLogger(__name__)
_MEMORY_HISTORY: dict[str, list[BaseMessage]] = {}
_REDIS_WARNING_LOGGED = False

SYSTEM_PROMPT_TEMPLATE = """
당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 다른 수식어나 전문 분야를 언급하지 마세요.
오늘 날짜 및 시간: {current_date}

[지침]
1. 아래 [참고 자료]에 명시된 사실만 사용해 답변하세요. 참고 자료 밖의 학교 정보, 일반 지식, 추론, 예시는 절대 추가하지 마세요.
2. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [참고 자료]에 있다면 해당 설명 바로 아래에 '[사이트로 이동하기](URL)' 형식으로 적어주세요. 첨부파일은 본문 중에 '[파일명](URL)' 형식으로 포함하세요.
3. 친절한 한국어(해요체)로 답변하세요.
4. 절차나 방법을 설명할 때는 반드시 번호를 매겨 단계별로 작성하세요.
5. 참고 자료에서 답을 확인할 수 없으면 "현재 수집된 문서에서는 확인되지 않습니다"라고 답하고, 그 외 내용을 덧붙이지 마세요.
6. {current_date} 기준 최신 정보를 우선하여 답변하세요.
7. 가독성을 위해 불필요한 마크다운(과도한 볼드체 등)은 피하고, 링크는 반드시 마크다운 형식으로 작성하세요.
8. 이전 대화의 답변 내용은 근거로 사용하지 마세요. 현재 답변의 근거는 오직 아래 [참고 자료]입니다.
9. 질문에 '최근', '어제' 등 시간 표현이 포함된 경우, [참고 자료]의 게시일과 현재 날짜({current_date})를 비교하여 정확히 계산해 답변하세요.
10. 질문이 "무엇이 있나", "뭐 받을 수 있나", "알려줘"처럼 목록형이면 2~5개 항목으로 나누어 답변하세요.
11. 각 항목은 "대상", "혜택/내용", "신청/확인 방법", "주의사항" 중 참고 자료에서 확인되는 정보만 짧게 적으세요.
12. 참고 자료에 없으면 "확인되지 않습니다"라고 쓰고, 다른 문서 내용을 섞어서 단정하지 마세요.
13. 질문에 "장학", "장학금"이 있으면 장학금명, 금액, 선발요건, 신청기간을 우선하고 일반 선발절차는 보조 정보로만 쓰세요.
14. 질문의 핵심어가 제목에 포함된 문서를 가장 신뢰하세요. 핵심어가 없는 문서는 관련 주의사항으로만 사용하세요.
15. 답변에 포함하는 모든 구체 정보는 참고 자료의 제목, 게시일, URL, 내용 중 하나로 검증 가능해야 합니다.

[출력 형식 지침]
- 첫 문장은 질문에 대한 결론을 한 문장으로 짧게 쓰세요.
- 번호 목록은 반드시 '번호 + 제목'을 같은 줄에 작성하세요.
- 번호 목록 내부에는 빈 줄을 넣지 마세요.
- 번호 목록의 하위 항목은 '-' 기호 bullet만 사용하세요.
- ○, ·, ▪ 등의 특수기호는 사용하지 마세요.
- 번호 항목과 번호 항목 사이에만 빈 줄을 허용하세요.
- 불필요한 줄바꿈이나 개행으로 문단을 분리하지 마세요.
- 답변 전체는 가능하면 700자 이내로 작성하세요.

[참고 자료]
{context}
"""

GENERAL_CHAT_PROMPT_TEMPLATE = """
당신은 동국대학교 AI 어시스턴트 '동똑이'입니다.
오늘 날짜 및 시간: {current_date}

[지침]
1. 사용자가 인사, 자기소개, 감사, 짧은 잡담을 하면 자연스럽고 짧게 응답하세요.
2. 동국대학교 공개 정보 제공 챗봇이라는 역할을 분명히 하세요.
3. 공지, 학사, 장학, 수강신청, 도서관, 식단, 학과 등 학교 정보가 필요하면 구체적으로 질문해 달라고 안내하세요.
4. 출처나 문서 내용을 지어내지 마세요.
5. 답변은 한국어 해요체로 3문장 이내로 작성하세요.
"""


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL)


def _history(session_id: str) -> RedisChatMessageHistory:
    return RedisChatMessageHistory(session_id, redis_client=_redis_client())


def _load_history(session_id: str) -> tuple[RedisChatMessageHistory | None, list[BaseMessage]]:
    global _REDIS_WARNING_LOGGED
    try:
        history = _history(session_id)
        return history, history.messages
    except Exception as exc:  # noqa: BLE001
        if not _REDIS_WARNING_LOGGED:
            logger.warning("Redis chat history unavailable; falling back to memory history: %s", exc)
            _REDIS_WARNING_LOGGED = True
        return None, _MEMORY_HISTORY.get(session_id, [])


def _save_history(
    session_id: str,
    history: RedisChatMessageHistory | None,
    question: str,
    answer: str,
) -> None:
    if history is not None:
        history.add_user_message(question)
        history.add_ai_message(answer)
        return

    messages = _MEMORY_HISTORY.setdefault(session_id, [])
    messages.extend([HumanMessage(content=question), AIMessage(content=answer)])
    del messages[:-16]


def _message_content(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return str(content)


def _to_ollama_history(messages: list[BaseMessage], limit: int = 8) -> list[dict[str, str]]:
    converted: list[dict[str, str]] = []
    for message in messages[-limit:]:
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue
        content = _message_content(message).strip()
        if content:
            converted.append({"role": role, "content": content})
    return converted


async def _call_ollama(messages: list[dict[str, str]]) -> str:
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
    data = response.json()
    content = data.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama returned an empty response.")
    return content.strip()


async def _call_openai(messages: list[dict[str, str]]) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=OLLAMA_TEMPERATURE,
    )
    answer = response.choices[0].message.content or ""
    if not answer.strip():
        raise RuntimeError("OpenAI returned an empty response.")
    return answer.strip()


async def generate_langchain_answer(
    question: str,
    context: str,
    session_id: str | None = None,
    current_date: str = "",
) -> str:
    """대화 이력을 활용해 답변을 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info("Generating answer for session_id: %s with provider=%s", actual_session_id, LLM_PROVIDER)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        current_date=current_date,
        context=context or "컨텍스트가 제공되지 않았습니다.",
    )
    history, _ = _load_history(actual_session_id)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    if LLM_PROVIDER == "openai":
        answer = await _call_openai(messages)
    else:
        answer = await _call_ollama(messages)

    _save_history(actual_session_id, history, question, answer)
    return answer


async def generate_general_chat_answer(
    question: str,
    session_id: str | None = None,
    current_date: str = "",
) -> str:
    """검색 컨텍스트 없이 일반 대화 응답을 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info("Generating general chat answer for session_id: %s with provider=%s", actual_session_id, LLM_PROVIDER)

    history, history_messages = _load_history(actual_session_id)
    previous_messages = _to_ollama_history(history_messages)
    messages = [
        {"role": "system", "content": GENERAL_CHAT_PROMPT_TEMPLATE.format(current_date=current_date)},
        *previous_messages,
        {"role": "user", "content": question},
    ]

    if LLM_PROVIDER == "openai":
        answer = await _call_openai(messages)
    else:
        answer = await _call_ollama(messages)

    _save_history(actual_session_id, history, question, answer)
    return answer


__all__ = ["generate_langchain_answer", "generate_general_chat_answer"]

"LLM을 사용하여 사용자의 질문을 가장 관련 있는 데이터셋으로 라우팅합니다."
from __future__ import annotations

from functools import lru_cache
import logging
from typing import List

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field, ValidationError
from langchain_openai import ChatOpenAI

from src.config import LLM_ROUTER_DESCRIPTIONS, OPENAI_API_KEY, OPENAI_MODEL

# LLM이 출력할 라우팅 결정의 스키마를 정의합니다.
# 여러 데이터셋과 관련될 수 있으므로 문자열 리스트를 사용합니다.
class RouteChoice(BaseModel):
    """사용자 질문에 가장 관련 있는 데이터셋(들)의 이름."""
    names: List[str] = Field(
        description="선택된 데이터셋의 이름 리스트. 예를 들어 ['rules', 'schedule']와 같이 출력되어야 합니다."
    )

# LangChain V2부터 PydanticOutputParser를 사용하는 것이 권장됩니다.
parser = PydanticOutputParser(pydantic_object=RouteChoice)

# LLM에게 제공할 프롬프트 템플릿입니다.
# 사용자의 질문과 각 데이터셋의 설명을 제공하고, 가장 적절한 것을 선택하도록 요청합니다.
prompt = PromptTemplate(
    template="""사용자의 질문을 분석하여 가장 관련 있는 데이터셋으로 라우팅하는 역할을 수행합니다.
질문에 답변하기 위해 참조해야 할 가장 적절한 데이터셋을 하나 이상 선택하세요.

사용 가능한 데이터셋:
{destinations}

사용자 질문:
{query}

선택된 데이터셋의 이름을 포함하는 JSON 객체를 다음 형식으로 출력하세요:
{format_instructions}
""",
    input_variables=["query", "destinations"],
    partial_variables={"format_instructions": parser.get_format_instructions()},
)

@lru_cache(maxsize=1)
def _build_router_chain():
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, model_kwargs={"response_format": {"type": "json_object"}})
    return prompt | llm | parser

def _format_destinations() -> str:
    """LLM 프롬프트에 포함될 데이터셋 설명의 형식을 지정합니다."""
    return "\n".join(f"- {name}: {desc}" for name, desc in LLM_ROUTER_DESCRIPTIONS.items())


def _route_by_rules(query: str) -> List[str]:
    text = query.lower()
    routes: list[str] = []
    rules = [
        ("schedule", ["일정", "학사일정", "수강신청", "개강", "종강", "방학", "시험", "정정 기간"]),
        ("rules", ["학칙", "규정", "졸업요건", "졸업 요건", "성적", "징계", "휴학", "복학"]),
        ("courses", ["교과목", "강의", "수업", "전공", "학점", "이수구분", "교육과정"]),
        ("staff", ["연락처", "전화번호", "담당자", "부서", "학과 사무실", "교수"]),
        ("notices", ["공지", "장학", "모집", "신청", "등록금", "행사", "발표"]),
    ]
    for route, keywords in rules:
        if any(keyword in text for keyword in keywords):
            routes.append(route)
    return routes or ["notices"]


async def route_query(query: str) -> List[str]:
    """LLM 라우터를 사용하여 사용자 질문에 가장 적합한 데이터셋을 결정합니다."""
    if not query:
        # 질문이 비어 있으면 기본값으로 'notices'를 반환합니다.
        return ["notices"]
    if not OPENAI_API_KEY:
        return _route_by_rules(query)

    formatted_destinations = _format_destinations()
    
    try:
        router_chain = _build_router_chain()
        result = await router_chain.ainvoke({
            "query": query,
            "destinations": formatted_destinations,
        })
        # LLM의 선택에서 유효한 데이터셋 이름만 필터링합니다.
        # LLM이 목록에 없는 이름을 지어낼 경우를 대비합니다.
        valid_routes = [name for name in result.names if name in LLM_ROUTER_DESCRIPTIONS]
        return valid_routes or ["notices"]  # 유효한 선택이 없으면 기본값으로 'notices' 반환
    except ValidationError as e:
        logging.warning(f"LLM 라우터의 Pydantic 검증에 실패했습니다: {e}. 'notices'로 기본 설정합니다.")
        return ["notices"]
    except Exception as e:
        logging.error(f"LLM 라우터에서 예기치 않은 오류가 발생했습니다: {e}. 'notices'로 기본 설정합니다.")
        return ["notices"]

# 이 파일에서 외부에 제공할 함수는 route_query 뿐입니다.
__all__ = ["route_query"]

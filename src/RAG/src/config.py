"""프로젝트 전반에서 사용하는 설정과 상수 모음입니다."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

# 주요 파일 시스템 경로를 정의한다.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
ARTIFACT_DIR = BASE_DIR / "artifacts"
CHROMA_DIR = ARTIFACT_DIR / "db_chroma"
MODEL_DIR = ARTIFACT_DIR / "models"
CHUNKS_DIR = ARTIFACT_DIR / "chunks"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
LOG_DIR = DATA_DIR / "logs"
SOURCES_CONFIG_PATH = CONFIG_DIR / "sources.yaml"

# 모듈이 임포트될 때 필요한 디렉터리를 미리 만든다.
for _path in (
    DATA_DIR,
    CONFIG_DIR,
    ARTIFACT_DIR,
    CHROMA_DIR,
    MODEL_DIR,
    CHUNKS_DIR,
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    LOG_DIR,
):
    _path.mkdir(parents=True, exist_ok=True)

# 임베딩 관련 설정.
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "nlpai-lab/KURE-v1")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cpu")
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))
EMBED_WARMUP_ENABLED = os.getenv("EMBED_WARMUP_ENABLED", "false").lower() == "true"
EMBED_LOCAL_FILES_ONLY = os.getenv("EMBED_LOCAL_FILES_ONLY", "true").lower() == "true"
EMBED_SUPPRESS_MODEL_LOAD_OUTPUT = os.getenv("EMBED_SUPPRESS_MODEL_LOAD_OUTPUT", "true").lower() == "true"
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").upper()
THIRD_PARTY_LOG_LEVEL = os.getenv("THIRD_PARTY_LOG_LEVEL", "WARNING").upper()

# 청크 분할과 검색 기본값.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "300")) # 청크 크기 기본값
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80")) # 청크 겹침 기본값
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "5")) # 검색 결과 개수 기본값
RECENCY_WEIGHT = float(os.getenv("RECENCY_WEIGHT", "0.4")) # Re-ranking 가중치 추가

# LLM 라우터가 각 데이터셋의 역할을 이해하는 데 사용하는 설명
LLM_ROUTER_DESCRIPTIONS = {
    "notices": "학교 생활 전반에 걸친 공지사항, 모집, 발표, 장학금, 등록금, 입시, 휴학, 복학 관련 안내입니다.",
    "rules": "학사 운영, 졸업, 성적, 징계 등 학교의 공식적인 학칙, 규정, 시행세칙에 대한 정보입니다.",
    "schedule": "수강신청, 개강, 종강, 방학, 시험 등 주요 학사일정에 대한 정보입니다.",
    "courses": "개설된 교과목, 수업, 강의, 전공, 선수과목, 학점, 이수구분 등 교과 과정에 대한 상세 정보입니다.",
    "staff": "교직원, 교수, 행정 부서의 연락처, 담당 업무 정보입니다.",
}

# LLM 기본 설정.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "512"))

# OpenAI 설정은 query rewrite/router를 OpenAI로 명시적으로 켤 때만 필요하다.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 컨텍스트 및 retrieval 관련 설정
MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", "8000"))
ASK_TOP_K = int(os.getenv("ASK_TOP_K", "5"))
ASK_MAX_CONTEXT_LENGTH = int(os.getenv("ASK_MAX_CONTEXT_LENGTH", "3000"))
QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "false").lower() == "true"
QUERY_REWRITE_MODEL = os.getenv("QUERY_REWRITE_MODEL", OPENAI_MODEL)
QUERY_REWRITE_MAX_VARIANTS = int(os.getenv("QUERY_REWRITE_MAX_VARIANTS", "3"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "8"))
RERANK_MIN_SCORE = float(os.getenv("RERANK_MIN_SCORE", "0.18"))
CORRECTIVE_RETRY_ENABLED = os.getenv("CORRECTIVE_RETRY_ENABLED", "true").lower() == "true"
DETERMINISTIC_ANSWERS_ENABLED = os.getenv("DETERMINISTIC_ANSWERS_ENABLED", "false").lower() == "true"

# 대화 기록 관련 설정 (인메모리).
MAX_HISTORY_STORE_SIZE = int(os.getenv("MAX_HISTORY_STORE_SIZE", "1000"))

# Redis 대화 기록 백엔드 설정.
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")

# 노트북에서 가져온 입력 데이터 소스 경로.
DATA_SOURCES: Dict[str, Path] = {
    "notices": DATA_DIR / "dongguk_notices.csv",
    "rules": DATA_DIR / "dongguk_rule_texts.csv",
    "schedule": DATA_DIR / "dongguk_schedule.csv",
    "courses_desc": DATA_DIR / "dongguk_statistics_course_descriptions.csv",
    "courses_major": DATA_DIR / "dongguk_statistics_major_course.csv",
    "staff": DATA_DIR / "dongguk_staff_contacts.csv",
}


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "CONFIG_DIR",
    "ARTIFACT_DIR",
    "CHROMA_DIR",
    "MODEL_DIR",
    "CHUNKS_DIR",
    "RAW_DATA_DIR",
    "PROCESSED_DATA_DIR",
    "LOG_DIR",
    "SOURCES_CONFIG_PATH",
    "EMBED_MODEL_NAME",
    "EMBED_DEVICE",
    "EMBED_BATCH_SIZE",
    "EMBED_WARMUP_ENABLED",
    "EMBED_LOCAL_FILES_ONLY",
    "EMBED_SUPPRESS_MODEL_LOAD_OUTPUT",
    "APP_LOG_LEVEL",
    "THIRD_PARTY_LOG_LEVEL",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "DEFAULT_TOP_K",
    "MAX_CONTEXT_LENGTH",
    "ASK_TOP_K",
    "ASK_MAX_CONTEXT_LENGTH",
    "LLM_ROUTER_DESCRIPTIONS",
    "LLM_PROVIDER",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_NUM_PREDICT",
    "OLLAMA_TIMEOUT_SECONDS",
    "OLLAMA_TEMPERATURE",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "QUERY_REWRITE_ENABLED",
    "QUERY_REWRITE_MODEL",
    "QUERY_REWRITE_MAX_VARIANTS",
    "RERANK_TOP_N",
    "RERANK_MIN_SCORE",
    "CORRECTIVE_RETRY_ENABLED",
    "DETERMINISTIC_ANSWERS_ENABLED",
    "MAX_HISTORY_STORE_SIZE",
    "REDIS_URL",
    "DATA_SOURCES",
]

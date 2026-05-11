# Dongttok RAG Ingestion

동똑이의 공개 데이터 수집 및 검색용 1차 ingestion 파이프라인입니다.  
현재 범위는 동국대학교 공개 공지, 학사일정, 학칙/규정, 교직원 연락처, 교과목/교육과정 CSV를 정규화해 ChromaDB에 적재하는 것입니다.  
기존에 확보한 `data/*.csv`는 학사일정, 규정, 연락처, 교과목 같은 정형 데이터의 1차 원천으로 사용합니다. 공지는 웹 크롤러가 기존 공지 CSV의 가장 오래된 날짜까지 직접 수집하도록 구성되어 있으며, 최종적으로 CSV 없이 운영할 수 있습니다.

## 추가된 구조

```text
src/RAG/
  config/
    sources.yaml
  data/
    raw/
    processed/
    logs/
  src/
    ingestion/
      crawlers/
      parsers/
      normalizers/
      legacy_csv_source.py
      dedup.py
      pipeline.py
    rag/
      chunker.py
      embedder.py
      retriever.py
      vector_store.py
    schemas/
      document.py
      search.py
  tests/
```

## 설정

수집 대상 URL과 게시판 코드는 [config/sources.yaml](/Users/172mac/Desktop/AI/dongttok/src/RAG/config/sources.yaml)에 있습니다.  
소스 URL은 코드에 하드코딩하지 않고 YAML에서 읽습니다.

현재 등록된 주요 source는 아래와 같습니다.

- 공지: 일반공지, 학사공지, 장학공지, 국제공지, 국제교류공지, 유학생공지, 학술공지, 행사공지, 안전공지, 입학공지
- 학사: 학사일정
- 규정: `data/dongguk_rule_texts.csv`
- 생활/행정: 교직원 연락처
- 교육과정: 통계학과 교과목 해설, 전공 교과목
- 고정 안내 페이지: 학사제도, 등록/증명, 학생서비스, 국제교류, 캠퍼스맵, 도서관, 남산학사 식단표
- 공공데이터: 대학알리미 대학 기본 정보, 대학별 학과정보, 교육여건 현황 API URL 등록

## 실행

1. 의존성 설치

```bash
cd /Users/172mac/Desktop/AI/dongttok/src/RAG
pip3 install -r requirements.txt
```

2. API 서버 실행

```bash
uvicorn api.rag_service:app --reload --host 0.0.0.0 --port 8000
```

기본적으로 startup 시 임베딩 warmup은 건너뛰고, 외부 라이브러리 로그는 축소합니다. 필요하면 아래처럼 조정할 수 있습니다.

```bash
APP_LOG_LEVEL=INFO THIRD_PARTY_LOG_LEVEL=WARNING EMBED_WARMUP_ENABLED=true \
uvicorn api.rag_service:app --reload --host 0.0.0.0 --port 8000
```

기본값은 `EMBED_LOCAL_FILES_ONLY=true`입니다. 로컬 캐시에 임베딩 모델이 없으면 해시 기반 fallback 임베딩으로 적재가 계속됩니다. Hugging Face에서 모델을 새로 내려받아야 하는 환경에서는 `EMBED_LOCAL_FILES_ONLY=false`를 지정하세요.
모델 로딩 progress 출력은 기본적으로 숨깁니다. 문제를 진단해야 하면 `EMBED_SUPPRESS_MODEL_LOAD_OUTPUT=false`를 지정하세요.

답변 생성 LLM은 기본적으로 로컬 Ollama를 사용합니다. 기본 모델은 빠른 응답을 우선해 `qwen2.5:1.5b`입니다. 품질을 우선해야 하면 아래처럼 모델만 바꿔 실행하세요.

```bash
OLLAMA_MODEL=gemma4:e4b uvicorn api.rag_service:app --reload --host 0.0.0.0 --port 8000
```

로컬 실행 기본값은 `OLLAMA_BASE_URL=http://127.0.0.1:11434`입니다. Docker 컨테이너에서 호스트의 Ollama를 사용할 때는 `OLLAMA_BASE_URL=http://host.docker.internal:11434`를 사용하세요.
빠른 응답을 위해 `/ask`는 기본적으로 상위 `5`개 문서와 최대 `3000`자 context만 답변 모델에 전달합니다. 필요하면 `ASK_TOP_K`, `ASK_MAX_CONTEXT_LENGTH`, `OLLAMA_NUM_PREDICT`로 조정할 수 있습니다.

3. 수집 실행

```bash
curl -X POST http://127.0.0.1:8000/ingest/run \
  -H "Content-Type: application/json" \
  -d '{"source_name":"dongguk_academic_notice","limit":20,"force":false}'
```

전체 enabled source를 제한 없이 수집하려면 `source_name`과 `limit`을 `null`로 둡니다.

```bash
curl -X POST http://127.0.0.1:8000/ingest/run \
  -H "Content-Type: application/json" \
  -d '{"source_name":null,"limit":null,"force":false}'
```

기존 ingestion 산출물과 `dongguk_documents` Chroma 컬렉션을 비우고 전체를 다시 만들려면 `clean`을 켭니다. `clean`은 공유 컬렉션을 초기화하므로 `source_name`은 반드시 `null`이어야 합니다.

```bash
curl -X POST http://127.0.0.1:8000/ingest/run \
  -H "Content-Type: application/json" \
  -d '{"source_name":null,"limit":null,"force":true,"clean":true}'
```

4. 검색 실행

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"수강신청 정정 기간 알려줘","category":"학사공지","top_k":5}'
```

## API

- `GET /health`
- `POST /ingest/run`
- `GET /ingest/status`
- `POST /search`
- `POST /answer/context`

## 저장 방식

- 원문 문서: `data/raw/<source_name>.jsonl`
- 정규화 문서 인덱스: `data/processed/document_index.jsonl`
- 청크 결과: `data/processed/<source_name>_chunks.jsonl`
- 수집 로그: `data/logs/ingest_status.jsonl`
- 벡터 저장소: `artifacts/db_chroma`

## 테스트

```bash
cd /Users/172mac/Desktop/AI/dongttok/src/RAG
pytest tests/test_dedup.py tests/test_chunker.py
```

## 주의

- 로그인 기반 시스템 데이터는 수집하지 않습니다.
- 개인정보, 성적, 시간표, 과제, 출석, 개인 상담, 개인 포트폴리오는 제외합니다.
- 수집 시 `User-Agent`, 요청 간격, 타임아웃을 YAML로 관리합니다.
- 공지 source는 웹 크롤러 기반입니다. 각 게시판은 `crawl_until_date`까지 내려가며, 이 날짜는 기존 `data/dongguk_notices.csv`의 게시판별 가장 오래된 게시일을 기준으로 설정했습니다.
- 학사일정, 규정, 연락처, 교과목 source는 현재 `legacy CSV` 기반으로 적재합니다.
- 학교 고정 안내 페이지는 `static_page` crawler로 수집합니다.
- 공지 PDF 첨부파일 파싱은 `sources.yaml`에서 `parse_pdf_attachments: true`로 켤 수 있습니다.
- 공공데이터포털 API source는 인증키가 필요하므로 기본 `enabled: false`입니다.
- 일반 증분 실행에서는 이미 `document_index.jsonl`에 있는 URL을 상세 페이지 요청 전에 건너뜁니다.

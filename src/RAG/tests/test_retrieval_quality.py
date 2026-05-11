from __future__ import annotations

import numpy as np

from src.rag.bm25 import BM25ChunkIndex
from src.rag.query_intent import classify_query_intent
from src.rag.retriever import Retriever


class FakeEmbedder:
    def embed_texts(self, texts):
        return np.zeros((len(list(texts)), 1), dtype=np.float32)

    def embed_query(self, query):
        return np.zeros((1, 1), dtype=np.float32)


class FakeVectorStore:
    def search(self, collection_name, query_embedding, top_k, where=None):
        return {
            "ids": [["notice-1"]],
            "documents": [["장학금 문의처: 장학팀 02-2260-3698"]],
            "metadatas": [
                [
                    {
                        "document_id": "notice-1",
                        "title": "복지장학 안내",
                        "category": "장학공지",
                        "document_type": "notice",
                        "published_at": "2026-02-12",
                    }
                ]
            ],
            "distances": [[0.01]],
        }


class MultiNoticeVectorStore:
    def search(self, collection_name, query_embedding, top_k, where=None):
        return {
            "ids": [["old-notice", "new-notice"]],
            "documents": [["아주 관련 있는 오래된 공지입니다.", "최근 올라온 공지입니다."]],
            "metadatas": [
                [
                    {
                        "document_id": "old-notice",
                        "title": "최근 공지 관련 오래된 안내",
                        "category": "일반공지",
                        "document_type": "notice",
                        "published_at": "2020-01-01",
                    },
                    {
                        "document_id": "new-notice",
                        "title": "최근 공지 안내",
                        "category": "일반공지",
                        "document_type": "notice",
                        "published_at": "2099-01-01",
                    },
                ]
            ],
            "distances": [[0.01, 0.9]],
        }


class NoDateVectorStore:
    def search(self, collection_name, query_embedding, top_k, where=None):
        return {
            "ids": [["no-date-doc"]],
            "documents": [["오늘 하루 보지 않기 같은 페이지 장식 문구가 있는 정적 페이지입니다."]],
            "metadatas": [
                [
                    {
                        "document_id": "no-date-doc",
                        "title": "정적 페이지",
                        "category": "학생생활",
                        "document_type": "student_service",
                        "published_at": "",
                    }
                ]
            ],
            "distances": [[0.01]],
        }


def test_classify_food_query_uses_hard_food_filter():
    intent = classify_query_intent("학식 뭐야")

    assert intent.name == "food"
    assert intent.hard_filters == {"document_type": "food"}
    assert intent.block_fallback is True


def test_classify_course_registration_policy_query_prefers_academic_documents():
    intent = classify_query_intent("수강신청 기준 알려줘")

    assert intent.name == "course_registration"
    assert intent.score_profile == "academic_policy"
    assert intent.preferred_document_types[0] == "academic"


def test_classify_course_query_uses_hard_course_filter():
    intent = classify_query_intent("통계학과 전공과목 뭐 있어")

    assert intent.name == "course"
    assert intent.hard_filters == {"document_type": "course"}
    assert intent.block_fallback is True


def test_classify_latest_notice_query_uses_latest_notice_profile():
    intent = classify_query_intent("최근 올라온 공지 뭐야")

    assert intent.name == "notice"
    assert intent.score_profile == "latest_notice"


def test_bm25_prefers_department_contact_chunk():
    index = BM25ChunkIndex.from_chunks(
        [
            {
                "id": "department-1",
                "content": "조직: 학사지원팀\n담당업무: 학부 수업 성적 관리\n전화번호: 02-2260-3621",
                "metadata": {
                    "title": "학사지원팀 - 학부 수업 성적 관리",
                    "category": "부서/학과 전화번호",
                    "document_type": "department",
                },
            },
            {
                "id": "notice-1",
                "content": "장학금 신청 안내입니다.",
                "metadata": {
                    "title": "복지장학 안내",
                    "category": "장학공지",
                    "document_type": "notice",
                },
            },
        ]
    )

    hits = index.search("학사지원팀 전화번호 알려줘", top_k=2, filters={"document_type": "department"})

    assert [hit["id"] for hit in hits] == ["department-1"]


def test_retriever_hard_filter_blocks_unrelated_vector_results():
    retriever = Retriever(FakeEmbedder(), FakeVectorStore(), "test")
    retriever.bm25_index = BM25ChunkIndex.from_chunks([])
    intent = classify_query_intent("학식 뭐야")

    hits = retriever.search("학식 뭐야", top_k=5, intent=intent)

    assert hits == []


def test_retriever_merges_bm25_department_candidate():
    retriever = Retriever(FakeEmbedder(), FakeVectorStore(), "test")
    retriever.bm25_index = BM25ChunkIndex.from_chunks(
        [
            {
                "id": "department-1",
                "content": "조직: 학사지원팀\n담당업무: 학부 수업 성적 관리\n전화번호: 02-2260-3621",
                "metadata": {
                    "document_id": "department-1",
                    "title": "학사지원팀 - 학부 수업 성적 관리",
                    "category": "부서/학과 전화번호",
                    "document_type": "department",
                },
            }
        ]
    )
    intent = classify_query_intent("학사지원팀 전화번호 알려줘")

    hits = retriever.search("학사지원팀 전화번호 알려줘", top_k=5, intent=intent)

    assert hits
    assert hits[0]["metadata"]["document_type"] == "department"


def test_latest_notice_profile_prefers_recent_notice_over_old_high_vector_match():
    retriever = Retriever(FakeEmbedder(), MultiNoticeVectorStore(), "test")
    retriever.bm25_index = BM25ChunkIndex.from_chunks([])
    intent = classify_query_intent("최근 올라온 공지 뭐야")

    hits = retriever.search("최근 올라온 공지 뭐야", top_k=2, intent=intent)

    assert hits[0]["id"] == "new-notice"


def test_date_range_filter_excludes_documents_without_published_at():
    retriever = Retriever(FakeEmbedder(), NoDateVectorStore(), "test")
    retriever.bm25_index = BM25ChunkIndex.from_chunks([])

    hits = retriever.search(
        "이번 주 공지 알려줘",
        top_k=5,
        date_range=["2026-05-04", "2026-05-10"],
    )

    assert hits == []

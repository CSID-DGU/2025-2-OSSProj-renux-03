from api import rag_service


def test_course_followup_uses_previous_numbered_course(monkeypatch):
    session_id = "course-session"
    rag_service._SESSION_COURSE_HITS[session_id] = [
        {
            "id": "STA2019",
            "content": "학수번호: STA2019 교과목명: 통계수학및R실습 학점: 3 전공구분: 기초 이수대상: 학사2년 개설학기: 1",
            "metadata": {
                "source": "dongguk_official",
                "title": "STA2019 통계수학및R실습",
                "document_type": "course",
                "url": "dongguk://major-course/STA2019",
                "source_url": "dongguk://major-course/STA2019",
            },
        }
    ]
    monkeypatch.setattr(
        rag_service,
        "_find_course_description",
        lambda code, name: {
            "id": "desc-STA2019",
            "source": "dongguk_official",
            "category": "교육과정",
            "sub_category": "교과목해설",
            "title": "STA2019 통계수학및R실습",
            "content": "학수번호: STA2019 교과목명: 통계수학및R실습 해설: 통계학 계산을 위한 수학과 R 실습을 학습한다.",
            "url": "dongguk://course-description/STA2019",
            "published_at": None,
            "updated_at": None,
            "department": "통계학과",
            "campus": "서울",
            "document_type": "course",
            "has_attachment": False,
            "attachment_urls": [],
            "valid_from": None,
            "valid_until": None,
            "collected_at": "2026-05-08T00:00:00+00:00",
        },
    )

    result = rag_service._answer_course_followup("1번 교과목에서는 뭐 배워?", session_id)

    assert result is not None
    answer, sources = result
    assert "통계수학및R실습" in answer
    assert "통계학 계산을 위한 수학과 R 실습" in answer
    assert "컴퓨터공학" not in answer
    assert sources[0]["metadata"]["source_url"] == "dongguk://course-description/STA2019"


def test_internal_course_source_urls_are_not_exposed_as_public_links():
    metadata = {
        "title": "STA2019 통계수학및R실습",
        "url": "dongguk://major-course/STA2019",
        "source_url": "dongguk://major-course/STA2019",
    }

    assert rag_service._public_source_url(metadata) == ""
    sanitized = rag_service._metadata_for_response(metadata)
    assert "url" not in sanitized
    assert "source_url" not in sanitized

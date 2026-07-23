from src.utils.query_expansion import expand_query


def test_legacy_merit_scholarship_name_expands_to_current_rule_terms():
    expanded = expand_query("성적우수장학금은 직전 학기 평점이 몇 이상이어야 해?")

    assert "우수장학금" in expanded
    assert "동국인재육성장학" in expanded
    assert "평균평점" in expanded
    assert "직전 학기 평점" in expanded


def test_unrelated_student_query_is_unchanged():
    question = "일반휴학은 최대 몇 학기까지 할 수 있어?"

    assert expand_query(question) == question

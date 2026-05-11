from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.utils.date_parser import extract_date_range_from_query


def test_parse_today_yesterday_tomorrow():
    today = datetime.now(timezone(timedelta(hours=9))).date()

    assert extract_date_range_from_query("오늘 올라온 공지 있어?") == (today, today)
    assert extract_date_range_from_query("어제 공지 알려줘") == (today - timedelta(days=1), today - timedelta(days=1))
    assert extract_date_range_from_query("내일 학사일정 알려줘") == (today + timedelta(days=1), today + timedelta(days=1))


def test_parse_week_ranges():
    today = datetime.now(timezone(timedelta(hours=9))).date()
    start_of_week = today - timedelta(days=today.weekday())

    assert extract_date_range_from_query("이번 주 공지 알려줘") == (start_of_week, start_of_week + timedelta(days=6))
    assert extract_date_range_from_query("지난주 학사일정 알려줘") == (
        start_of_week - timedelta(days=7),
        start_of_week - timedelta(days=1),
    )
    assert extract_date_range_from_query("다음 주 행사 알려줘") == (
        start_of_week + timedelta(days=7),
        start_of_week + timedelta(days=13),
    )


def test_parse_specific_day_before_month_range():
    assert extract_date_range_from_query("2026년 5월 8일 공지 알려줘") == (
        datetime(2026, 5, 8).date(),
        datetime(2026, 5, 8).date(),
    )
    assert extract_date_range_from_query("2026-05-08 공지 알려줘") == (
        datetime(2026, 5, 8).date(),
        datetime(2026, 5, 8).date(),
    )


def test_parse_month_range():
    assert extract_date_range_from_query("2026년 5월 공지 알려줘") == (
        datetime(2026, 5, 1).date(),
        datetime(2026, 5, 31).date(),
    )

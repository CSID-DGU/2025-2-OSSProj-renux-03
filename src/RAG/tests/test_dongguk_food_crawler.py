from __future__ import annotations

from src.ingestion.crawlers.dongguk_food import DguCoopFoodCrawler, DonggukFoodCrawler, DonggukOfficialFoodCrawler


def test_food_crawler_parse_preserves_detail_urls_and_date_range():
    crawler = DonggukFoodCrawler(
        {
            "source": "dongguk_official",
            "base_url": "https://dorm.dongguk.edu",
            "category": "식단표",
            "sub_category": "남산학사",
            "department": "남산학사",
            "campus": "서울",
            "document_type": "food",
            "request_delay_seconds": 0,
            "enable_ocr": False,
        }
    )
    item = {
        "title": "동국대학교 남산학사 기숙사식당 주간 식단표 5월 2주차(26. 05. 11~26. 05. 15)",
        "url": "https://dorm.dongguk.edu/article/food/detail/209989?pageIndex=1&",
        "published_at": "2026-05-07",
    }
    detail = {
        "title": item["title"],
        "content_text": "",
        "attachment_texts": [],
        "attachment_urls": ["https://dorm.dongguk.edu/cmmn/fileDown.do?filename=menu.pdf"],
        "image_urls": ["https://dorm.dongguk.edu/cmmn/fileView?path=/ckeditor//food&physical=menu.png"],
        "image_texts": [],
        "url": item["url"],
    }

    parsed = crawler.parse(item, detail)

    assert parsed is not None
    assert parsed["document_type"] == "food"
    assert parsed["url"].endswith("/article/food/detail/209989?pageIndex=1&")
    assert parsed["valid_from"] == "2026-05-11"
    assert parsed["valid_until"] == "2026-05-15"
    assert "fileDown.do?filename=menu.pdf" in parsed["content"]


def test_official_food_crawler_parses_dflex_date_range():
    crawler = DonggukOfficialFoodCrawler(
        {
            "source": "dongguk_official",
            "base_url": "https://www.dongguk.edu",
            "category": "식단표",
            "sub_category": "D-Flex",
            "department": "동국대학교",
            "campus": "서울",
            "document_type": "food",
            "request_delay_seconds": 0,
            "enable_ocr": False,
        }
    )
    item = {
        "title": "동국대학교 경영관 D-Flex 식당 주간식단표[2026. 05. 11. ~ 2026. 05. 15. ]",
        "url": "https://www.dongguk.edu/article/FOODDFLEX/detail/26764651",
        "published_at": "2026-05-07",
    }
    detail = {
        "title": item["title"],
        "content_text": "순살안동찜닭 쌀밥 배추김치",
        "attachment_texts": [],
        "attachment_urls": [],
        "image_urls": [],
        "image_texts": [],
        "url": item["url"],
    }

    parsed = crawler.parse(item, detail)

    assert parsed is not None
    assert parsed["sub_category"] == "D-Flex"
    assert parsed["valid_from"] == "2026-05-11"
    assert parsed["valid_until"] == "2026-05-15"


def test_coop_food_crawler_preserves_weekly_menu_text():
    crawler = DguCoopFoodCrawler(
        {
            "source": "dongguk_official",
            "base_url": "https://dgucoop.dongguk.edu",
            "category": "식단표",
            "sub_category": "상록원",
            "department": "동국대학교 소비자생활협동조합",
            "campus": "서울",
            "document_type": "food",
            "request_delay_seconds": 0,
        }
    )
    item = {"title": "상록원 주간식단표", "url": "https://dgucoop.dongguk.edu/store/store.php?w=4&l=2"}
    detail = {
        "title": "상록원 주간식단표 2026-05-03~2026-05-09",
        "content_text": "2026.05.03 ~ 2026.05.09 상록원3층식당 중식 돈목살김치찌개",
        "url": item["url"],
        "valid_from": "2026-05-03",
        "valid_until": "2026-05-09",
    }

    parsed = crawler.parse(item, detail)

    assert parsed is not None
    assert parsed["sub_category"] == "상록원"
    assert parsed["valid_from"] == "2026-05-03"
    assert "돈목살김치찌개" in parsed["content"]

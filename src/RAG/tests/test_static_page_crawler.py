from urllib.robotparser import RobotFileParser

from src.ingestion.crawlers import static_page
from src.ingestion.crawlers.static_page import StaticPageCrawler


class FakeResponse:
    def __init__(self, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None


def test_static_page_crawler_extracts_pdf_attachment_text(monkeypatch):
    monkeypatch.setattr(RobotFileParser, "read", lambda self: None)
    monkeypatch.setattr(RobotFileParser, "can_fetch", lambda self, user_agent, url: True)
    monkeypatch.setattr(static_page, "extract_pdf_text", lambda *args, **kwargs: "PDF 본문")

    settings = {
        "source": "dongguk_official",
        "base_url": "https://www.dongguk.edu",
        "category": "학사제도",
        "sub_category": "교육과정",
        "department": "학사지원팀",
        "campus": "서울",
        "document_type": "academic",
        "parse_pdf_attachments": True,
        "request_delay_seconds": 0,
    }
    crawler = StaticPageCrawler(settings)

    html = """
    <html>
      <body>
        <main>
          <h1>교육과정</h1>
          <p>학사제도 안내 본문</p>
          <a href="/files/guide.pdf">학업이수 가이드 PDF 다운로드</a>
        </main>
      </body>
    </html>
    """

    def fake_get(url: str, **kwargs):
        if url.endswith(".pdf"):
            return FakeResponse(content=b"%PDF")
        return FakeResponse(text=html)

    monkeypatch.setattr(crawler, "_get", fake_get)

    item = {"title": "교육과정", "url": "https://www.dongguk.edu/page/137", "content_selector": "main"}
    detail = crawler.fetch_detail(item)
    parsed = crawler.parse(item, detail)

    assert parsed is not None
    assert "학사제도 안내 본문" in parsed["content"]
    assert "PDF 본문" in parsed["content"]
    assert parsed["attachment_urls"] == ["https://www.dongguk.edu/files/guide.pdf"]

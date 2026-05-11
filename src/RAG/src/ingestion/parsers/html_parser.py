from __future__ import annotations

from bs4 import BeautifulSoup

from src.utils.preprocess import normalize_whitespace


REMOVAL_SELECTORS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "header",
    "footer",
    "nav",
    ".breadcrumb",
    ".board_util",
    ".board_view_btn",
]


def extract_clean_text(html: str, content_selector: str | None = None) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for selector in REMOVAL_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    target = soup.select_one(content_selector) if content_selector else soup
    if target is None:
        return ""

    for node in target.select("img, video, source"):
        node.decompose()

    text = target.get_text("\n", strip=True)
    return normalize_whitespace(text)

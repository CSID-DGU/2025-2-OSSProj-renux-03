from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.ingestion.crawlers.base import BaseCrawler
from src.ingestion.parsers.html_parser import extract_clean_text
from src.ingestion.parsers.pdf_parser import extract_pdf_text, is_pdf_url


logger = logging.getLogger(__name__)


class StaticPageCrawler(BaseCrawler):
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        pages = list(self.settings.get("pages", []))
        if limit:
            pages = pages[:limit]
        return pages

    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        url = item["url"]
        response = self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        selector = item.get("content_selector") or self.settings.get("content_selector")
        title = item.get("title") or self._extract_title(soup)
        attachment_scope = soup.select_one(selector) if selector else soup
        attachment_urls = self._extract_attachment_urls(attachment_scope or soup, url, item)
        content_parts = [extract_clean_text(response.text, selector)]

        if item.get("parse_pdf_attachments", self.settings.get("parse_pdf_attachments", False)):
            content_parts.extend(self._extract_pdf_attachment_texts(attachment_urls, item))

        return {
            "title": title,
            "content_text": "\n\n".join(part for part in content_parts if part),
            "url": url,
            "attachment_urls": attachment_urls,
        }

    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        content = (detail.get("content_text") or "").strip()
        if len(content) < int(item.get("min_content_length") or self.settings.get("min_content_length", 30)):
            logger.info("Skipping short static page: %s", detail.get("url"))
            return None

        return {
            "source": item.get("source") or self.settings["source"],
            "category": item.get("category") or self.settings["category"],
            "sub_category": item.get("sub_category") or self.settings.get("sub_category"),
            "title": detail.get("title") or item.get("title") or self._title_from_url(detail["url"]),
            "content": content,
            "url": detail["url"],
            "published_at": item.get("published_at"),
            "updated_at": item.get("updated_at"),
            "department": item.get("department") or self.settings.get("department"),
            "campus": item.get("campus") or self.settings.get("campus"),
            "document_type": item.get("document_type") or self.settings.get("document_type", "academic"),
            "attachment_urls": detail.get("attachment_urls") or item.get("attachment_urls", []),
            "valid_from": item.get("valid_from"),
            "valid_until": item.get("valid_until"),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "h2", ".page-title", ".tit p", "title"]:
            node = soup.select_one(selector)
            if node:
                title = node.get_text(" ", strip=True)
                if title:
                    return title
        return ""

    def _title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.path.strip("/") or parsed.netloc

    def _extract_attachment_urls(
        self,
        soup: BeautifulSoup,
        page_url: str,
        item: dict[str, Any],
    ) -> list[str]:
        configured = item.get("attachment_urls") or []
        urls: list[str] = [urljoin(page_url, str(url)) for url in configured if str(url).strip()]

        for anchor in soup.select("a[href]"):
            href = anchor.get("href") or ""
            text = anchor.get_text(" ", strip=True)
            absolute_url = urljoin(page_url, href)
            if is_pdf_url(absolute_url, text) or "fileDown.do" in absolute_url:
                urls.append(absolute_url)

        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    def _extract_pdf_attachment_texts(self, attachment_urls: list[str], item: dict[str, Any]) -> list[str]:
        max_attachments = int(
            item.get("max_pdf_attachments_per_page")
            or self.settings.get("max_pdf_attachments_per_page")
            or self.settings.get("max_pdf_attachments_per_notice", 2)
        )
        max_pages = int(item.get("max_pdf_pages_per_attachment") or self.settings.get("max_pdf_pages_per_attachment", 20))
        enable_ocr = bool(item.get("enable_ocr", self.settings.get("enable_ocr", False)))
        ocr_lang = str(item.get("ocr_lang", self.settings.get("ocr_lang", "kor+eng")))

        texts: list[str] = []
        for url in attachment_urls[:max_attachments]:
            if not is_pdf_url(url):
                continue
            try:
                response = self._get(url)
                text = extract_pdf_text(
                    response.content,
                    max_pages=max_pages,
                    enable_ocr=enable_ocr,
                    ocr_lang=ocr_lang,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse static page PDF attachment %s: %s", url, exc)
                continue
            if text:
                texts.append(f"[첨부 PDF 텍스트: {url}]\n{text}")
        return texts


__all__ = ["StaticPageCrawler"]

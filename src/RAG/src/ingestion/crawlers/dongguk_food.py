from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from src.ingestion.crawlers.base import BaseCrawler
from src.ingestion.parsers.html_parser import extract_clean_text
from src.ingestion.parsers.pdf_parser import extract_image_text_with_ocr, extract_pdf_text, is_pdf_url
from src.utils.preprocess import normalize_whitespace


logger = logging.getLogger(__name__)


class DonggukFoodCrawler(BaseCrawler):
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        list_url = self.settings.get("list_url") or self.settings.get("pages", [{}])[0].get("url")
        max_pages = int(self.settings.get("max_pages", 1))
        items: list[dict[str, Any]] = []

        for page_index in range(1, max_pages + 1):
            page_url = f"{list_url}?pageIndex={page_index}" if "?" not in list_url else list_url
            soup = BeautifulSoup(self._get(page_url).text, "lxml")
            for row in soup.select("tr"):
                anchor = row.select_one("td.td_tit a[href*='/article/food/detail/']")
                if not anchor:
                    continue
                title = self._inline_text(anchor.get_text(" ", strip=True))
                href = anchor.get("href") or ""
                published_at = self._extract_row_date(row)
                items.append(
                    {
                        "title": title,
                        "url": self.build_url(href),
                        "published_at": published_at,
                    }
                )
                if limit and len(items) >= limit:
                    return items
        return items

    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        url = item["url"]
        response = self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        title_node = soup.select_one(".board_view .tit p")
        title = self._inline_text(title_node.get_text(" ", strip=True)) if title_node else item.get("title", "")

        attachment_urls: list[str] = []
        attachment_texts: list[str] = []
        for anchor in soup.select(".view_files a[href]"):
            file_name = self._inline_text(anchor.get_text(" ", strip=True))
            file_url = self.build_url((anchor.get("href") or "").strip())
            attachment_urls.append(file_url)
            if is_pdf_url(file_url, file_name):
                pdf_text = self._extract_pdf_attachment_text(file_url)
                if pdf_text:
                    attachment_texts.append(f"[첨부 PDF: {file_name}]\n{pdf_text}")

        image_urls: list[str] = []
        image_texts: list[str] = []
        for image in soup.select(".view_cont img[src]"):
            image_url = self.build_url(image.get("src") or "")
            image_urls.append(image_url)
            image_text = self._extract_image_text(image_url)
            if image_text:
                image_texts.append(f"[본문 이미지 OCR]\n{image_text}")

        return {
            "title": title,
            "content_text": extract_clean_text(response.text, ".board_view .view_cont"),
            "attachment_texts": attachment_texts,
            "attachment_urls": attachment_urls,
            "image_urls": image_urls,
            "image_texts": image_texts,
            "url": url,
        }

    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        content_parts = [
            detail.get("title") or item.get("title") or "",
            self._date_range_text(detail.get("title") or item.get("title") or ""),
            detail.get("content_text") or "",
            *detail.get("attachment_texts", []),
            *detail.get("image_texts", []),
        ]
        attachment_urls = [*detail.get("attachment_urls", []), *detail.get("image_urls", [])]
        if attachment_urls:
            content_parts.append("원문 식단표 파일/이미지: " + " ".join(attachment_urls))

        content = self._content_text("\n".join(part for part in content_parts if part))
        if len(content) < int(self.settings.get("min_content_length", 30)):
            logger.info("Skipping short food document: %s", detail.get("url"))
            return None

        return {
            "source": self.settings["source"],
            "category": self.settings["category"],
            "sub_category": self.settings.get("sub_category"),
            "title": detail.get("title") or item.get("title"),
            "content": content,
            "url": detail["url"],
            "published_at": item.get("published_at"),
            "updated_at": item.get("published_at"),
            "department": self.settings.get("department"),
            "campus": self.settings.get("campus"),
            "document_type": self.settings.get("document_type", "food"),
            "attachment_urls": attachment_urls,
            "valid_from": self._date_range(detail.get("title") or item.get("title") or "")[0],
            "valid_until": self._date_range(detail.get("title") or item.get("title") or "")[1],
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_row_date(self, row) -> str | None:
        for cell in row.select("td"):
            text = cell.get_text(" ", strip=True)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                return text
        return None

    def _extract_pdf_attachment_text(self, url: str) -> str:
        try:
            response = self._get(url)
            return extract_pdf_text(
                response.content,
                max_pages=int(self.settings.get("pdf_max_pages", 3)),
                enable_ocr=bool(self.settings.get("enable_ocr", False)),
                ocr_lang=str(self.settings.get("ocr_lang", "kor+eng")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to extract food PDF text from %s: %s", url, exc)
            return ""

    def _extract_image_text(self, url: str) -> str:
        if not self.settings.get("enable_ocr", False):
            return ""
        try:
            response = self._get(url)
            return extract_image_text_with_ocr(response.content, lang=str(self.settings.get("ocr_lang", "kor+eng")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to OCR food image from %s: %s", url, exc)
            return ""

    def _date_range_text(self, title: str) -> str:
        start, end = self._date_range(title)
        if not start and not end:
            return ""
        return f"식단 기간: {start or ''} ~ {end or ''}"

    def _date_range(self, title: str) -> tuple[str | None, str | None]:
        compact_title = re.sub(r"\s+", "", title)
        match = re.search(r"\((\d{2})\.(\d{2})\.(\d{2})~(?:\d{2}\.)?(\d{2})\.(\d{2})\)", compact_title)
        if not match:
            return None, None
        year, start_month, start_day, end_month, end_day = match.groups()
        full_year = f"20{year}"
        return f"{full_year}-{start_month}-{start_day}", f"{full_year}-{end_month}-{end_day}"

    def _inline_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", normalize_whitespace(text)).strip()

    def _content_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()


__all__ = ["DonggukFoodCrawler"]


class DonggukOfficialFoodCrawler(BaseCrawler):
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        list_url = self.settings.get("list_url") or self.build_url("/article/FOODDFLEX/list")
        response = self._get(list_url)
        soup = BeautifulSoup(response.text, "lxml")
        items: list[dict[str, Any]] = []

        for node in soup.select(".board_list li a[onclick*='goDetail']"):
            onclick = node.get("onclick") or ""
            match = re.search(r"goDetail\((\d+)\)", onclick)
            if not match:
                continue
            title_node = node.select_one(".tit")
            date_node = node.select_one(".info span")
            title = self._inline_text(title_node.get_text(" ", strip=True) if title_node else "")
            published_at = self._normalize_dot_date(date_node.get_text(" ", strip=True) if date_node else "")
            items.append(
                {
                    "title": title,
                    "url": self.build_url(f"/article/FOODDFLEX/detail/{match.group(1)}"),
                    "published_at": published_at,
                }
            )
            if limit and len(items) >= limit:
                return items
        return items

    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        response = self._get(item["url"])
        soup = BeautifulSoup(response.text, "lxml")
        title_node = soup.select_one(".board_view .tit p")
        title = self._inline_text(title_node.get_text(" ", strip=True)) if title_node else item.get("title", "")
        content_text = extract_clean_text(response.text, ".board_view .view_cont")

        attachment_urls: list[str] = []
        attachment_texts: list[str] = []
        for anchor in soup.select(".view_files a"):
            file_name = self._inline_text(anchor.get_text(" ", strip=True))
            file_url = self._official_file_url(anchor.get("href") or "")
            if not file_url:
                continue
            attachment_urls.append(file_url)
            if is_pdf_url(file_url, file_name):
                pdf_text = self._extract_pdf_attachment_text(file_url)
                if pdf_text:
                    attachment_texts.append(f"[첨부 PDF: {file_name}]\n{pdf_text}")

        image_urls: list[str] = []
        image_texts: list[str] = []
        for image in soup.select(".view_cont img[src]"):
            image_url = self.build_url(image.get("src") or "")
            image_urls.append(image_url)
            image_text = self._extract_image_text(image_url)
            if image_text:
                image_texts.append(f"[본문 이미지 OCR]\n{image_text}")

        return {
            "title": title,
            "content_text": content_text,
            "attachment_texts": attachment_texts,
            "attachment_urls": attachment_urls,
            "image_urls": image_urls,
            "image_texts": image_texts,
            "url": item["url"],
        }

    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        content_parts = [
            detail.get("title") or item.get("title") or "",
            self._date_range_text(detail.get("title") or item.get("title") or ""),
            detail.get("content_text") or "",
            *detail.get("attachment_texts", []),
            *detail.get("image_texts", []),
        ]
        attachment_urls = [*detail.get("attachment_urls", []), *detail.get("image_urls", [])]
        if attachment_urls:
            content_parts.append("원문 식단표 파일/이미지: " + " ".join(attachment_urls))

        content = self._content_text("\n".join(part for part in content_parts if part))
        if len(content) < int(self.settings.get("min_content_length", 30)):
            logger.info("Skipping short D-Flex food document: %s", detail.get("url"))
            return None

        valid_from, valid_until = self._date_range(detail.get("title") or item.get("title") or "")
        return {
            "source": self.settings["source"],
            "category": self.settings["category"],
            "sub_category": self.settings.get("sub_category"),
            "title": detail.get("title") or item.get("title"),
            "content": content,
            "url": detail["url"],
            "published_at": item.get("published_at"),
            "updated_at": item.get("published_at"),
            "department": self.settings.get("department"),
            "campus": self.settings.get("campus"),
            "document_type": self.settings.get("document_type", "food"),
            "attachment_urls": attachment_urls,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_pdf_attachment_text(self, url: str) -> str:
        try:
            response = self._get(url)
            return extract_pdf_text(
                response.content,
                max_pages=int(self.settings.get("pdf_max_pages", 3)),
                enable_ocr=bool(self.settings.get("enable_ocr", False)),
                ocr_lang=str(self.settings.get("ocr_lang", "kor+eng")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to extract D-Flex PDF text from %s: %s", url, exc)
            return ""

    def _extract_image_text(self, url: str) -> str:
        if not self.settings.get("enable_ocr", False):
            return ""
        try:
            response = self._get(url)
            return extract_image_text_with_ocr(response.content, lang=str(self.settings.get("ocr_lang", "kor+eng")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to OCR D-Flex image from %s: %s", url, exc)
            return ""

    def _official_file_url(self, href: str) -> str:
        match = re.search(r"downGO\('([^']*)','([^']*)','([^']*)'\)", href)
        if not match:
            return ""
        filename, filepath, filerealname = match.groups()
        return self.build_url(
            "/cmmn/fileDown.do?"
            f"filename={quote(filename)}&filepath={quote(filepath, safe='/')}&filerealname={quote(filerealname)}"
        )

    def _normalize_dot_date(self, value: str) -> str | None:
        match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", value or "")
        if not match:
            return None
        return "-".join(match.groups())

    def _date_range_text(self, title: str) -> str:
        start, end = self._date_range(title)
        if not start and not end:
            return ""
        return f"식단 기간: {start or ''} ~ {end or ''}"

    def _date_range(self, title: str) -> tuple[str | None, str | None]:
        match = re.search(r"(\d{4})\.\s*(\d{2})\.\s*(\d{2})\.\s*~\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})", title)
        if not match:
            return None, None
        sy, sm, sd, ey, em, ed = match.groups()
        return f"{sy}-{sm}-{sd}", f"{ey}-{em}-{ed}"

    def _inline_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", normalize_whitespace(text)).strip()

    def _content_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()


class DguCoopFoodCrawler(BaseCrawler):
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        page_url = self.settings.get("page_url") or self.settings.get("list_url")
        return [{"title": self.settings.get("title", "상록원 주간식단"), "url": page_url}]

    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        response = self._get(item["url"])
        text = response.content.decode(str(self.settings.get("encoding", "euc-kr")), errors="replace")
        soup = BeautifulSoup(text, "lxml")
        content_node = soup.select_one(self.settings.get("content_selector", "#sdetail"))
        content = self._content_text(content_node.get_text(" ", strip=True) if content_node else "")
        date_match = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*~\s*(\d{4}\.\d{2}\.\d{2})", content)
        valid_from = self._normalize_dot_date(date_match.group(1)) if date_match else None
        valid_until = self._normalize_dot_date(date_match.group(2)) if date_match else None
        return {
            "title": f"상록원 주간식단표 {valid_from or ''}~{valid_until or ''}".strip(),
            "content_text": content,
            "url": item["url"],
            "valid_from": valid_from,
            "valid_until": valid_until,
        }

    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        content = detail.get("content_text") or ""
        if len(content) < int(self.settings.get("min_content_length", 30)):
            logger.info("Skipping short coop food document: %s", detail.get("url"))
            return None
        return {
            "source": self.settings["source"],
            "category": self.settings["category"],
            "sub_category": self.settings.get("sub_category"),
            "title": detail.get("title") or item.get("title"),
            "content": content,
            "url": detail["url"],
            "published_at": detail.get("valid_from"),
            "updated_at": detail.get("valid_from"),
            "department": self.settings.get("department"),
            "campus": self.settings.get("campus"),
            "document_type": self.settings.get("document_type", "food"),
            "attachment_urls": [],
            "valid_from": detail.get("valid_from"),
            "valid_until": detail.get("valid_until"),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _normalize_dot_date(self, value: str) -> str | None:
        match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", value or "")
        if not match:
            return None
        return "-".join(match.groups())

    def _content_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()


__all__ = ["DonggukFoodCrawler", "DonggukOfficialFoodCrawler", "DguCoopFoodCrawler"]

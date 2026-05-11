from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from src.ingestion.crawlers.base import BaseCrawler
from src.ingestion.parsers.html_parser import extract_clean_text
from src.ingestion.parsers.pdf_parser import extract_pdf_text, is_pdf_url


logger = logging.getLogger(__name__)


class DonggukNoticeCrawler(BaseCrawler):
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        board_code = self.settings["board_code"]
        list_path = self.settings["list_path"].format(board_code=board_code)
        max_pages = self._max_pages()
        crawl_until_date = self._parse_cutoff_date(self.settings.get("crawl_until_date"))
        list_selector = self.settings["list_item_selector"]

        items: list[dict[str, Any]] = []
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                break

            response = self._get(self.build_url(list_path), params={"pageIndex": page})
            soup = BeautifulSoup(response.text, "lxml")
            rows = soup.select(list_selector)
            if not rows:
                break

            should_stop = False
            valid_row_count = 0
            for row in rows:
                anchor = row.find("a")
                if anchor is None:
                    continue

                onclick = anchor.get("onclick", "")
                match = re.search(r"goDetail\((\d+)\)", onclick)
                if match is None:
                    continue

                valid_row_count += 1
                article_id = match.group(1)
                detail_path = self.settings["detail_path"].format(board_code=board_code, article_id=article_id)
                detail_url = self.build_url(detail_path)
                title = self._select_text(anchor, "p.tit")
                category = self._select_text(anchor, "div.top > em") or self.settings.get("sub_category")
                posted_at = self._parse_list_date(anchor)
                is_pinned = anchor.select_one("div.mark span.fix") is not None

                if crawl_until_date and posted_at:
                    posted_date = datetime.strptime(posted_at, "%Y-%m-%d").date()
                    if posted_date < crawl_until_date:
                        should_stop = True
                        continue

                if detail_url in self.skip_urls:
                    if not is_pinned:
                        should_stop = True
                        break
                    continue

                items.append(
                    {
                        "article_id": article_id,
                        "url": detail_url,
                        "title": title,
                        "category": category,
                        "posted_at": posted_at,
                        "is_pinned": is_pinned,
                    }
                )
                if limit and len(items) >= limit:
                    return items

            if should_stop:
                break
            if valid_row_count == 0:
                logger.info(
                    "Stopping notice crawl for %s at page %s; no valid notice rows found.",
                    self.settings.get("source_name") or board_code,
                    page,
                )
                break
            page += 1

        return items

    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        board_code = self.settings["board_code"]
        detail_path = self.settings["detail_path"].format(board_code=board_code, article_id=item["article_id"])
        detail_url = self.build_url(detail_path)
        response = self._get(detail_url)
        soup = BeautifulSoup(response.text, "lxml")

        content_node = soup.select_one(self.settings["content_selector"])
        content_html = str(content_node) if content_node else ""
        content_text = extract_clean_text(response.text, self.settings["content_selector"])
        title = self._select_text(soup, self.settings["title_selector"]) or item.get("title")
        info_spans = [span.get_text(" ", strip=True) for span in soup.select(self.settings["info_selector"])]
        posted_at = self._parse_detail_date(info_spans) or item.get("posted_at")

        attachments = []
        for link in soup.select(self.settings["attachment_selector"]):
            href = link.get("href", "")
            match = re.search(r"downGO\('(.+?)','(.+?)','(.+?)'\)", href)
            if not match:
                continue
            name, path, stored = match.groups()
            download_url = self.build_url(
                f"/cmmn/fileDown.do?filename={quote(name)}&filepath={quote(path, safe='/')}&filerealname={quote(stored)}"
            )
            attachments.append({"name": name, "url": download_url})

        return {
            "title": title,
            "posted_at": posted_at,
            "content_html": content_html,
            "content_text": content_text,
            "attachments": attachments,
            "detail_url": detail_url,
        }

    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        content = (detail.get("content_text") or "").strip()
        pdf_text = self._extract_attachment_text(detail.get("attachments", []))
        if pdf_text:
            content = "\n\n[첨부 PDF 텍스트]\n" + pdf_text if not content else f"{content}\n\n[첨부 PDF 텍스트]\n{pdf_text}"
        if len(content) < 20:
            logger.info("Skipping short notice document: %s", detail.get("detail_url"))
            return None

        return {
            "source": self.settings["source"],
            "category": self.settings["category"],
            "sub_category": item.get("category") or self.settings.get("sub_category"),
            "title": detail.get("title") or item.get("title") or "",
            "content": content,
            "url": detail["detail_url"],
            "published_at": detail.get("posted_at"),
            "updated_at": detail.get("posted_at"),
            "department": self.settings.get("department"),
            "campus": self.settings.get("campus"),
            "document_type": self.settings.get("document_type", "notice"),
            "attachment_urls": [entry["url"] for entry in detail.get("attachments", []) if entry.get("url")],
        }

    def _select_text(self, root, selector: str) -> str:
        node = root.select_one(selector)
        return node.get_text(" ", strip=True) if node else ""

    def _parse_list_date(self, anchor) -> str | None:
        info_spans = anchor.select("div.info span")
        if not info_spans:
            return None
        raw = info_spans[0].get_text(strip=True).rstrip(".")
        try:
            return datetime.strptime(raw, "%Y.%m.%d").strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _parse_detail_date(self, info_spans: list[str]) -> str | None:
        for text in info_spans:
            if text.startswith("등록일"):
                raw = text.replace("등록일", "").strip().rstrip(".")
                try:
                    return datetime.strptime(raw, "%Y.%m.%d").strftime("%Y-%m-%d")
                except ValueError:
                    return None
        return None

    def _max_pages(self) -> int | None:
        value = self.settings.get("max_pages")
        if value in (None, "", "null"):
            return None
        return int(value)

    def _parse_cutoff_date(self, value: Any):
        if not value:
            return None
        if hasattr(value, "date"):
            return value.date()
        return datetime.strptime(str(value), "%Y-%m-%d").date()

    def _extract_attachment_text(self, attachments: list[dict[str, Any]]) -> str:
        if not self.settings.get("parse_pdf_attachments", False):
            return ""

        max_files = int(self.settings.get("max_pdf_attachments_per_notice", 2))
        max_pages = int(self.settings.get("max_pdf_pages_per_attachment", 20))
        texts: list[str] = []
        parsed_count = 0

        for attachment in attachments:
            if parsed_count >= max_files:
                break
            url = attachment.get("url") or ""
            name = attachment.get("name") or ""
            if not url or not is_pdf_url(url, name):
                continue

            try:
                response = self._get(url)
                text = extract_pdf_text(response.content, max_pages=max_pages)
            except Exception as exc:  # noqa: BLE001
                logger.warning("PDF attachment parsing failed: %s (%s)", url, exc)
                continue

            if text:
                texts.append(f"{name}\n{text}" if name else text)
                parsed_count += 1

        return "\n\n".join(texts)

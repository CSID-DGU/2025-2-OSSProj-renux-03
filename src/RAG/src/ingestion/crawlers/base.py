from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests


logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.skip_urls: set[str] = set(settings.get("skip_urls") or [])
        self.base_url = settings["base_url"].rstrip("/")
        self.delay = float(settings.get("request_delay_seconds", 0.7))
        self.timeout = int(settings.get("timeout_seconds", 30))
        self.verify_ssl = bool(settings.get("verify_ssl", True))
        self.session = requests.Session()
        self.robots = RobotFileParser()
        self.robots.set_url(self.build_url("/robots.txt"))
        try:
            self.robots.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read robots.txt for %s: %s", self.base_url, exc)
            self.robots = None
        self.session.headers.update(
            {
                "User-Agent": settings.get("user_agent", "DongttokRagBot/1.0"),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            }
        )

    def _get(self, url: str, **kwargs):
        user_agent = self.session.headers.get("User-Agent", "*")
        if self.robots is not None and not self.robots.can_fetch(user_agent, url):
            raise PermissionError(f"Blocked by robots.txt: {url}")
        response = self.session.get(url, timeout=self.timeout, verify=self.verify_ssl, **kwargs)
        response.raise_for_status()
        time.sleep(self.delay)
        return response

    def build_url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    @abstractmethod
    def fetch_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def parse(self, item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    def run(self, limit: int | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in self.fetch_list(limit=limit):
            try:
                item_url = item.get("url") or item.get("detail_url")
                if item_url and item_url in self.skip_urls:
                    continue
                detail = self.fetch_detail(item)
                detail_url = detail.get("url") or detail.get("detail_url")
                if detail_url and detail_url in self.skip_urls:
                    continue
                parsed = self.parse(item, detail)
                if parsed:
                    records.append(parsed)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to process item from %s: %s", self.settings.get("source_name"), exc)
        return records

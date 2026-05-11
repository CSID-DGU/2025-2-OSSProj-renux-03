from __future__ import annotations

import json
from pathlib import Path

from src.schemas.document import DocumentSchema


class Deduplicator:
    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self.by_url: set[str] = set()
        self.by_signature: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        with self.index_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("url"):
                    self.by_url.add(item["url"])
                self.by_signature.add(self._signature(item))

    def _signature(self, doc: dict | DocumentSchema) -> str:
        if isinstance(doc, DocumentSchema):
            return f"{doc.title}|{doc.published_at}|{doc.category}|{doc.sub_category}|{doc.document_type}"
        return (
            f"{doc.get('title')}|{doc.get('published_at')}|{doc.get('category')}|"
            f"{doc.get('sub_category')}|{doc.get('document_type')}"
        )

    def is_duplicate(self, doc: DocumentSchema) -> bool:
        return doc.url in self.by_url or self._signature(doc) in self.by_signature

    def is_duplicate_payload(self, payload: dict) -> bool:
        url = payload.get("url")
        if url and url in self.by_url:
            return True
        return self._signature(payload) in self.by_signature

    def remember(self, doc: DocumentSchema) -> None:
        self.by_url.add(doc.url)
        self.by_signature.add(self._signature(doc))

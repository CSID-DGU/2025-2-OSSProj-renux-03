from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import LOG_DIR, PROCESSED_DATA_DIR, RAW_DATA_DIR
from src.ingestion.config_loader import get_source_settings, list_enabled_sources
from src.ingestion.crawlers.dongguk_food import DguCoopFoodCrawler, DonggukFoodCrawler, DonggukOfficialFoodCrawler
from src.ingestion.crawlers.dongguk_notice import DonggukNoticeCrawler
from src.ingestion.crawlers.static_page import StaticPageCrawler
from src.ingestion.dedup import Deduplicator
from src.ingestion.legacy_csv_source import load_legacy_csv_documents
from src.ingestion.normalizers.document_normalizer import build_document
from src.rag.chunker import TextChunker
from src.rag.embedder import SentenceTransformerEmbedder
from src.rag.vector_store import ChromaVectorStore
from src.schemas.document import DocumentSchema


logger = logging.getLogger(__name__)

CRAWLER_REGISTRY = {
    "dgucoop_food": DguCoopFoodCrawler,
    "dongguk_food": DonggukFoodCrawler,
    "dongguk_official_food": DonggukOfficialFoodCrawler,
    "dongguk_notice": DonggukNoticeCrawler,
    "static_page": StaticPageCrawler,
}


class IngestionPipeline:
    def __init__(self) -> None:
        self.raw_index_path = PROCESSED_DATA_DIR / "document_index.jsonl"
        self.log_path = LOG_DIR / "ingest_status.jsonl"
        self.chunker = TextChunker()
        self.embedder = SentenceTransformerEmbedder()
        self.vector_store = ChromaVectorStore()

    def run(
        self,
        source_name: str | None = None,
        limit: int | None = None,
        force: bool = False,
        clean: bool = False,
    ) -> list[dict[str, Any]]:
        if clean and source_name:
            raise ValueError("clean rebuild must run with source_name=None because it resets the shared Chroma collection.")
        targets = [source_name] if source_name else list_enabled_sources()
        if clean:
            self.clean(targets)
        results: list[dict[str, Any]] = []
        deduplicator = Deduplicator(self.raw_index_path)

        for target in targets:
            started_at = self._utcnow()
            result = {
                "source_name": target,
                "status": "success",
                "fetched": 0,
                "stored": 0,
                "skipped": 0,
                "failed": 0,
                "started_at": started_at,
                "finished_at": None,
                "errors": [],
            }
            try:
                settings = get_source_settings(target)
                raw_items: list[dict[str, Any]] = []

                if settings.get("use_legacy_csv"):
                    raw_items.extend(load_legacy_csv_documents(settings, limit=limit))

                crawler_name = settings.get("crawler")
                if crawler_name:
                    try:
                        crawler_cls = CRAWLER_REGISTRY[crawler_name]
                        settings["skip_urls"] = list(deduplicator.by_url) if not force else []
                        crawler = crawler_cls(settings)
                        raw_items.extend(crawler.run(limit=limit))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Incremental crawl failed for %s", target)
                        result["errors"].append(f"incremental crawl failed: {exc}")

                result["fetched"] = len(raw_items)
                raw_items = self._deduplicate_batch(raw_items)

                documents: list[DocumentSchema] = []
                for item in raw_items:
                    try:
                        document = build_document(item)
                        if not force and deduplicator.is_duplicate(document):
                            result["skipped"] += 1
                            continue
                        documents.append(document)
                        deduplicator.remember(document)
                    except Exception as exc:  # noqa: BLE001
                        result["failed"] += 1
                        result["errors"].append(str(exc))

                min_content_length = int(settings.get("min_content_length", 20))
                before_length_filter = len(documents)
                documents = [doc for doc in documents if len(doc.content) >= min_content_length]
                result["skipped"] += before_length_filter - len(documents)

                self._append_jsonl(RAW_DATA_DIR / f"{target}.jsonl", [doc.model_dump() for doc in documents])
                self._append_jsonl(self.raw_index_path, [doc.model_dump() for doc in documents])

                chunk_payloads = []
                for document in documents:
                    chunks = self.chunker.chunk_document(document)
                    for chunk in chunks:
                        chunk_payloads.append(
                            {
                                "id": chunk.id,
                                "content": chunk.content,
                                "metadata": chunk.metadata.model_dump(),
                            }
                        )

                if chunk_payloads:
                    embeddings = self.embedder.embed_texts(chunk["content"] for chunk in chunk_payloads)
                    self.vector_store.upsert_chunks(
                        collection_name="dongguk_documents",
                        chunks=chunk_payloads,
                        embeddings=embeddings,
                    )
                    self._append_jsonl(PROCESSED_DATA_DIR / f"{target}_chunks.jsonl", chunk_payloads)

                result["stored"] = len(documents)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ingestion failed for %s", target)
                result["status"] = "failed"
                result["failed"] += 1
                result["errors"].append(str(exc))
            finally:
                result["finished_at"] = self._utcnow()
                self._append_jsonl(self.log_path, [result])
                results.append(result)

        return results

    def clean(self, source_names: list[str] | None = None) -> None:
        targets = source_names or list_enabled_sources()
        paths: list[Path] = [self.raw_index_path]
        for target in targets:
            paths.extend(
                [
                    RAW_DATA_DIR / f"{target}.jsonl",
                    PROCESSED_DATA_DIR / f"{target}_chunks.jsonl",
                ]
            )

        for path in paths:
            if path.exists():
                path.unlink()

        self.vector_store.reset_collection("dongguk_documents")

    def latest_status(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        with self.log_path.open("r", encoding="utf-8") as file:
            rows = [json.loads(line) for line in file if line.strip()]
        return rows[-limit:]

    def _append_jsonl(self, path: Path, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            for item in items:
                file.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _deduplicate_batch(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        seen_signatures: set[str] = set()

        for item in items:
            url = item.get("url") or ""
            signature = (
                f"{item.get('title')}|{item.get('published_at')}|{item.get('category')}|"
                f"{item.get('sub_category')}|{item.get('document_type')}"
            )
            if url and url in seen_urls:
                continue
            if signature in seen_signatures:
                continue
            if url:
                seen_urls.add(url)
            seen_signatures.add(signature)
            deduped.append(item)

        return deduped

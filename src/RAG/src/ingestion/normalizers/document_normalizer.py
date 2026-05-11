from __future__ import annotations

from datetime import datetime, timezone

from src.schemas.document import DocumentSchema
from src.utils.preprocess import make_doc_id, standardize_date


def build_document(payload: dict) -> DocumentSchema:
    published_at = standardize_date(payload.get("published_at"))
    updated_at = standardize_date(payload.get("updated_at"))
    stable_id = payload.get("id") or make_doc_id(
        payload.get("url"),
        payload.get("title"),
        published_at,
        payload.get("category"),
    )
    return DocumentSchema(
        id=stable_id,
        source=payload["source"],
        category=payload["category"],
        sub_category=payload.get("sub_category"),
        title=payload["title"].strip(),
        content=payload["content"].strip(),
        url=payload["url"],
        published_at=published_at,
        updated_at=updated_at,
        department=payload.get("department"),
        campus=payload.get("campus"),
        document_type=payload["document_type"],
        has_attachment=bool(payload.get("attachment_urls")),
        attachment_urls=list(payload.get("attachment_urls", [])),
        valid_from=payload.get("valid_from"),
        valid_until=payload.get("valid_until"),
        collected_at=payload.get("collected_at") or datetime.now(timezone.utc).isoformat(),
    )

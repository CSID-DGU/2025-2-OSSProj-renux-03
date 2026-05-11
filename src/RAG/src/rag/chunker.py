from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.schemas.document import ChunkMetadata, ChunkRecord, DocumentSchema
from src.utils.preprocess import make_chunk_id


class TextChunker:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 120) -> None:
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", ".\n", "! ", "? ", " "],
            length_function=len,
        )

    def chunk_document(self, document: DocumentSchema) -> list[ChunkRecord]:
        segments = self.splitter.split_text(document.content)
        chunks: list[ChunkRecord] = []
        prefix = self._metadata_prefix(document)
        for idx, segment in enumerate(segments):
            content = f"{prefix}\n\n{segment.strip()}" if prefix else segment.strip()
            chunks.append(
                ChunkRecord(
                    id=make_chunk_id(document.id, idx),
                    content=content,
                    metadata=ChunkMetadata(
                        document_id=document.id,
                        source=document.source,
                        category=document.category,
                        sub_category=document.sub_category,
                        title=document.title,
                        url=document.url,
                        source_url=document.url,
                        published_at=document.published_at,
                        department=document.department,
                        campus=document.campus,
                        document_type=document.document_type,
                        chunk_index=idx,
                    ),
                )
            )
        return chunks

    def _metadata_prefix(self, document: DocumentSchema) -> str:
        parts = [
            f"제목: {document.title}",
            f"카테고리: {document.category}",
        ]
        if document.sub_category:
            parts.append(f"세부카테고리: {document.sub_category}")
        if document.department:
            parts.append(f"부서: {document.department}")
        if document.published_at:
            parts.append(f"게시일: {document.published_at}")
        return "\n".join(parts)

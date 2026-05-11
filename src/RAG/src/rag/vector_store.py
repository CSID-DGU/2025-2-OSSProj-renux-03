from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from src.vectorstore.chroma_client import get_collection


class BaseVectorStore(ABC):
    @abstractmethod
    def upsert_chunks(self, collection_name: str, chunks: Iterable[dict], embeddings) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, collection_name: str, query_embedding, top_k: int, where: dict | None = None) -> dict:
        raise NotImplementedError

    @abstractmethod
    def reset_collection(self, collection_name: str) -> None:
        raise NotImplementedError


class ChromaVectorStore(BaseVectorStore):
    def upsert_chunks(self, collection_name: str, chunks: Iterable[dict], embeddings) -> None:
        collection = get_collection(collection_name)
        chunk_list = list(chunks)
        metadatas = []
        for chunk in chunk_list:
            metadata = {}
            for key, value in chunk["metadata"].items():
                metadata[key] = "" if value is None else value
            metadatas.append(metadata)
        collection.upsert(
            ids=[chunk["id"] for chunk in chunk_list],
            documents=[chunk["content"] for chunk in chunk_list],
            metadatas=metadatas,
            embeddings=list(embeddings),
        )

    def search(self, collection_name: str, query_embedding, top_k: int, where: dict | None = None) -> dict:
        collection = get_collection(collection_name)
        return collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

    def reset_collection(self, collection_name: str) -> None:
        from src.vectorstore.chroma_client import reset_collection

        reset_collection(collection_name)

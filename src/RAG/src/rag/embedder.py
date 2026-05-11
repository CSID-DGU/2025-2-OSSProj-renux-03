from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.preprocessing import normalize

from src.models.embedding import encode_texts


class BaseEmbedder(ABC):
    @abstractmethod
    def embed_texts(self, texts: Iterable[str]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, query: str) -> np.ndarray:
        raise NotImplementedError


class SentenceTransformerEmbedder(BaseEmbedder):
    def __init__(self) -> None:
        self._fallback_vectorizer = HashingVectorizer(
            n_features=1024,
            alternate_sign=False,
            norm=None,
        )

    def embed_texts(self, texts: Iterable[str]) -> np.ndarray:
        text_list = list(texts)
        try:
            return encode_texts(text_list)
        except Exception:
            matrix = self._fallback_vectorizer.transform(text_list)
            return normalize(matrix, norm="l2").toarray().astype(np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed_texts([query])

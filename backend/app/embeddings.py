from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from app.config import get_settings
from app.models import EMBEDDING_DIM

settings = get_settings()


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """确定性替身：仅用于开发与自动化测试，不用于真实问答质量验收。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(EMBEDDING_DIM)]
            norm = math.sqrt(sum(value * value for value in raw)) or 1.0
            vectors.append([value / norm for value in raw])
        return vectors


class HttpEmbeddingProvider:
    """调用本地或远程的嵌入服务；模型须与文档向量一致。"""

    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": texts},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        if settings.embedding_base_url:
            _provider = HttpEmbeddingProvider(
                settings.embedding_base_url, settings.embedding_model, settings.embedding_api_key
            )
        else:
            _provider = HashingEmbeddingProvider()
    return _provider

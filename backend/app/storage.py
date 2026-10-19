from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from app.config import get_settings

settings = get_settings()


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


def build_storage_key(team_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return f"teams/{team_id}/documents/{document_id}{suffix}"


class LocalObjectStore:
    """把原始文件写入本地目录；部署时目录须挂载到持久卷。"""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise ValueError("非法存储路径")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = LocalObjectStore(settings.storage_root)
    return _store

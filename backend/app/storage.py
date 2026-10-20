from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import boto3

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


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


class S3ObjectStore:
    """S3 兼容对象存储；按需初始化客户端，避免配置错误影响其他模块。"""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "garage",
    ) -> None:
        if not endpoint_url or not bucket or not access_key or not secret_key:
            raise ValueError("S3 对象存储配置不完整")
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        data: bytes = response["Body"].read()
        return data

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        if settings.storage_backend == "s3":
            _store = S3ObjectStore(
                endpoint_url=settings.s3_endpoint_url,
                bucket=settings.s3_bucket,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                region=settings.s3_region,
            )
        elif settings.storage_backend == "local":
            _store = LocalObjectStore(settings.storage_root)
        else:
            raise ValueError(f"不支持的对象存储类型：{settings.storage_backend}")
    return _store


def delete_objects_best_effort(keys: Iterable[str]) -> None:
    """数据库已提交后清理原始文件；单个对象失败只记录日志，不回滚业务删除。"""
    try:
        store = get_object_store()
    except Exception:
        logger.exception("对象存储初始化失败，未能清理原始文件")
        return
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            logger.exception("原始文件清理失败：%s", key)

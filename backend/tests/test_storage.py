from __future__ import annotations

import uuid

from app.storage import S3ObjectStore, build_storage_key


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _Body]:
        return {"Body": _Body(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def test_s3_store_uses_configured_bucket_and_round_trips() -> None:
    store = S3ObjectStore.__new__(S3ObjectStore)
    store.bucket = "kplatform"
    store.client = _FakeS3()  # type: ignore[assignment]
    key = build_storage_key(uuid.uuid4(), uuid.uuid4(), "资料.PDF")

    store.put(key, b"original")
    assert store.get(key) == b"original"
    assert store.client.objects == {("kplatform", key): b"original"}

    store.delete(key)
    assert store.client.objects == {}

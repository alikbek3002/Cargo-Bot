from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from cargo_bots.core.config import Settings


class StorageBackend(Protocol):
    async def save_bytes(self, key: str, payload: bytes) -> str:
        ...

    async def fetch_bytes(self, key: str) -> bytes:
        ...


class LocalStorage:
    def __init__(self, root_path: str) -> None:
        self.root_path = Path(root_path)

    async def save_bytes(self, key: str, payload: bytes) -> str:
        destination = self.root_path / key
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, payload)
        return key

    async def fetch_bytes(self, key: str) -> bytes:
        destination = self.root_path / key
        return await asyncio.to_thread(destination.read_bytes)


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.storage_bucket
        self.client_args = {
            "service_name": "s3",
            "region_name": settings.aws_region,
        }
        if settings.aws_access_key_id:
            self.client_args["aws_access_key_id"] = settings.aws_access_key_id
        if settings.aws_secret_access_key:
            self.client_args["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_s3_endpoint_url:
            self.client_args["endpoint_url"] = settings.aws_s3_endpoint_url

    async def save_bytes(self, key: str, payload: bytes) -> str:
        await asyncio.to_thread(self._put_object, key, payload)
        return key

    async def fetch_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_object, key)

    def _put_object(self, key: str, payload: bytes) -> None:
        import boto3

        client = boto3.client(**self.client_args)
        client.put_object(Bucket=self.bucket, Key=key, Body=payload)

    def _get_object(self, key: str) -> bytes:
        import boto3

        client = boto3.client(**self.client_args)
        response = client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()


def build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.local_storage_path)


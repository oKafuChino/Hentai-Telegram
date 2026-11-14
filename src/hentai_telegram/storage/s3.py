import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3StorageError(Exception):
    pass


class S3StorageClient:
    """S3-compatible Object Storage Client for Cloudflare R2, Backblaze B2, MinIO, AWS S3."""

    def __init__(
        self,
        endpoint_url: str = "",
        bucket_name: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        public_url: str = "",
        region_name: str = "auto",
        key_prefix: str = "manga/",
    ):
        self.endpoint_url = endpoint_url.strip().rstrip("/")
        self.bucket_name = bucket_name.strip()
        self.access_key_id = access_key_id.strip()
        self.secret_access_key = secret_access_key.strip()
        self.public_url = public_url.strip().rstrip("/")
        self.region_name = region_name.strip() or "auto"
        self.key_prefix = key_prefix.strip().lstrip("/")
        if self.key_prefix and not self.key_prefix.endswith("/"):
            self.key_prefix += "/"

        self._s3_client = None

    def is_configured(self) -> bool:
        return bool(self.endpoint_url and self.bucket_name and self.access_key_id and self.secret_access_key)

    @property
    def client(self):
        if self._s3_client is None:
            if not self.is_configured():
                raise S3StorageError("S3/R2/B2 对象存储未配置完整")
            config = Config(
                region_name=self.region_name,
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            )
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                config=config,
            )
        return self._s3_client

    def verify_bucket(self) -> dict[str, Any]:
        """Test connection and bucket access."""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return {
                "endpoint": self.endpoint_url,
                "bucket": self.bucket_name,
                "region": self.region_name,
                "public_url": self.public_url or f"{self.endpoint_url}/{self.bucket_name}",
            }
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise S3StorageError(f"存储桶验证失败 [{error_code}]: {exc}") from exc
        except Exception as exc:
            raise S3StorageError(f"连接存储桶失败: {exc}") from exc

    def build_key(self, source_id: str, chapter_ordinal: int, page_number: int, ext: str) -> str:
        """Construct deterministic object key for a manga page."""
        ext = ext if ext.startswith(".") else f".{ext}"
        prefix = self.key_prefix
        return f"{prefix}{source_id}/{chapter_ordinal:04d}/{page_number:04d}{ext}"

    def upload_file_sync(self, local_path: Path, object_key: str, content_type: str) -> str:
        """Synchronously upload a single file to S3/R2/B2."""
        extra_args = {
            "ContentType": content_type,
            "CacheControl": "public, max-age=31536000, immutable",
        }
        try:
            self.client.upload_file(
                Filename=str(local_path),
                Bucket=self.bucket_name,
                Key=object_key,
                ExtraArgs=extra_args,
            )
        except Exception as exc:
            logger.error("s3_upload_file_failed key=%s error=%s", object_key, exc)
            raise S3StorageError(f"上传文件至存储桶失败: {exc}") from exc

        base = self.public_url or f"{self.endpoint_url}/{self.bucket_name}"
        return f"{base}/{object_key.lstrip('/')}"

    async def upload_file(self, local_path: Path, object_key: str | None = None) -> str:
        """Asynchronously upload a local image to S3/R2/B2 and return the public URL."""
        if not local_path.is_file():
            raise FileNotFoundError(f"文件不存在: {local_path}")

        ext = local_path.suffix.lower()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "image/jpeg")

        if not object_key:
            object_key = f"{self.key_prefix}{uuid.uuid4().hex[:16]}_{local_path.name}"

        return await asyncio.to_thread(self.upload_file_sync, local_path, object_key, content_type)

    def delete_manga_objects_sync(self, source_id: str) -> int:
        """Synchronously delete all objects associated with a manga source_id."""
        if not self.is_configured():
            return 0
        deleted_count = 0
        clean_id = str(source_id).strip()
        if not clean_id or any(c in clean_id for c in ("/", "\\", "*", "?", "..")):
            return 0
        prefixes = [
            f"{self.key_prefix}{clean_id}/",
            f"manga_{clean_id}/",
        ]
        for prefix in prefixes:
            try:
                paginator = self.client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    contents = page.get("Contents", [])
                    if not contents:
                        continue
                    objects = [{"Key": item["Key"]} for item in contents]
                    for i in range(0, len(objects), 1000):
                        chunk = objects[i : i + 1000]
                        res = self.client.delete_objects(
                            Bucket=self.bucket_name,
                            Delete={"Objects": chunk},
                        )
                        deleted_count += len(res.get("Deleted", []))
            except Exception as exc:  # noqa: BLE001
                logger.warning("delete_manga_objects_failed prefix=%s error=%s", prefix, exc)
        return deleted_count

    async def delete_manga_objects(self, source_id: str) -> int:
        """Asynchronously delete all objects associated with a manga source_id."""
        return await asyncio.to_thread(self.delete_manga_objects_sync, source_id)

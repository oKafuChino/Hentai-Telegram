"""Cloudflare R2 concurrent upload module with adaptive retries and custom domain URLs."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config

try:
    from src.config import Settings
    from src.utils import format_bytes, setup_logger
except ImportError:
    from config import Settings
    from utils import format_bytes, setup_logger

logger = setup_logger("storage_r2")


class R2StorageClient:
    """High-concurrency S3/R2 storage client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.r2_bucket_name
        self.custom_domain = (settings.r2_custom_domain or "").rstrip("/")

        # Configure connection pool and adaptive retry strategy
        boto_config = Config(
            retries={"max_attempts": 5, "mode": "adaptive"},
            max_pool_connections=max(32, settings.io_upload_workers * 2),
        )

        endpoint_url = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=boto_config,
        )

    def is_configured(self) -> bool:
        """Check if R2 credentials and bucket are provided."""
        return bool(
            self.settings.r2_account_id
            and self.settings.r2_access_key_id
            and self.settings.r2_secret_access_key
            and self.bucket
        )

    def build_key(self, manga_id: str, chapter_num: str, slice_filename: str) -> str:
        """Generate canonical S3 storage key."""
        return f"manga_{manga_id}/ch_{chapter_num}/{slice_filename}"

    def build_public_url(self, key: str) -> str:
        """Construct full public URL using custom domain or R2 direct link."""
        if self.custom_domain:
            return f"{self.custom_domain}/{key}"
        # Fallback to endpoint path style if custom domain not configured
        return f"https://{self.settings.r2_account_id}.r2.cloudflarestorage.com/{self.bucket}/{key}"

    def _upload_single_slice(
        self,
        slice_filename: str,
        data: bytes,
        manga_id: str,
        chapter_num: str,
    ) -> tuple[str, str]:
        """Upload one slice to Cloudflare R2 with mandatory caching headers."""
        key = self.build_key(manga_id, chapter_num, slice_filename)
        ext = Path(slice_filename).suffix.lower()
        content_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/webp"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000",
        )
        url = self.build_public_url(key)
        return slice_filename, url

    def upload_chapter_slices(
        self,
        slices: Sequence[tuple[str, bytes]],
        manga_id: str,
        chapter_num: str,
    ) -> list[str]:
        """Concurrently upload all in-memory slices to R2 and return ordered URLs."""
        if not slices:
            return []

        if not self.is_configured():
            raise ValueError(
                "Cloudflare R2 未正确配置，请在 .env 中设置 R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME"
            )

        total_count = len(slices)
        total_bytes = sum(len(d) for _, d in slices)
        logger.info(
            f"开始并发上传 [manga_{manga_id}/ch_{chapter_num}]: "
            f"共 {total_count} 个切片 ({format_bytes(total_bytes)}), 线程数: {self.settings.io_upload_workers}"
        )

        completed_map: dict[str, str] = {}
        uploaded_count = 0

        with ThreadPoolExecutor(max_workers=self.settings.io_upload_workers) as executor:
            future_to_fn = {
                executor.submit(
                    self._upload_single_slice, fn, data, manga_id, chapter_num
                ): fn
                for fn, data in slices
            }

            for future in as_completed(future_to_fn):
                fn = future_to_fn[future]
                try:
                    slice_fn, url = future.result()
                    completed_map[slice_fn] = url
                    uploaded_count += 1
                    if uploaded_count % 10 == 0 or uploaded_count == total_count:
                        logger.info(f"R2 上传进度: {uploaded_count}/{total_count} ({uploaded_count/total_count*100:.1f}%)")
                except Exception as exc:
                    logger.error(f"切片 {fn} 上传失败: {exc}")
                    raise

        # Return URLs strictly in original slice order
        ordered_urls = [completed_map[fn] for fn, _ in slices]
        logger.info(f"✅ R2 全部切片并发上传完成，生成 {len(ordered_urls)} 个直链")
        return ordered_urls

    def delete_manga_objects(self, manga_id: str) -> int:
        """Delete all objects of a manga from R2."""
        if not self.is_configured():
            return 0
        deleted_count = 0
        clean_id = str(manga_id).strip()
        if not clean_id or any(c in clean_id for c in ("/", "\\", "*", "?", "..")):
            return 0
        prefixes = [
            f"manga_{clean_id}/",
            f"manga/{clean_id}/",
        ]
        for prefix in prefixes:
            try:
                paginator = self.s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    contents = page.get("Contents", [])
                    if not contents:
                        continue
                    objects = [{"Key": item["Key"]} for item in contents]
                    for i in range(0, len(objects), 1000):
                        chunk = objects[i : i + 1000]
                        res = self.s3.delete_objects(
                            Bucket=self.bucket,
                            Delete={"Objects": chunk},
                        )
                        deleted_count += len(res.get("Deleted", []))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"R2 删除对象异常 prefix={prefix}: {exc}")
        return deleted_count

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    telegram_channel_id: str
    database_url: str = "sqlite+aiosqlite:///./data/app/hentai_telegram.db"
    storage_root: str = "/storage"
    download_concurrency: int = 2
    retry_max_attempts: int = 3
    min_free_space_gb: int = 2
    resume_free_space_gb: int = 5
    disk_space_check_enabled: bool = False
    provider_command: str = ""
    provider_domains: tuple[str, ...] = ()
    operation_timeout: int = 300
    max_page_bytes: int = 30 * 1024 * 1024
    max_pixels: int = 50_000_000
    max_chapter_pages: int = 2000
    max_task_bytes: int = 10 * 1024**3
    telegraph_api_url: str = "https://api.telegra.ph"
    telegraph_upload_url: str = "https://catbox.moe/user/api.php"
    telegraph_access_token: str = field(default="", repr=False)
    telegraph_author_name: str = "Hentai-Telegram"
    telegraph_author_url: str = ""
    catbox_userhash: str = field(default="", repr=False)
    s3_endpoint_url: str = ""
    s3_bucket_name: str = ""
    s3_access_key_id: str = field(default="", repr=False)
    s3_secret_access_key: str = field(default="", repr=False)
    s3_public_url: str = ""
    s3_region_name: str = "auto"
    s3_key_prefix: str = "manga/"
    image_optimize_enabled: bool = True
    image_jpeg_quality: int = 85
    image_max_width: int = 1600
    image_min_compress_bytes: int = 100 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        users = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        channel = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
        if not token or token == "replace-me":
            raise ValueError("TELEGRAM_BOT_TOKEN 必须配置")
        if not users:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS 必须配置至少一个管理员数字 ID")
        try:
            allowed = frozenset(int(item.strip()) for item in users.split(",") if item.strip())
        except ValueError as exc:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS 必须是逗号分隔的数字 ID") from exc
        if not allowed or any(u <= 0 for u in allowed):
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS 必须包含正整数 ID")

        minimum = _int("MIN_FREE_SPACE_GB", 2)
        resume = _int("RESUME_FREE_SPACE_GB", 5)
        if minimum < 0 or resume <= minimum:
            raise ValueError("RESUME_FREE_SPACE_GB 必须大于 MIN_FREE_SPACE_GB")

        raw_upload = os.getenv("TELEGRAPH_UPLOAD_URL", "").strip()
        if not raw_upload or raw_upload.rstrip("/") == "https://telegra.ph/upload":
            upload_url = "https://catbox.moe/user/api.php"
        else:
            upload_url = raw_upload

        s3_endpoint = (
            os.getenv("S3_ENDPOINT_URL")
            or os.getenv("R2_ENDPOINT_URL")
            or os.getenv("B2_ENDPOINT_URL")
            or ""
        ).strip()
        s3_bucket = (
            os.getenv("S3_BUCKET_NAME")
            or os.getenv("R2_BUCKET_NAME")
            or os.getenv("B2_BUCKET_NAME")
            or ""
        ).strip()
        s3_key_id = (
            os.getenv("S3_ACCESS_KEY_ID")
            or os.getenv("R2_ACCESS_KEY_ID")
            or os.getenv("B2_KEY_ID")
            or ""
        ).strip()
        s3_secret = (
            os.getenv("S3_SECRET_ACCESS_KEY")
            or os.getenv("R2_SECRET_ACCESS_KEY")
            or os.getenv("B2_APPLICATION_KEY")
            or ""
        ).strip()
        s3_public = (
            os.getenv("S3_PUBLIC_URL")
            or os.getenv("R2_PUBLIC_URL")
            or os.getenv("B2_PUBLIC_URL")
            or ""
        ).strip()
        s3_region = (
            os.getenv("S3_REGION_NAME")
            or os.getenv("R2_REGION_NAME")
            or os.getenv("B2_REGION_NAME")
            or "auto"
        ).strip() or "auto"
        s3_prefix = (
            os.getenv("S3_KEY_PREFIX")
            or os.getenv("R2_KEY_PREFIX")
            or "manga/"
        ).strip()

        opt_enabled = os.getenv("IMAGE_OPTIMIZE_ENABLED", "true").lower() in {"true", "1", "yes"}
        jpeg_quality = _int("IMAGE_JPEG_QUALITY", 85)
        max_width = _int("IMAGE_MAX_WIDTH", 1600)
        min_bytes = _int("IMAGE_MIN_COMPRESS_BYTES", 100 * 1024)

        return cls(
            telegram_bot_token=token,
            allowed_user_ids=allowed,
            telegram_channel_id=channel,
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app/hentai_telegram.db"),
            storage_root=os.getenv("STORAGE_ROOT", "./data/storage"),
            download_concurrency=_int("DOWNLOAD_CONCURRENCY", 2),
            retry_max_attempts=_int("RETRY_MAX_ATTEMPTS", 3),
            min_free_space_gb=minimum,
            resume_free_space_gb=resume,
            disk_space_check_enabled=os.getenv("DISK_SPACE_CHECK_ENABLED", "false").lower() in {"true", "1", "yes"},
            provider_command=os.getenv("PROVIDER_COMMAND") or json.dumps([sys.executable, "-m", "hentai_telegram.providers.jm_adapter"]),
            provider_domains=tuple(x.strip().lower() for x in os.getenv("PROVIDER_DOMAINS", "").split(",") if x.strip()),
            operation_timeout=_int("OPERATION_TIMEOUT", 300),
            max_page_bytes=_int("MAX_PAGE_BYTES", 30 * 1024 * 1024),
            max_pixels=_int("MAX_PIXELS", 50_000_000),
            max_chapter_pages=_int("MAX_CHAPTER_PAGES", 2000),
            max_task_bytes=_int("MAX_TASK_BYTES", 10 * 1024**3),
            telegraph_api_url=os.getenv("TELEGRAPH_API_URL", "https://api.telegra.ph").rstrip("/"),
            telegraph_upload_url=upload_url,
            telegraph_access_token=os.getenv("TELEGRAPH_ACCESS_TOKEN", "").strip(),
            telegraph_author_name=os.getenv("TELEGRAPH_AUTHOR_NAME", "Hentai-Telegram").strip(),
            telegraph_author_url=os.getenv("TELEGRAPH_AUTHOR_URL", "").strip(),
            catbox_userhash=os.getenv("CATBOX_USERHASH", "").strip(),
            s3_endpoint_url=s3_endpoint,
            s3_bucket_name=s3_bucket,
            s3_access_key_id=s3_key_id,
            s3_secret_access_key=s3_secret,
            s3_public_url=s3_public,
            s3_region_name=s3_region,
            s3_key_prefix=s3_prefix,
            image_optimize_enabled=opt_enabled,
            image_jpeg_quality=max(50, min(jpeg_quality, 95)),
            image_max_width=max(0, max_width),
            image_min_compress_bytes=max(0, min_bytes),
        )

    def __post_init__(self):
        for name in (
            "download_concurrency",
            "retry_max_attempts",
            "operation_timeout",
            "max_page_bytes",
            "max_pixels",
            "max_chapter_pages",
            "max_task_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正整数")

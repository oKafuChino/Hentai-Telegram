"""Configuration management using pydantic-settings."""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CompressConfig(BaseModel):
    """Configuration specifically for CPU-bound image compression and slicing."""

    cpu_workers: int = Field(default=2, description="ProcessPoolExecutor 并发进程数")
    max_width_bw: int = Field(default=1280, description="黑白页最大宽度")
    max_width_color: int = Field(default=1440, description="彩页最大宽度")
    max_slice_height: int = Field(default=2400, description="条漫切片阈值高度")
    quality_bw: int = Field(default=75, description="黑白页编码质量 (0-100)")
    quality_color: int = Field(default=80, description="彩页编码质量 (0-100)")
    grayscale_threshold: float = Field(default=3.5, description="黑白/彩色通道差异判别阈值")
    image_format: str = Field(default="WEBP", description="输出图片格式 (WEBP/JPEG)")
    image_min_compress_bytes: int = Field(default=102400, description="触发压缩的最小文件体积 (bytes，默认 100KB)")


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # --- Cloudflare R2 / S3 配置 ---
    r2_account_id: str = Field(default="", description="Cloudflare 账号 ID")
    r2_access_key_id: str = Field(default="", description="R2 Access Key ID")
    r2_secret_access_key: str = Field(default="", description="R2 Secret Access Key")
    r2_bucket_name: str = Field(default="manga-bucket", description="R2 存储桶名")
    r2_custom_domain: str = Field(default="", description="R2 公开自定义域名")

    # --- 图像压缩与并发参数 ---
    cpu_workers: int = Field(default=2, description="图像处理 CPU 进程数")
    io_upload_workers: int = Field(default=16, description="R2 上传 IO 线程数")
    max_width_bw: int = Field(default=1280, description="黑白页最大宽度限制")
    max_width_color: int = Field(default=1440, description="彩页最大宽度限制")
    max_slice_height: int = Field(default=2400, description="条漫自动横向切片高度")
    quality_bw: int = Field(default=75, description="黑白页编码质量")
    quality_color: int = Field(default=80, description="彩页编码质量")
    grayscale_threshold: float = Field(default=3.5, description="灰度识别阈值")
    image_format: str = Field(default="WEBP", description="图片格式 (默认 WEBP)")
    image_min_compress_bytes: int = Field(default=102400, description="触发压缩的最小文件大小 (bytes，默认 100KB)")

    # --- Telegraph 与分页配置 ---
    telegraph_short_name: str = Field(default="MangaBot", description="Telegraph 短名")
    telegraph_author_name: str = Field(default="漫画频道", description="Telegraph 作者名称")
    telegraph_access_token: str = Field(default="", description="Telegraph Access Token")
    telegraph_max_images_per_page: int = Field(default=100, description="单页最大切片图片数量")

    # --- Telegram Bot 配置 ---
    tg_bot_token: str = Field(default="", description="Telegram Bot Token")
    tg_main_chat_id: str = Field(default="", description="正式发布频道 ID")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def get_compress_config(self) -> CompressConfig:
        """Extract CompressConfig for multiprocessing workers."""
        return CompressConfig(
            cpu_workers=self.cpu_workers,
            max_width_bw=self.max_width_bw,
            max_width_color=self.max_width_color,
            max_slice_height=self.max_slice_height,
            quality_bw=self.quality_bw,
            quality_color=self.quality_color,
            grayscale_threshold=self.grayscale_threshold,
            image_format=self.image_format,
            image_min_compress_bytes=self.image_min_compress_bytes,
        )

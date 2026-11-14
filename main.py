"""CLI Entrypoint and pipeline orchestrator for Manga Telegraph Pipeline."""

import argparse
import sys
import time
from pathlib import Path

from src.compressor import compress_chapter
from src.config import Settings
from src.storage_r2 import R2StorageClient
from src.telegraph_pub import TelegraphPublisher
from src.tg_bot import send_channel_notification_sync
from src.utils import format_bytes, scan_image_files, setup_logger

logger = setup_logger("main")


def run_pipeline(
    directory: str | Path,
    manga_id: str,
    chapter_num: str,
    title: str,
    tags: list[str] | None = None,
    cover: str | None = None,
    dry_run: bool = False,
    skip_tg: bool = False,
) -> None:
    """Execute complete end-to-end pipeline."""
    settings = Settings()
    chapter_dir = Path(directory)
    if not chapter_dir.is_dir():
        logger.error(f"指定的章节目录不存在: {directory}")
        sys.exit(1)

    print("=" * 65)
    print("📖 Manga Telegraph Pipeline - 漫画智能压缩与发布流水线")
    print("=" * 65)
    print(f"• 漫画 ID: {manga_id}")
    print(f"• 章节号: 第 {chapter_num} 话")
    print(f"• 标题: {title}")
    if tags:
        print(f"• 标签: {', '.join(tags)}")
    print(f"• 目录: {chapter_dir.resolve()}")
    print(f"• 运行模式: {'[DRY RUN 预演]' if dry_run else '[正式发布]'}")
    print("-" * 65)

    # 1. Input Layer: scan and naturally sort image files
    files = scan_image_files(chapter_dir)
    if not files:
        logger.error("目录下未找到任何支持的图片文件！")
        sys.exit(1)

    logger.info(f"读取原始图片序列: 共 {len(files)} 页")

    # 2. Compression & Slicing Layer: in-memory multi-process processing
    orig_total_size = sum(f.stat().st_size for f in files)
    compress_cfg = settings.get_compress_config()

    start_time = time.perf_counter()
    logger.info(f"启动 ProcessPoolExecutor (CPU 并发: {compress_cfg.cpu_workers}) 进行流式内存压缩与切片...")
    slices = compress_chapter(files, compress_cfg)
    compress_duration = time.perf_counter() - start_time

    compressed_total_size = sum(len(d) for _, d in slices)
    saved_bytes = orig_total_size - compressed_total_size
    reduction_pct = (saved_bytes / orig_total_size * 100.0) if orig_total_size > 0 else 0.0

    print("-" * 65)
    print("📊 内存流式压缩统计:")
    print(f"• 原始总大小: {format_bytes(orig_total_size)}")
    print(f"• 压缩后大小: {format_bytes(compressed_total_size)}")
    print(f"• 体积缩减率: {reduction_pct:.2f}% (节省 {format_bytes(saved_bytes)})")
    print(f"• 生成切片数: {len(slices)} 个 (原 {len(files)} 页)")
    print(f"• 压缩耗时: {compress_duration:.2f} 秒 ({len(files) / max(0.001, compress_duration):.1f} 页/秒)")
    print("-" * 65)

    if dry_run:
        print("✨ [DRY RUN] 预演测试完成，未执行 R2 上传及 Telegram 推送。")
        print("=" * 65)
        return

    # 3. Storage Layer: upload slices to Cloudflare R2
    logger.info("连接 Cloudflare R2 对象存储...")
    r2_client = R2StorageClient(settings)
    image_urls = r2_client.upload_chapter_slices(slices, manga_id=manga_id, chapter_num=chapter_num)

    # 4. Telegraph Layer: auto pagination and publication
    logger.info("排版并发布至 Telegraph...")
    telegraph_pub = TelegraphPublisher(settings)
    telegraph_urls = telegraph_pub.publish_chapter(title=title, image_urls=image_urls)

    print("-" * 65)
    print(f"🎉 Telegraph 页面发布成功 (共 {len(telegraph_urls)} 卷):")
    for idx, u in enumerate(telegraph_urls, 1):
        print(f"  [{idx}] {u}")
    print("-" * 65)

    # 5. Telegram Bot Layer: cache prewarming and channel push
    if skip_tg:
        logger.info("已指定 --skip-tg，跳过 Telegram 频道推送")
    else:
        logger.info("向 Telegram 正式频道推送消息并生成 Instant View 即时预览...")
        send_channel_notification_sync(
            settings=settings,
            title=title,
            chapter_num=chapter_num,
            telegraph_urls=telegraph_urls,
            tags=tags,
            cover_url=cover,
        )

    print("=" * 65)
    print("✅ 流水线任务全部执行完毕！")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Manga Telegraph Pipeline CLI")
    parser.add_argument("--dir", required=True, help="漫画章节本地图片目录")
    parser.add_argument("--id", required=True, help="漫画 ID (如 1001)")
    parser.add_argument("--ch", required=True, help="章节序号 (如 1 或 01)")
    parser.add_argument("--title", required=True, help="章节标题 (如 '示例漫画 第01话')")
    parser.add_argument("--tags", help="标签 (逗号分隔，如 '同人,全彩,治愈')")
    parser.add_argument("--cover", help="封面图本地路径或公开图片 URL (可选)")
    parser.add_argument("--dry-run", action="store_true", help="仅执行内存压缩测试，不上传发布")
    parser.add_argument("--skip-tg", action="store_true", help="上传 R2 并发布 Telegraph，但跳过 Telegram 频道推送")

    args = parser.parse_args()

    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None

    run_pipeline(
        directory=args.dir,
        manga_id=args.id,
        chapter_num=args.ch,
        title=args.title,
        tags=tags_list,
        cover=args.cover,
        dry_run=args.dry_run,
        skip_tg=args.skip_tg,
    )


if __name__ == "__main__":
    main()

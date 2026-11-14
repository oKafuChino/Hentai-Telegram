import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

from hentai_telegram.config import Settings
from hentai_telegram.db.store import Store
from hentai_telegram.domain.errors import JobError
from hentai_telegram.integrations.telegraph import TelegraphClient
from hentai_telegram.providers.builtin import JmProvider
from hentai_telegram.providers.command import CommandProvider
from hentai_telegram.runtime import JsonFormatter, backup_database, database_path, instance_lock
from hentai_telegram.services.channel_push import ChannelPublisher
from hentai_telegram.services.diagnostics import Diagnostics, RecentLogs
from hentai_telegram.services.tasks import TaskService
from hentai_telegram.services.users import UserService
from hentai_telegram.workers.queue import Worker

logger = logging.getLogger(__name__)


async def run(settings: Settings, db_path, recent_logs):
    from aiogram import Bot

    from hentai_telegram.bot.app import create_dispatcher, progress_updater

    provider_type = CommandProvider if os.getenv("PROVIDER_COMMAND") else JmProvider
    provider = provider_type(settings.provider_command, settings.operation_timeout)

    store = Store(db_path)

    s3_client = None
    if settings.s3_endpoint_url and settings.s3_bucket_name:
        from hentai_telegram.storage.s3 import S3StorageClient
        s3_client = S3StorageClient(
            endpoint_url=settings.s3_endpoint_url,
            bucket_name=settings.s3_bucket_name,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            public_url=settings.s3_public_url,
            region_name=settings.s3_region_name,
            key_prefix=settings.s3_key_prefix,
        )

    telegraph = TelegraphClient(
        api_url=settings.telegraph_api_url,
        upload_url=settings.telegraph_upload_url,
        access_token=settings.telegraph_access_token,
        author_name=settings.telegraph_author_name,
        author_url=settings.telegraph_author_url,
        token_file=db_path.parent / "telegraph_token.txt",
        catbox_userhash=settings.catbox_userhash,
        s3_client=s3_client,
    )

    bot = Bot(settings.telegram_bot_token)
    publisher = ChannelPublisher(bot, settings.telegram_channel_id)
    worker = Worker(store, provider, telegraph, publisher, settings)
    users = UserService(settings.allowed_user_ids)
    task_service = TaskService(
        store,
        provider,
        settings.provider_domains,
        settings.allowed_user_ids,
        telegraph=telegraph,
    )
    diagnostics = Diagnostics(users, worker, recent_logs, publisher)

    dispatcher = create_dispatcher(
        service=task_service,
        diagnostics=diagnostics,
        publisher=publisher,
        allowed_user_ids=settings.allowed_user_ids,
        channel_id=settings.telegram_channel_id,
        s3_client=s3_client,
        storage_root=Path(settings.storage_root),
        telegraph=telegraph,
    )

    heartbeat = db_path.parent / "heartbeat"

    async def health():
        while True:
            if time.monotonic() - worker.heartbeat < 30:
                heartbeat.write_text(str(time.time()))
            await asyncio.sleep(5)

    jobs = [
        asyncio.create_task(worker.run()),
        asyncio.create_task(progress_updater(bot, store, settings.telegram_channel_id)),
        asyncio.create_task(health()),
        asyncio.create_task(dispatcher.start_polling(bot)),
    ]

    try:
        logger.info("hentai_telegram_started channel=%s", settings.telegram_channel_id)
        done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        for job in done:
            job.result()
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await bot.session.close()
        store.close()
        heartbeat.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Hentai-Telegram: Manga to Telegraph Channel Uploader")
    parser.add_argument(
        "command",
        choices=["run", "check-config", "check-channel", "check-storage", "check-source", "health", "backup"],
        nargs="?",
        default="run",
    )
    parser.add_argument("--destination")
    parser.add_argument("--manga-id")
    args = parser.parse_args()

    db_path = database_path(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app/hentai_telegram.db"))

    if args.command == "health":
        try:
            alive = time.time() - float((db_path.parent / "heartbeat").read_text()) < 60
        except (OSError, ValueError):
            alive = False
        raise SystemExit(0 if alive else 1)

    if args.command == "backup":
        if not args.destination:
            parser.error("--destination is required")
        backup_database(db_path, args.destination)
        print(f"Database backed up to {args.destination}")
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    recent_logs = RecentLogs()
    logging.basicConfig(handlers=[handler, recent_logs], level=logging.INFO)

    try:
        settings = Settings.from_env()

        if args.command == "check-config":
            print("Configuration valid:")
            print(f"• Allowed Admin IDs: {list(settings.allowed_user_ids)}")
            print(f"• Target Channel: {settings.telegram_channel_id or '(Not specified)'}")
            print(f"• Telegraph API: {settings.telegraph_api_url}")
            if settings.s3_endpoint_url and settings.s3_bucket_name:
                print(f"• Image Storage: R2/B2 S3 (Bucket: {settings.s3_bucket_name}, Endpoint: {settings.s3_endpoint_url})")
                print(f"• Public Image URL: {settings.s3_public_url or f'{settings.s3_endpoint_url}/{settings.s3_bucket_name}'}")
                print(f"• S3 Key Prefix: {settings.s3_key_prefix}")
            else:
                print(f"• Image Storage: HTTP/Catbox ({settings.telegraph_upload_url})")
            if settings.image_optimize_enabled:
                print(f"• Image Optimization: Enabled (JPEG Q{settings.image_jpeg_quality}, Max Width: {settings.image_max_width or 'Unlimited'}px)")
            else:
                print("• Image Optimization: Disabled")
            print(f"• Storage Root: {settings.storage_root}")
            return

        if args.command == "check-storage":
            if not settings.s3_endpoint_url or not settings.s3_bucket_name:
                print("未配置 S3 / R2 / B2 对象存储（当前使用默认图床 Catbox）")
                return
            from hentai_telegram.storage.s3 import S3StorageClient, S3StorageError
            s3 = S3StorageClient(
                endpoint_url=settings.s3_endpoint_url,
                bucket_name=settings.s3_bucket_name,
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key,
                public_url=settings.s3_public_url,
                region_name=settings.s3_region_name,
                key_prefix=settings.s3_key_prefix,
            )
            try:
                info = s3.verify_bucket()
                print("R2/B2 对象存储连接与鉴权测试成功！")
                print(f"• Endpoint: {info['endpoint']}")
                print(f"• Bucket: {info['bucket']}")
                print(f"• Region: {info['region']}")
                print(f"• Public URL: {info['public_url']}")
            except S3StorageError as exc:
                parser.exit(2, f"R2/B2 存储桶测试失败: {exc}\n")
            return

        if args.command == "check-channel":
            from aiogram import Bot

            async def test_ch():
                bot = Bot(settings.telegram_bot_token)
                try:
                    pub = ChannelPublisher(bot, settings.telegram_channel_id)
                    info = await pub.verify_channel()
                    print(f"Channel verified successfully: {info}")
                finally:
                    await bot.session.close()

            asyncio.run(test_ch())
            return

        if args.command == "check-source":
            from hentai_telegram.providers.reference import parse_reference

            if not args.manga_id:
                parser.error("check-source requires --manga-id")
            source_id = parse_reference(args.manga_id)
            provider = (
                CommandProvider(settings.provider_command, settings.operation_timeout)
                if os.getenv("PROVIDER_COMMAND")
                else JmProvider(settings.provider_command, settings.operation_timeout)
            )
            manga = asyncio.run(provider.get_manga(source_id))
            print(f"数据源元数据查询成功: 《{manga.title}》 共 {len(manga.chapters)} 个章节。")
            return

        with instance_lock(db_path.parent / "instance.lock"):
            asyncio.run(run(settings, db_path, recent_logs))

    except JobError as exc:
        parser.exit(2, f"检查失败 [{exc.code}]：{exc}\n")
    except (ValueError, RuntimeError, PermissionError) as exc:
        parser.exit(2, f"Startup failed: {exc}\n")


if __name__ == "__main__":
    main()

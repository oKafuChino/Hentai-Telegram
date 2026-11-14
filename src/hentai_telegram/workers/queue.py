import asyncio
from dataclasses import asdict, replace
import errno
import json
import logging
from pathlib import Path
import random
import shutil
import time
import aiohttp

from hentai_telegram.db.store import Store, decode_manga
from hentai_telegram.domain.errors import Cancelled, DiskLow, JobError
from hentai_telegram.domain.models import Manga, Page
from hentai_telegram.integrations.telegraph import TelegraphClient
from hentai_telegram.services.channel_push import ChannelPublisher
from hentai_telegram.storage.cbz import contained, staged_pages, validate_image

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        store: Store,
        provider,
        telegraph: TelegraphClient,
        publisher: ChannelPublisher,
        settings,
    ):
        self.store = store
        self.provider = provider
        self.telegraph = telegraph
        self.publisher = publisher
        self.settings = settings
        self.root = Path(settings.storage_root).resolve()
        self.staging = self.root / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.heartbeat = time.monotonic()
        self.current_task: str | None = None

    def check(self, task_id: str):
        self.heartbeat = time.monotonic()
        task = self.store.get(task_id)
        if not task or task.get("cancel_requested"):
            raise Cancelled()
        if (
            self.settings.disk_space_check_enabled
            and shutil.disk_usage(self.root).free < self.settings.min_free_space_gb * 1024**3
        ):
            raise DiskLow()

    async def guarded(self, task_id: str, awaitable):
        operation = asyncio.ensure_future(awaitable)
        try:
            while not operation.done():
                self.check(task_id)
                await asyncio.wait({operation}, timeout=0.25)
            self.check(task_id)
            return await operation
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    async def run_once(self) -> bool:
        self.heartbeat = time.monotonic()
        task = self.store.next_task()
        if task is None:
            return False

        task_id = task["id"]
        self.current_task = task_id
        try:
            await self.execute(task)
            self.store.finish(task_id, "COMPLETED")
        except Cancelled as exc:
            self.store.finish(task_id, "CANCELLED", exc.code, str(exc))
        except DiskLow as exc:
            self.store.update(task_id, status="PAUSED_DISK", error_code=exc.code, error=str(exc))
        except JobError as exc:
            attempts = task.get("attempts", 0) + 1
            message = str(exc)
            if exc.retryable and attempts < self.settings.retry_max_attempts:
                delay = max(exc.delay, min(300, 2**attempts + random.random()))
                self.store.update(
                    task_id,
                    status="RETRY_WAIT",
                    attempts=attempts,
                    next_at=time.time() + delay,
                    error_code=exc.code,
                    error=message,
                    progress=f"重试等待中 (第 {attempts}/{self.settings.retry_max_attempts} 次): {message}",
                )
            else:
                self.store.finish(task_id, "FAILED", exc.code, message)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                self.store.finish(task_id, "FAILED", "DISK_FULL", "服务器磁盘已满")
            else:
                self.store.finish(task_id, "FAILED", "FILESYSTEM", "文件系统读写失败")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("task_failure task=%s exception=%s", task_id, exc, exc_info=True)
            self.store.finish(task_id, "FAILED", "INTERNAL", f"内部错误: {exc}")
        finally:
            self.current_task = None
            task_stage = contained(self.staging, task_id)
            if task_stage.exists():
                shutil.rmtree(task_stage, ignore_errors=True)

        return True

    async def execute(self, task: dict):
        task_id = task["id"]
        self.check(task_id)
        manga: Manga = decode_manga(task["snapshot"])
        manga_id = task["manga_id"]
        records = self.store.chapters(manga_id)
        chapter_map = {c.source_id: c for c in manga.chapters}

        task_stage = contained(self.staging, task_id)
        task_stage.mkdir(parents=True, exist_ok=True)
        cover_path = task_stage / "cover.jpg"

        channel_url = ""
        if self.settings.telegram_channel_id:
            ch_id = self.settings.telegram_channel_id.strip()
            if ch_id.startswith("@"):
                channel_url = f"https://t.me/{ch_id[1:]}"

        async with aiohttp.ClientSession() as http_session:
            # 1. Process each chapter
            for idx, record in enumerate(records, 1):
                self.check(task_id)
                # Resumable check
                if record.get("telegraph_url"):
                    continue

                chapter = chapter_map[record["source_id"]]
                pages = tuple(Page(**p) for p in json.loads(record["pages"])) if record.get("pages") else None

                if pages is None:
                    self.store.update(
                        task_id,
                        status="DOWNLOADING",
                        phase="download",
                        progress=f"章节 {chapter.ordinal}/{len(records)}: 正在获取分页信息",
                    )
                    pages = await self.guarded(task_id, self.provider.get_pages(chapter.source_id))
                    if not pages or len(pages) > self.settings.max_chapter_pages:
                        raise JobError("PAGE_LIMIT", "章节为空或页数超出限制")
                    self.store.update_chapter(record["id"], pages=json.dumps([asdict(p) for p in pages]))

                if not pages or [p.number for p in pages] != list(range(1, len(pages) + 1)):
                    raise JobError("BAD_PAGES", "章节页清单无效")

                chapter_stage = task_stage / str(record["id"])
                chapter_stage.mkdir(parents=True, exist_ok=True)

                # Download chapter pages
                self.store.update(
                    task_id,
                    status="DOWNLOADING",
                    phase="download",
                    progress=f"章节 {chapter.ordinal}/{len(records)}: 正在下载图片 (0/{len(pages)})",
                )
                if hasattr(self.provider, "download_batch"):
                    pending = []
                    for page in pages:
                        candidates = staged_pages(chapter_stage, page.number)
                        if len(candidates) != 1:
                            pending.append(page)
                    if pending:
                        await self.guarded(
                            task_id,
                            self.provider.download_batch(
                                pending,
                                chapter_stage,
                                self.settings.max_page_bytes,
                                self.settings.max_pixels,
                                self.settings.max_task_bytes,
                            ),
                        )
                else:
                    semaphore = asyncio.Semaphore(self.settings.download_concurrency)

                    async def download_one(p: Page):
                        async with semaphore:
                            self.check(task_id)
                            candidates = staged_pages(chapter_stage, p.number)
                            if candidates:
                                return
                            partial = chapter_stage / f"{p.number:06d}.part"
                            await self.guarded(
                                task_id,
                                self.provider.download(p, partial, self.settings.max_page_bytes),
                            )
                            ext = validate_image(partial, self.settings.max_page_bytes, self.settings.max_pixels)
                            partial.replace(chapter_stage / f"{p.number:06d}{ext}")

                    await asyncio.gather(*(download_one(p) for p in pages))

                # Save cover from chapter 1 page 1
                if idx == 1 and not cover_path.exists():
                    p1_candidates = staged_pages(chapter_stage, 1)
                    if p1_candidates:
                        shutil.copy2(p1_candidates[0], cover_path)

                # Upload chapter images to Telegraph
                self.store.update(
                    task_id,
                    status="UPLOADING",
                    phase="upload",
                    progress=f"章节 {chapter.ordinal}/{len(records)}: 正在上传至 Telegraph (0/{len(pages)})",
                )

                image_urls: list[str] = []
                for p in pages:
                    self.check(task_id)
                    candidates = staged_pages(chapter_stage, p.number)
                    if len(candidates) != 1:
                        raise JobError("BAD_IMAGE", f"缺少图片 {p.number}")
                    img_file = candidates[0]
                    img_url = await self.telegraph.upload_image(http_session, img_file)
                    image_urls.append(img_url)
                    # Clean up local image file right after upload
                    img_file.unlink(missing_ok=True)
                    self.store.update(
                        task_id,
                        progress=f"章节 {chapter.ordinal}/{len(records)}: 正在上传 Telegraph ({p.number}/{len(pages)})",
                    )

                # Create Telegraph page for this chapter
                page_url = await self.telegraph.create_chapter_page(
                    http_session,
                    manga_title=manga.title,
                    chapter_title=chapter.title,
                    ordinal=chapter.ordinal,
                    image_urls=image_urls,
                    channel_url=channel_url,
                )
                self.store.update_chapter(record["id"], telegraph_url=page_url, status="COMPLETED")
                shutil.rmtree(chapter_stage, ignore_errors=True)

            # 2. Reload chapters with telegraph_url
            updated_chapters = self.store.chapters(manga_id)

            # 3. If multiple chapters, create an Index page
            index_url = None
            if len(updated_chapters) > 1:
                self.store.update(
                    task_id,
                    status="UPLOADING",
                    phase="upload",
                    progress="正在生成全本目录页面",
                )
                index_url = await self.telegraph.create_index_page(
                    http_session,
                    manga_title=manga.title,
                    authors=manga.authors,
                    tags=manga.tags,
                    source_id=manga.source_id,
                    chapters=updated_chapters,
                    channel_url=channel_url,
                )
                self.store.update_manga(manga_id, index_url=index_url)
                manga = replace(manga, index_url=index_url)

            # 4. Publish to Telegram Channel
            self.check(task_id)
            self.store.update(
                task_id,
                status="PUBLISHING",
                phase="publish",
                progress="正在发布到 Telegram 频道",
            )
            channel_msg_id = await self.publisher.publish(
                manga=manga,
                chapters=updated_chapters,
                cover_path=cover_path if cover_path.exists() else None,
            )
            self.store.update(
                task_id,
                channel_message_id=channel_msg_id,
                progress="已成功发布到频道",
            )

    async def run(self):
        self.store.recover()
        while True:
            if not await self.run_once():
                await asyncio.sleep(2)

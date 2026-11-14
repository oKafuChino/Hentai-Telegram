import json
import logging
import re
from typing import Any

from hentai_telegram.db.store import Store, decode_manga
from hentai_telegram.domain.models import Manga
from hentai_telegram.providers.reference import parse_reference

logger = logging.getLogger(__name__)


def _extract_telegraph_urls(val: Any) -> list[str]:
    """Extract all Telegraph URLs from string, dict, or list recursively."""
    if not val:
        return []
    if isinstance(val, (list, tuple)):
        urls: list[str] = []
        for v in val:
            urls.extend(_extract_telegraph_urls(v))
        return urls
    val_str = str(val).strip()
    if val_str.startswith("[") and val_str.endswith("]"):
        try:
            parsed = json.loads(val_str)
            if isinstance(parsed, list):
                return _extract_telegraph_urls(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    matches = re.findall(r"https?://telegra\.ph/[^\s\"',?#<>]+", val_str)
    if matches:
        return [m.rstrip("/") for m in matches]
    return []


class TaskService:
    def __init__(
        self,
        store: Store,
        provider,
        domains: tuple[str, ...] = (),
        allowed_user_ids: frozenset[int] | None = None,
        telegraph=None,
    ):
        self.store = store
        self.provider = provider
        self.domains = domains
        self.allowed_user_ids = allowed_user_ids or frozenset()
        self.telegraph = telegraph

    def authorize(self, user_id: int):
        if not self.allowed_user_ids or user_id not in self.allowed_user_ids:
            raise ValueError("抱歉，本机器人为私有频道管理机器人，仅管理员可用。")

    async def download(
        self,
        reference: str,
        user_id: int,
        chat_id: int,
        message_id: int | None = None,
        force: bool = False,
    ) -> dict:
        self.authorize(user_id)
        source_id = parse_reference(reference, self.domains)
        self.store.check_submission(getattr(self.provider, "source", "jmcomic"), source_id, user_id, force=force)
        manga: Manga = await self.provider.get_manga(source_id)
        self.authorize(user_id)
        return self.store.submit(manga, user_id, chat_id, message_id=message_id, force=force)

    def get(self, task_id: str, user_id: int) -> dict:
        self.authorize(user_id)
        task = self.store.get(task_id)
        if not task:
            raise ValueError("任务不存在")
        return task

    def cancel(self, task_id: str, user_id: int):
        self.authorize(user_id)
        self.store.request_cancel(task_id, user_id)

    def retry(self, task_id: str, user_id: int) -> dict:
        self.authorize(user_id)
        task = self.get(task_id, user_id)
        if task["status"] != "FAILED":
            raise ValueError("只能重试失败的任务")
        manga = decode_manga(task["snapshot"])
        return self.store.submit(
            manga,
            user_id,
            task["chat_id"],
            message_id=task.get("message_id"),
            retry_of=task_id,
        )

    def list_tasks(self, user_id: int, limit: int = 20, active_only: bool = False) -> list[dict]:
        self.authorize(user_id)
        return self.store.tasks(limit=limit, active_only=active_only)

    async def delete(
        self,
        reference: str,
        user_id: int,
        bot=None,
        channel_id: str = "",
        s3_client=None,
        storage_root=None,
        telegraph_client=None,
    ) -> dict:
        self.authorize(user_id)
        active_telegraph = telegraph_client or self.telegraph

        # Check if reference is a direct Telegraph URL
        if "telegra.ph/" in reference.lower():
            if not active_telegraph:
                raise ValueError("未配置 Telegraph 客户端，无法清空 Telegraph 页面")
            clean_url = reference.strip()
            deleted = await active_telegraph.delete_page(clean_url)
            if not deleted:
                raise ValueError(f"清空 Telegraph 页面失败，请确认链接有效且由本账号创建: {clean_url}")
            return {
                "source_id": "Telegraph",
                "title": clean_url,
                "found_in_db": False,
                "deleted_channel_msgs": 0,
                "deleted_telegraph_count": 1,
                "deleted_r2_count": 0,
                "deleted_chapters": 0,
                "deleted_tasks": 0,
                "deleted_local_files": 0,
            }

        source_id = parse_reference(reference, self.domains)
        source = getattr(self.provider, "source", "jmcomic")

        # 1. Fetch details before database deletion
        manga_info = self.store.get_manga_with_details(source, source_id)
        title = manga_info["title"] if manga_info else f"JM{source_id}"
        tasks = manga_info.get("tasks", []) if manga_info else []
        chapters = manga_info.get("chapters", []) if manga_info else []

        # 2. Telegraph pages deletion
        deleted_telegraph_count = 0
        if active_telegraph and manga_info:
            telegraph_urls: list[str] = []
            if manga_info.get("index_url"):
                telegraph_urls.extend(_extract_telegraph_urls(manga_info["index_url"]))
            for ch in chapters:
                if ch.get("telegraph_url"):
                    telegraph_urls.extend(_extract_telegraph_urls(ch["telegraph_url"]))
            for t in tasks:
                if t.get("snapshot"):
                    telegraph_urls.extend(_extract_telegraph_urls(t["snapshot"]))

            unique_telegraph_urls = list(dict.fromkeys(telegraph_urls))
            if unique_telegraph_urls:
                try:
                    if hasattr(active_telegraph, "delete_pages"):
                        deleted_telegraph_count = await active_telegraph.delete_pages(unique_telegraph_urls)
                    elif hasattr(active_telegraph, "delete_page"):
                        for u in unique_telegraph_urls:
                            if await active_telegraph.delete_page(u):
                                deleted_telegraph_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("删除 Telegraph 页面异常 (source_id=%s): %s", source_id, exc)

        # 3. Channel message deletion
        deleted_channel_msgs = 0
        if bot and channel_id:
            channel_str = str(channel_id)
            target_chat = int(channel_str) if channel_str.lstrip("-").isdigit() else channel_str
            for t in tasks:
                c_msg_id = t.get("channel_message_id")
                if c_msg_id:
                    try:
                        await bot.delete_message(chat_id=target_chat, message_id=c_msg_id)
                        deleted_channel_msgs += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("删除频道消息失败 (chat_id=%s, msg_id=%s): %s", target_chat, c_msg_id, exc)

        # 4. R2 / S3 storage deletion
        deleted_r2_count = 0
        if s3_client and s3_client.is_configured():
            try:
                deleted_r2_count = await s3_client.delete_manga_objects(source_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除R2对象失败 (source_id=%s): %s", source_id, exc)

        # 5. Local cache and staging deletion
        deleted_local_files = 0
        if storage_root:
            import shutil
            from pathlib import Path

            from hentai_telegram.storage.cbz import id_component

            root_path = Path(storage_root).resolve()
            if root_path.is_dir():
                staging_dir = (root_path / "staging").resolve()
                for t in tasks:
                    t_stage = (staging_dir / f"task_{t['id']}").resolve()
                    if t_stage.exists() and t_stage.is_relative_to(staging_dir) and t_stage != staging_dir:
                        shutil.rmtree(t_stage, ignore_errors=True)
                        deleted_local_files += 1

                id_comp = id_component(source_id)
                src_comp = id_component(source)
                matched_items = set()

                def _scan_and_match(directory: Path):
                    if not directory.is_dir():
                        return
                    for item in directory.iterdir():
                        if item == staging_dir or item.name == "staging":
                            continue
                        name = item.name
                        if (
                            f"[{src_comp}-{id_comp}]" in name
                            or f"[{id_comp}]" in name
                            or f"[JM{source_id}]" in name
                            or f"_JM{source_id}" in name
                            or name in (f"JM{source_id}", str(source_id))
                        ):
                            matched_items.add(item)

                _scan_and_match(root_path)
                for sub in ("library", "download"):
                    _scan_and_match((root_path / sub).resolve())

                for item in matched_items:
                    try:
                        if item.is_relative_to(root_path) and item != root_path and item != staging_dir:
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                                deleted_local_files += 1
                            elif item.is_file():
                                item.unlink(missing_ok=True)
                                deleted_local_files += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("删除本地缓存文件失败 item=%s: %s", item, exc)

        # 6. Database record deletion
        self.store.delete_manga(source, source_id)

        return {
            "source_id": source_id,
            "title": title,
            "found_in_db": bool(manga_info),
            "deleted_channel_msgs": deleted_channel_msgs,
            "deleted_telegraph_count": deleted_telegraph_count,
            "deleted_r2_count": deleted_r2_count,
            "deleted_chapters": len(chapters),
            "deleted_tasks": len(tasks),
            "deleted_local_files": deleted_local_files,
        }

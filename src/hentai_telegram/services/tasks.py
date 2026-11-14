import logging

from hentai_telegram.db.store import Store, decode_manga
from hentai_telegram.domain.models import Manga
from hentai_telegram.providers.reference import parse_reference

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, store: Store, provider, domains: tuple[str, ...] = (), allowed_user_ids: frozenset[int] | None = None):
        self.store = store
        self.provider = provider
        self.domains = domains
        self.allowed_user_ids = allowed_user_ids or frozenset()

    def authorize(self, user_id: int):
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
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
    ) -> dict:
        self.authorize(user_id)
        source_id = parse_reference(reference, self.domains)
        source = getattr(self.provider, "source", "jmcomic")

        # 1. Database record deletion
        manga_info = self.store.delete_manga(source, source_id)
        title = manga_info["title"] if manga_info else f"JM{source_id}"
        tasks = manga_info.get("tasks", []) if manga_info else []
        chapters = manga_info.get("chapters", []) if manga_info else []

        # 2. Channel message deletion
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

        # 3. R2 / S3 storage deletion
        deleted_r2_count = 0
        if s3_client and s3_client.is_configured():
            try:
                deleted_r2_count = await s3_client.delete_manga_objects(source_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除R2对象失败 (source_id=%s): %s", source_id, exc)

        # 4. Local cache and staging deletion
        deleted_local_files = 0
        if storage_root:
            import shutil
            from pathlib import Path
            root_path = Path(storage_root)
            if root_path.is_dir():
                staging_dir = root_path / "staging"
                for t in tasks:
                    t_stage = staging_dir / f"task_{t['id']}"
                    if t_stage.exists():
                        shutil.rmtree(t_stage, ignore_errors=True)
                        deleted_local_files += 1
                for item in root_path.glob(f"*{source_id}*"):
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                        deleted_local_files += 1
                    elif item.is_file():
                        item.unlink(missing_ok=True)
                        deleted_local_files += 1

        return {
            "source_id": source_id,
            "title": title,
            "found_in_db": bool(manga_info),
            "deleted_channel_msgs": deleted_channel_msgs,
            "deleted_r2_count": deleted_r2_count,
            "deleted_chapters": len(chapters),
            "deleted_tasks": len(tasks),
            "deleted_local_files": deleted_local_files,
        }

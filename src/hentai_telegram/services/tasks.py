from hentai_telegram.db.store import Store, decode_manga
from hentai_telegram.domain.models import Manga
from hentai_telegram.providers.reference import parse_reference


class TaskService:
    def __init__(self, store: Store, provider, domains: tuple[str, ...] = (), allowed_user_ids: frozenset[int] | None = None):
        self.store = store
        self.provider = provider
        self.domains = domains
        self.allowed_user_ids = allowed_user_ids or frozenset()

    def authorize(self, user_id: int):
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            raise ValueError("抱歉，本机器人为私有频道管理机器人，仅管理员可用。")

    async def download(self, reference: str, user_id: int, chat_id: int, message_id: int | None = None) -> dict:
        self.authorize(user_id)
        source_id = parse_reference(reference, self.domains)
        self.store.check_submission(getattr(self.provider, "source", "jmcomic"), source_id, user_id)
        manga: Manga = await self.provider.get_manga(source_id)
        self.authorize(user_id)
        return self.store.submit(manga, user_id, chat_id, message_id=message_id)

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

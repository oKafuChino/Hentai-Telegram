import logging
import os
import re
import shutil
import time
from collections import deque
from html import escape

from hentai_telegram.services.users import UserService


class RecentLogs(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = deque(maxlen=100)
        self.secrets = tuple(v for k, v in os.environ.items() if v and
                             any(s in k.upper() for s in ("TOKEN", "PASSWORD", "SECRET", "COOKIE", "API_KEY")))
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            text = self.format(record)
            for secret in sorted(self.secrets, key=len, reverse=True):
                text = text.replace(secret, "[REDACTED]")
            text = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", text)
            text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
            text = re.sub(r"(?i)(password|token|secret|cookie|authorization)([\"']?\s*[:=]\s*)([^\s,}]+)",
                          r"\1\2[REDACTED]", text)
            self.lines.append(text.replace("\r", "\\r").replace("\n", "\\n")[:4000])
        except Exception:
            self.handleError(record)

    def snapshot(self) -> str:
        self.acquire()
        try:
            return "\n".join(self.lines) or "暂无日志"
        finally:
            self.release()


class Diagnostics:
    def __init__(self, users: UserService, worker, logs: RecentLogs, publisher=None):
        self.users, self.worker, self.logs, self.publisher = users, worker, logs, publisher
        self.started = time.monotonic()

    def recent_logs(self, actor: int) -> str:
        self.users.require_admin(actor)
        return self.logs.snapshot()

    async def status(self, actor: int) -> str:
        self.users.require_admin(actor)
        rows = self.worker.store.connection.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        ).fetchall()
        age = max(0, time.monotonic() - self.worker.heartbeat)
        try:
            disk = shutil.disk_usage(self.worker.root)
            disk_text = f"可用 {disk.free / 1024**3:.1f} / 总计 {disk.total / 1024**3:.1f} GiB"
        except OSError:
            disk_text = "无法读取"

        channel_status = "未配置"
        if self.publisher and self.publisher.channel_id:
            try:
                info = await self.publisher.verify_channel()
                channel_status = escape(f"正常 ({info.get('title', self.publisher.channel_id)})")
            except Exception as exc:
                channel_status = escape(f"异常 ({exc})")

        task_lines = "\n".join(f"• {r[0]}: {r[1]}" for r in rows) or "• 暂无任务"

        return (
            f"🩺 <b>Hentai-Telegram 系统状态</b>\n\n"
            f"⏱ 运行时长：{int(time.monotonic() - self.started)} 秒\n"
            f"⚙️ Worker：{'正常' if age < 30 else '心跳延迟'}（{age:.0f} 秒前）\n"
            f"💾 缓存磁盘：{disk_text}\n"
            f"📢 发布频道：{channel_status}\n\n"
            f"📊 <b>任务统计</b>：\n{task_lines}"
        )

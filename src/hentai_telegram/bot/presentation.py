import json
from html import escape

from hentai_telegram.services.channel_push import channel_post_link

STATUS = {
    "QUEUED": "⏳ 排队中",
    "DOWNLOADING": "📥 正在下载图片",
    "UPLOADING": "☁️ 正在转存至 Telegraph",
    "PUBLISHING": "📢 正在发布到 Telegram 频道",
    "COMPLETED": "✅ 已成功发布到频道",
    "FAILED": "❌ 任务失败",
    "RETRY_WAIT": "🔄 等待自动重试",
    "PAUSED_DISK": "⏸ 磁盘空间不足已暂停",
    "CANCEL_REQUESTED": "⏳ 正在取消任务",
    "CANCELLED": "🚫 任务已取消",
}


def describe_task(task: dict, channel_id: str = "") -> str:
    manga = json.loads(task["snapshot"]) if isinstance(task["snapshot"], str) else task["snapshot"]
    title = escape(str(manga.get("title", "未知标题"))[:200])
    source_id = escape(str(manga.get("source_id", "")))
    status_str = STATUS.get(task["status"], task["status"])

    lines = [
        f"📖 <b>{title}</b>",
        f"🆔 漫画 ID：<code>JM{source_id}</code>",
        f"📊 状态：{status_str}",
    ]

    if task.get("progress"):
        lines.append(f"⚡ 进度：{escape(str(task['progress']))}")

    if task.get("channel_message_id") and channel_id:
        link = channel_post_link(channel_id, task["channel_message_id"])
        lines.append(f'📢 频道消息：<a href="{escape(link, quote=True)}">点击查看频道推文</a>')

    if task.get("error"):
        lines.append(f"⚠️ 提示：{escape(str(task['error']))}")

    lines.extend([
        "",
        f"🔖 任务编号：<code>{escape(str(task['id']))}</code>",
    ])
    return "\n".join(lines)

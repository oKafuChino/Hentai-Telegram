import logging
from html import escape
from pathlib import Path

from aiogram import Bot

from hentai_telegram.domain.models import Manga

logger = logging.getLogger(__name__)


def channel_post_link(channel_id: str, message_id: int) -> str:
    target = channel_id.strip()
    if target.startswith("@"):
        return f"https://t.me/{target[1:]}/{message_id}"
    try:
        num = int(target)
        if num < 0:
            pure = abs(num)
            if str(pure).startswith("100"):
                pure = int(str(pure)[3:])
            return f"https://t.me/c/{pure}/{message_id}"
    except (ValueError, TypeError):
        pass
    return f"https://t.me/{target}/{message_id}"


class ChannelPublisher:
    def __init__(self, bot: Bot, channel_id: str):
        self.bot = bot
        self.channel_id = channel_id.strip()

    async def verify_channel(self) -> dict:
        """Verify that the bot has admin rights and can post to the target channel."""
        if not self.channel_id:
            raise ValueError("未配置 TELEGRAM_CHANNEL_ID")

        target = int(self.channel_id) if self.channel_id.lstrip("-").isdigit() else self.channel_id
        chat = await self.bot.get_chat(target)
        if chat.type != "channel":
            raise ValueError(f"目标必须是频道 (Channel)，当前类型为: {chat.type}")

        me = await self.bot.get_me()
        member = await self.bot.get_chat_member(chat.id, me.id)
        if member.status not in {"creator", "administrator"}:
            raise ValueError("机器人不是该频道的管理员，请先将机器人添加到频道并设为管理员")
        if member.status == "administrator" and not getattr(member, "can_post_messages", False):
            raise ValueError("机器人缺少频道的『发布消息』(can_post_messages) 权限，请在频道管理设置中开启")

        return {"id": chat.id, "title": chat.title, "username": chat.username}

    async def publish(
        self,
        manga: Manga,
        chapters: list[dict],
        cover_path: Path | None = None,
    ) -> int:
        if not self.channel_id:
            raise ValueError("未配置 TELEGRAM_CHANNEL_ID，无法发布至频道")

        target = int(self.channel_id) if self.channel_id.lstrip("-").isdigit() else self.channel_id
        raw_source = getattr(manga, "source", None) or "jmcomic"
        if raw_source.lower() in ("jmcomic", "jm"):
            source_name = "JMComic"
        else:
            source_name = raw_source

        authors_text = escape(", ".join(manga.authors) if manga.authors else "未知")
        tags_text = escape(", ".join(manga.tags[:15]) if manga.tags else "无")
        title_text = escape(manga.title)
        raw_sid = str(manga.source_id).strip()
        clean_sid = raw_sid[2:] if raw_sid.upper().startswith("JM") else raw_sid
        source_id_text = escape(clean_sid)

        caption_lines = [
            f"📚 <b>{title_text}</b>",
            f"📝 作者：{authors_text}",
            f"🏷 标签：{tags_text}",
            f"🆔 漫画 ID：<code>JM{source_id_text}</code>",
            f"📖 来源:{escape(source_name)}",
        ]

        # Chapter links in text (for multi-chapter manga)
        if len(chapters) > 1:
            caption_lines.append("")
            caption_lines.append("📑 <b>章节列表</b>：")
            if len(chapters) <= 12:
                for ch in chapters:
                    url = escape(ch.get("telegraph_url") or "#", quote=True)
                    ordinal = escape(str(ch.get("ordinal", 1)))
                    title = escape(ch.get("title", f"第 {ordinal} 话"))
                    caption_lines.append(f'• <a href="{url}">第 {ordinal} 话：{title}</a>')
            else:
                if manga.index_url:
                    idx_url = escape(manga.index_url, quote=True)
                    caption_lines.append(f'• <a href="{idx_url}">📑 查看全本完整目录（共 {len(chapters)} 话）</a>')
                for ch in chapters[:5]:
                    url = escape(ch.get("telegraph_url") or "#", quote=True)
                    ordinal = escape(str(ch.get("ordinal", 1)))
                    title = escape(ch.get("title", f"第 {ordinal} 话"))
                    caption_lines.append(f'• <a href="{url}">第 {ordinal} 话：{title}</a>')
                caption_lines.append("• ... （更多章节请查看完整目录）")
                for ch in chapters[-3:]:
                    url = escape(ch.get("telegraph_url") or "#", quote=True)
                    ordinal = escape(str(ch.get("ordinal", 1)))
                    title = escape(ch.get("title", f"第 {ordinal} 话"))
                    caption_lines.append(f'• <a href="{url}">第 {ordinal} 话：{title}</a>')

        # Safely constrain caption length to avoid breaking HTML tags or exceeding Telegram's 1024 limit
        safe_lines = []
        current_len = 0
        limit = 950
        truncated = False
        for line in caption_lines:
            if current_len + len(line) + 1 > limit and not truncated:
                if manga.index_url:
                    idx_url = escape(manga.index_url, quote=True)
                    safe_lines.append(f'• <a href="{idx_url}">📑 更多章节详见全本目录...</a>')
                else:
                    safe_lines.append("• ... (更多章节请在线阅读)")
                truncated = True
                break
            safe_lines.append(line)
            current_len += len(line) + 1

        caption = "\n".join(safe_lines)

        # Determine primary Telegraph URL to generate Instant View card
        preview_url = None
        if chapters and chapters[0].get("telegraph_url"):
            preview_url = chapters[0]["telegraph_url"]
        elif manga.index_url:
            preview_url = manga.index_url

        from aiogram.types import LinkPreviewOptions
        link_preview_options = LinkPreviewOptions(
            is_disabled=False,
            url=preview_url,
            prefer_large_media=True,
            show_above_text=True,
        )

        for attempt in range(3):
            try:
                msg = await self.bot.send_message(
                    chat_id=target,
                    text=caption,
                    parse_mode="HTML",
                    link_preview_options=link_preview_options,
                )
                logger.info("manga_published_to_channel manga=%s message_id=%s", manga.source_id, msg.message_id)
                return msg.message_id
            except Exception as exc:
                if attempt == 2:
                    raise
                logger.warning("publish_channel_attempt_failed error=%s attempt=%s", exc, attempt)
                import asyncio
                await asyncio.sleep(2)

        raise RuntimeError("发布到频道失败")

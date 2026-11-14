import logging
from pathlib import Path
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

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
        authors = ", ".join(manga.authors) if manga.authors else "未知"
        tags = ", ".join(manga.tags[:15]) if manga.tags else "无"

        caption_lines = [
            f"📚 <b>{manga.title}</b>",
            f"👨‍🎨 作者：{authors}",
            f"🏷 标签：{tags}",
            f"🔢 漫画 ID：<code>JM{manga.source_id}</code>",
            "",
            "📖 <b>在线阅读（Telegraph）</b>：",
        ]

        # Chapter links in text
        if len(chapters) == 1:
            url = chapters[0].get("telegraph_url") or "#"
            caption_lines.append(f'• <a href="{url}">点击在线阅读全文</a>')
        elif len(chapters) <= 12:
            for ch in chapters:
                url = ch.get("telegraph_url") or "#"
                ordinal = ch.get("ordinal", 1)
                title = ch.get("title", f"第 {ordinal} 话")
                caption_lines.append(f'• <a href="{url}">第 {ordinal} 话：{title}</a>')
        else:
            if manga.index_url:
                caption_lines.append(f'• <a href="{manga.index_url}">📑 查看全本完整目录（共 {len(chapters)} 话）</a>')
            for ch in chapters[:5]:
                url = ch.get("telegraph_url") or "#"
                caption_lines.append(f'• <a href="{url}">第 {ch["ordinal"]} 话：{ch["title"]}</a>')
            caption_lines.append("• ... （更多章节请查看完整目录）")
            for ch in chapters[-3:]:
                url = ch.get("telegraph_url") or "#"
                caption_lines.append(f'• <a href="{url}">第 {ch["ordinal"]} 话：{ch["title"]}</a>')

        caption = "\n".join(caption_lines)
        if len(caption) > 1024:
            caption = caption[:1000] + "...\n(详见目录链接)"

        # Inline Keyboard
        buttons: list[list[InlineKeyboardButton]] = []
        if len(chapters) == 1:
            url = chapters[0].get("telegraph_url") or "#"
            buttons.append([InlineKeyboardButton(text="📖 点击在线阅读", url=url)])
        else:
            if manga.index_url:
                buttons.append([InlineKeyboardButton(text="📑 查看全本目录", url=manga.index_url)])
            row: list[InlineKeyboardButton] = []
            for ch in chapters[:8]:
                url = ch.get("telegraph_url") or "#"
                row.append(InlineKeyboardButton(text=f"第 {ch['ordinal']} 话", url=url))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

        # Cover photo
        photo = None
        if cover_path and cover_path.is_file():
            try:
                photo = BufferedInputFile(cover_path.read_bytes(), filename="cover.jpg")
            except OSError as exc:
                logger.warning("read_cover_failed error=%s", exc)

        for attempt in range(3):
            try:
                if photo:
                    msg = await self.bot.send_photo(
                        chat_id=target,
                        photo=photo,
                        caption=caption,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    msg = await self.bot.send_message(
                        chat_id=target,
                        text=caption,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=False,
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

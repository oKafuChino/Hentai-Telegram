"""Telegram main channel push module with native Instant View generation using aiogram."""

import asyncio
import html
from collections.abc import Sequence

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import LinkPreviewOptions

try:
    from src.config import Settings
    from src.utils import setup_logger
except ImportError:
    from config import Settings
    from utils import setup_logger

logger = setup_logger("tg_bot")


class TelegramNotifier:
    """Handles structured channel publication with native Instant View card generation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.tg_bot_token
        self.main_chat_id = settings.tg_main_chat_id

    def is_configured(self) -> bool:
        """Check if Telegram Bot token and main channel are provided."""
        return bool(self.token and self.main_chat_id)

    async def publish_to_main_channel(
        self,
        bot: Bot,
        title: str,
        chapter_num: str,
        telegraph_urls: Sequence[str],
        tags: Sequence[str] | None = None,
        cover_url: str | None = None,
    ) -> None:
        """Publish structured announcement message directly to main channel with Instant View card."""
        if not self.main_chat_id:
            logger.info("未配置 TG_MAIN_CHAT_ID，跳过主频道推送")
            return

        escaped_title = html.escape(title)
        lines = [
            f"📖 <b>{escaped_title}</b>",
            f"🔢 <b>话数</b>: 第 {html.escape(str(chapter_num))} 话",
        ]

        if tags:
            valid_tags = [t.strip() for t in tags if t.strip()]
            if valid_tags:
                tag_str = " ".join(f"#{html.escape(t)}" for t in valid_tags)
                lines.append(f"🏷 <b>标签</b>: {tag_str}")

        lines.append("")
        lines.append("⚡ <b>即时阅读 (Instant View)</b>:")

        num_urls = len(telegraph_urls)
        if num_urls == 1:
            lines.append(f'👉 <a href="{html.escape(telegraph_urls[0], quote=True)}">点击即时阅读</a>')
        else:
            for idx, u in enumerate(telegraph_urls, 1):
                if num_urls == 2:
                    label = "上卷" if idx == 1 else "下卷"
                elif num_urls == 3:
                    label = ["上卷", "中卷", "下卷"][idx - 1]
                else:
                    label = f"第 {idx}/{num_urls} 卷"
                lines.append(f'👉 <a href="{html.escape(u, quote=True)}">{label} 点击阅读</a>')

        caption = "\n".join(lines)

        # Pin preview to the first Telegraph URL to generate native Instant View card
        primary_url = telegraph_urls[0] if telegraph_urls else None
        link_preview_options = LinkPreviewOptions(
            is_disabled=False,
            url=primary_url,
            prefer_large_media=True,
            show_above_text=True,
        )

        logger.info(f"向正式频道 [{self.main_chat_id}] 直接推送消息并生成 Instant View...")
        await bot.send_message(
            chat_id=self.main_chat_id,
            text=caption,
            link_preview_options=link_preview_options,
        )
        logger.info("✅ 频道消息推送成功，Instant View 即时预览已生成！")

    async def publish(
        self,
        title: str,
        chapter_num: str,
        telegraph_urls: Sequence[str],
        tags: Sequence[str] | None = None,
        cover_url: str | None = None,
    ) -> None:
        """Directly publish announcement to main channel."""
        if not self.is_configured():
            logger.warning("Telegram Bot 未完整配置 (缺少 TG_BOT_TOKEN 或 TG_MAIN_CHAT_ID)，跳过 Telegram 推送")
            return

        bot = Bot(token=self.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await self.publish_to_main_channel(
                bot,
                title=title,
                chapter_num=chapter_num,
                telegraph_urls=telegraph_urls,
                tags=tags,
                cover_url=cover_url,
            )
        finally:
            await bot.session.close()


def send_channel_notification_sync(
    settings: Settings,
    title: str,
    chapter_num: str,
    telegraph_urls: Sequence[str],
    tags: Sequence[str] | None = None,
    cover_url: str | None = None,
) -> None:
    """Synchronous wrapper for publishing to main channel."""
    notifier = TelegramNotifier(settings)
    if not notifier.is_configured():
        logger.info("未配置 Telegram 凭据，跳过频道通知")
        return
    asyncio.run(
        notifier.publish(
            title=title,
            chapter_num=chapter_num,
            telegraph_urls=telegraph_urls,
            tags=tags,
            cover_url=cover_url,
        )
    )

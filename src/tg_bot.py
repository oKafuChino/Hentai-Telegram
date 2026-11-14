"""Telegram Instant View prewarming and channel push module using aiogram."""

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
    """Handles Instant View cache prewarming and structured channel publication."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.tg_bot_token
        self.cache_chat_id = settings.tg_cache_chat_id
        self.main_chat_id = settings.tg_main_chat_id
        self.prewarm_delay = max(0, settings.prewarm_delay_seconds)

    def is_configured(self) -> bool:
        """Check if Telegram Bot token and main channel are provided."""
        return bool(self.token and self.main_chat_id)

    async def prewarm_instant_view(self, bot: Bot, telegraph_urls: Sequence[str]) -> None:
        """Send links to private cache channel to trigger Telegram Instant View crawler."""
        if not self.cache_chat_id:
            logger.info("未配置 TG_CACHE_CHAT_ID，跳过 Instant View 预热")
            return

        logger.info(f"正在向缓存频道 [{self.cache_chat_id}] 发送预热链接...")
        for url in telegraph_urls:
            try:
                await bot.send_message(
                    chat_id=self.cache_chat_id,
                    text=f"🔄 Instant View Prewarm:\n{url}",
                    link_preview_options=LinkPreviewOptions(is_disabled=False),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"预热发送失败 [{url}]: {e}")

        logger.info(f"等待 {self.prewarm_delay} 秒供 Telegram 爬虫抓取 R2 图片并建立永久 Instant View 缓存...")
        await asyncio.sleep(self.prewarm_delay)

    async def publish_to_main_channel(
        self,
        bot: Bot,
        title: str,
        chapter_num: str,
        telegraph_urls: Sequence[str],
        tags: Sequence[str] | None = None,
        cover_url: str | None = None,
    ) -> None:
        """Publish final structured announcement message to main channel."""
        if not self.main_chat_id:
            logger.info("未配置 TG_MAIN_CHAT_ID，跳过主频道推送")
            return

        escaped_title = html.escape(title)
        lines = [
            f"📖 <b>{escaped_title}</b>",
            f"🔢 <b>话数</b>: 第 {chapter_num} 话",
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
            lines.append(f"👉 <a href=\"{telegraph_urls[0]}\">点击即时阅读</a>")
        else:
            for idx, u in enumerate(telegraph_urls, 1):
                if num_urls == 2:
                    label = "上卷" if idx == 1 else "下卷"
                elif num_urls == 3:
                    label = ["上卷", "中卷", "下卷"][idx - 1]
                else:
                    label = f"第 {idx}/{num_urls} 卷"
                lines.append(f"👉 <a href=\"{u}\">{label} 点击阅读</a>")

        caption = "\n".join(lines)

        logger.info(f"向正式频道 [{self.main_chat_id}] 推送排版消息...")
        if cover_url:
            try:
                await bot.send_photo(
                    chat_id=self.main_chat_id,
                    photo=cover_url,
                    caption=caption,
                )
                logger.info("✅ 频道图文消息推送成功！")
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"封面图文消息推送失败，降级为纯文本消息: {e}")

        await bot.send_message(
            chat_id=self.main_chat_id,
            text=caption,
            link_preview_options=LinkPreviewOptions(is_disabled=False),
        )
        logger.info("✅ 频道消息推送成功！")

    async def prewarm_and_publish(
        self,
        title: str,
        chapter_num: str,
        telegraph_urls: Sequence[str],
        tags: Sequence[str] | None = None,
        cover_url: str | None = None,
    ) -> None:
        """Run complete Telegram flow: prewarm cache followed by main channel publication."""
        if not self.is_configured():
            logger.warning("Telegram Bot 未完整配置 (缺少 TG_BOT_TOKEN 或 TG_MAIN_CHAT_ID)，跳过 Telegram 推送")
            return

        bot = Bot(token=self.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await self.prewarm_instant_view(bot, telegraph_urls)
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
    """Synchronous wrapper for running prewarm and publish in an event loop."""
    notifier = TelegramNotifier(settings)
    if not notifier.is_configured():
        logger.info("未配置 Telegram 凭据，跳过频道通知")
        return
    asyncio.run(
        notifier.prewarm_and_publish(
            title=title,
            chapter_num=chapter_num,
            telegraph_urls=telegraph_urls,
            tags=tags,
            cover_url=cover_url,
        )
    )

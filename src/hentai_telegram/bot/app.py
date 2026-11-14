import asyncio
import logging
from html import escape

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from hentai_telegram.bot.access import parse_command
from hentai_telegram.bot.commands import configure_commands
from hentai_telegram.bot.presentation import describe_task
from hentai_telegram.domain.errors import JobError
from hentai_telegram.services.channel_push import ChannelPublisher
from hentai_telegram.services.diagnostics import Diagnostics
from hentai_telegram.services.tasks import TaskService

logger = logging.getLogger(__name__)


class AdminAuthorization(BaseMiddleware):
    def __init__(self, allowed_user_ids: frozenset[int]):
        self.allowed_user_ids = allowed_user_ids
        self.bot_username: str | None = None

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        callback = isinstance(event, CallbackQuery)
        message = event.message if callback else event
        chat = getattr(message, "chat", None)

        if not user or user.is_bot or not chat:
            return

        # Check bot username if command has @mention
        text = getattr(event, "text", "") or ""
        command, recipient, _ = parse_command(text)
        if command.startswith("/") and recipient:
            if self.bot_username is None:
                self.bot_username = (await data["bot"].get_me()).username or ""
            if recipient.lower() != self.bot_username.lower():
                return

        # Restrict to configured admin IDs only
        if user.id not in self.allowed_user_ids:
            if callback:
                await event.answer("⚠️ 仅管理员可用", show_alert=True)
            elif chat.type == "private":
                await message.answer("⚠️ 抱歉，本机器人为私有频道管理机器人，仅管理员可用。")
            return

        return await handler(event, data)


def create_dispatcher(
    service: TaskService,
    diagnostics: Diagnostics,
    publisher: ChannelPublisher,
    allowed_user_ids: frozenset[int],
    channel_id: str = "",
    s3_client=None,
    storage_root=None,
) -> Dispatcher:
    router = Router()
    router.message.outer_middleware(AdminAuthorization(allowed_user_ids))
    router.callback_query.outer_middleware(AdminAuthorization(allowed_user_ids))

    @router.message(Command("start", "help"))
    async def start_handler(message: Message):
        text = (
            "🤖 <b>Hentai-Telegram 频道搬运机器人</b>\n\n"
            "本机器人负责将禁漫漫画抓取并转存至 <b>Telegraph (Instant View)</b>，最后自动发布到指定的 Telegram 频道。\n\n"
            "📌 <b>常用指令</b>：\n"
            "• <code>/jm &lt;ID或链接&gt;</code>：抓取漫画并发布到频道\n"
            "• <code>/del &lt;ID或链接&gt;</code>：删除漫画（清理缓存、R2存储与频道消息）\n"
            "• <code>/tasks</code>：查看正在处理和排队的任务\n"
            "• <code>/cancel &lt;任务ID&gt;</code>：取消指定任务\n"
            "• <code>/retry &lt;任务ID&gt;</code>：重试失败的任务\n"
            "• <code>/channel</code>：检查目标频道状态与发布权限\n"
            "• <code>/status</code>：查看系统运行状态与任务统计\n"
            "• <code>/logs</code>：查看最近系统日志\n\n"
            "💡 <b>提示</b>：在私聊中直接发送漫画数字 ID 或链接，机器人将自动为您入队！"
        )
        await message.answer(text, parse_mode="HTML")

    @router.message(Command("channel"))
    async def channel_handler(message: Message):
        msg = await message.answer("⏳ 正在检查目标频道状态与权限...")
        try:
            info = await publisher.verify_channel()
            await msg.edit_text(
                f"✅ <b>频道检查正常</b>\n\n"
                f"• 频道名称：{escape(info.get('title', '未知'))}\n"
                f"• 频道 ID：<code>{info.get('id')}</code>\n"
                f"• 用户名：@{info.get('username') or '私有频道'}\n"
                f"• 发布权限：已具备管理员发帖权限",
                parse_mode="HTML",
            )
        except Exception as exc:
            await msg.edit_text(f"❌ <b>频道检查失败</b>\n\n原因：{escape(str(exc))}", parse_mode="HTML")

    @router.message(Command("status"))
    async def status_handler(message: Message):
        text = await diagnostics.status(message.from_user.id)
        await message.answer(text, parse_mode="HTML")

    @router.message(Command("logs"))
    async def logs_handler(message: Message):
        logs = diagnostics.recent_logs(message.from_user.id)
        if len(logs) > 3800:
            logs = logs[-3800:]
        await message.answer(f"<pre>{escape(logs)}</pre>", parse_mode="HTML")

    @router.message(Command("tasks"))
    async def tasks_handler(message: Message):
        tasks = service.list_tasks(message.from_user.id, limit=10, active_only=True)
        if not tasks:
            await message.answer("☕ 当前暂无正在运行或排队的任务。\n发送 <code>/jm &lt;ID&gt;</code> 开始下载！", parse_mode="HTML")
            return

        lines = [f"📋 <b>当前活动任务 ({len(tasks)})</b>\n"]
        buttons = []
        for t in tasks:
            lines.append(f"• <code>{t['id'][:8]}</code> | JM{t.get('jm_id', '')} | {t['status']}")
            if t["progress"]:
                lines.append(f"  └ <i>{escape(t['progress'])}</i>")
            buttons.append([InlineKeyboardButton(text=f"⏹ 取消 {t['id'][:8]}", callback_data=f"cancel:{t['id']}")])

        markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        await message.answer("\n".join(lines), reply_markup=markup, parse_mode="HTML")

    @router.message(Command("cancel"))
    async def cancel_command_handler(message: Message):
        _, _, arg = parse_command(message.text or "")
        if not arg.strip():
            await message.answer("请输入任务 ID，例如：<code>/cancel 12345678</code>", parse_mode="HTML")
            return
        task_id = arg.strip()
        try:
            service.cancel(task_id, message.from_user.id)
            await message.answer(f"⏳ 已提交取消请求：<code>{task_id}</code>", parse_mode="HTML")
        except Exception as exc:
            await message.answer(f"❌ 取消失败: {escape(str(exc))}")

    @router.message(Command("retry"))
    async def retry_command_handler(message: Message):
        _, _, arg = parse_command(message.text or "")
        if not arg.strip():
            await message.answer("请输入任务 ID，例如：<code>/retry 12345678</code>", parse_mode="HTML")
            return
        task_id = arg.strip()
        try:
            new_task = service.retry(task_id, message.from_user.id)
            await message.answer(f"🔄 任务已重新入队：<code>{new_task['id']}</code>", parse_mode="HTML")
        except Exception as exc:
            await message.answer(f"❌ 重试失败: {escape(str(exc))}")

    @router.message(Command("del", "delete", "rm"))
    async def del_command_handler(message: Message):
        _, _, arg = parse_command(message.text or "")
        if not arg.strip():
            await message.answer(
                "请输入要删除的漫画 ID 或链接，例如：\n<code>/del 123456</code> 或 <code>/del JM123456</code>",
                parse_mode="HTML",
            )
            return

        ref = arg.strip()
        status_msg = await message.answer(f"⏳ 正在移除漫画 <code>{escape(ref)}</code> 及其缓存、R2 存储与频道消息...", parse_mode="HTML")
        try:
            bot = message.bot
            res = await service.delete(
                reference=ref,
                user_id=message.from_user.id,
                bot=bot,
                channel_id=channel_id,
                s3_client=s3_client,
                storage_root=storage_root,
            )

            lines = [
                "🗑 <b>漫画删除成功</b>\n",
                f"• <b>漫画标题</b>：{escape(res['title'])}",
                f"• <b>漫画 ID</b>：<code>JM{res['source_id']}</code>",
                f"• <b>频道消息</b>：已删除 {res['deleted_channel_msgs']} 条",
                f"• <b>R2 对象存储</b>：已清理 {res['deleted_r2_count']} 个文件",
                f"• <b>数据库记录</b>：已清理 {res['deleted_chapters']} 个章节 / {res['deleted_tasks']} 个任务",
            ]
            if not res["found_in_db"]:
                lines.append("\n<i>（提示：该漫画此前未在数据库中登记或已被清理）</i>")

            await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            logger.exception("delete_manga_failed ref=%s", ref)
            await status_msg.edit_text(f"❌ <b>删除失败</b>\n原因：{escape(str(exc))}", parse_mode="HTML")


    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel_callback(query: CallbackQuery):
        task_id = query.data.split(":", 1)[1]
        try:
            service.cancel(task_id, query.from_user.id)
            await query.answer("已请求取消任务", show_alert=True)
        except Exception as exc:
            await query.answer(f"取消失败: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("retry:"))
    async def retry_callback(query: CallbackQuery):
        task_id = query.data.split(":", 1)[1]
        try:
            new_task = service.retry(task_id, query.from_user.id)
            await query.answer("任务已重新入队", show_alert=True)
            await query.message.answer(f"🔄 任务已重新入队：<code>{new_task['id']}</code>", parse_mode="HTML")
        except Exception as exc:
            await query.answer(f"重试失败: {exc}", show_alert=True)

    async def handle_submission(message: Message, reference: str, force: bool = False):
        ref_clean = reference.strip()
        reply = await message.answer(f"⏳ 正在查询漫画 <code>{escape(ref_clean)}</code> 元数据...", parse_mode="HTML")
        try:
            task = await service.download(
                reference=ref_clean,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                message_id=reply.message_id,
                force=force,
            )
            cancel_btn = [[InlineKeyboardButton(text="⏹ 取消下载", callback_data=f"cancel:{task['id']}")]]
            await reply.edit_text(
                describe_task(task, channel_id),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=cancel_btn),
                parse_mode="HTML",
            )
        except (ValueError, JobError) as exc:
            await reply.edit_text(f"❌ <b>提交失败</b>\n{escape(str(exc))}", parse_mode="HTML")
        except Exception as exc:
            logger.exception("submit_error reference=%s exc=%s", reference, exc)
            await reply.edit_text(f"❌ <b>提交失败</b>\n系统错误: {escape(str(exc))}", parse_mode="HTML")

    @router.message(Command("jm", "download"))
    async def jm_command_handler(message: Message):
        _, _, arg = parse_command(message.text or "")
        if not arg.strip():
            await message.answer(
                "请输入漫画 ID 或链接，例如：\n<code>/jm 123456</code>\n如需强制重新抓取已完成的漫画：\n<code>/jm 123456 force</code>",
                parse_mode="HTML",
            )
            return
        parts = arg.strip().split()
        force = False
        ref_tokens = []
        for p in parts:
            if p.lower() in ("force", "-f", "--force", "强制"):
                force = True
            else:
                ref_tokens.append(p)
        ref = " ".join(ref_tokens) if ref_tokens else parts[0]
        await handle_submission(message, ref, force=force)

    @router.message(F.text)
    async def text_handler(message: Message):
        text = (message.text or "").strip()
        if text.startswith("/"):
            return
        # If admin pastes a number or supported url in private chat
        parts = text.split()
        force = False
        ref_tokens = []
        for p in parts:
            if p.lower() in ("force", "-f", "--force", "强制"):
                force = True
            else:
                ref_tokens.append(p)
        ref = " ".join(ref_tokens) if ref_tokens else text
        lower = ref.lower()
        if ref.isdigit() or lower.startswith("jm") or "comic" in lower or "album" in lower or "photo" in lower or "http" in lower:
            await handle_submission(message, ref, force=force)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.startup.register(configure_commands)
    return dispatcher


async def progress_updater(bot: Bot, store, channel_id: str):
    """Background task to update progress messages in admin chats."""
    last_text: dict[str, str] = {}
    completed_tasks: set[str] = set()

    while True:
        try:
            # 1. Update active tasks
            for task in store.tasks(limit=30, active_only=True):
                if not task.get("message_id") or not task.get("chat_id"):
                    continue
                text = describe_task(task, channel_id)
                if last_text.get(task["id"]) == text:
                    continue
                try:
                    cancel_btn = [[InlineKeyboardButton(text="⏹ 取消下载", callback_data=f"cancel:{task['id']}")]]
                    await bot.edit_message_text(
                        text=text,
                        chat_id=task["chat_id"],
                        message_id=task["message_id"],
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=cancel_btn),
                        parse_mode="HTML",
                    )
                    last_text[task["id"]] = text
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(min(exc.retry_after, 30))
                    break
                except TelegramBadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        last_text[task["id"]] = text
                except Exception:
                    pass

            # 2. Update recently finished tasks once
            for task in store.tasks(limit=20):
                if task["id"] in completed_tasks:
                    continue
                if task["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    if task.get("message_id") and task.get("chat_id"):
                        text = describe_task(task, channel_id)
                        try:
                            retry_btn = []
                            if task["status"] == "FAILED":
                                retry_btn = [[InlineKeyboardButton(text="🔄 重试任务", callback_data=f"retry:{task['id']}")]]
                            await bot.edit_message_text(
                                text=text,
                                chat_id=task["chat_id"],
                                message_id=task["message_id"],
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=retry_btn) if retry_btn else None,
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                    completed_tasks.add(task["id"])
        except Exception as exc:
            logger.warning("progress_updater_exception: %s", exc)

        await asyncio.sleep(3)

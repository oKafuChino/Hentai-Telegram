import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault

logger = logging.getLogger(__name__)

COMMANDS = (
    ("start", "启动并查看使用说明"),
    ("help", "使用帮助与指令说明"),
    ("jm", "下载并发布到频道：/jm 123456"),
    ("tasks", "查看当前任务列表"),
    ("cancel", "取消任务：/cancel 任务ID"),
    ("retry", "重试任务：/retry 任务ID"),
    ("channel", "检查目标频道状态与权限"),
    ("status", "查看系统运行状态"),
    ("logs", "查看最近系统日志"),
)


async def configure_commands(bot: Bot):
    commands = [BotCommand(command=cmd, description=desc) for cmd, desc in COMMANDS]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    except Exception as exc:
        logger.warning("configure_commands_failed error=%s", exc)

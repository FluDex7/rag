from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Начало работы"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="add_qa", description="Добавить Q&A (senior, admin)"),
        BotCommand(command="admin", description="Панель управления (admin)"),
    ]
    await bot.set_my_commands(commands, BotCommandScopeDefault())

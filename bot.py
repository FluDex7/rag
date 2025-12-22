import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import config
from utils.logger import setup_logging
from database.async_db import init_db_async
from handlers import user_handlers

# Настраиваем логирование
setup_logging()
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
        
    # Проверяем токен
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    # Инициализируем базу данных (asyncpg)
    logger.info("Инициализация базы данных (asyncpg)...")
    try:
        await init_db_async()
        logger.info("База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД (asyncpg): {e}")
        return
    
    # Создаем бота и диспетчер
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем роутеры
    dp.include_router(user_handlers.router)
    
    logger.info("Бот успешно инициализирован")
    
    # Запускаем polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)


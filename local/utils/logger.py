import logging
import sys
from pathlib import Path

import config

# Создаем директорию для логов
config.LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    """Настройка системы логирования"""

    # Формат логов
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Уровень логирования
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    # Настройка root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Очищаем существующие handlers
    root_logger.handlers.clear()

    # Handler для файла
    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8", mode="a")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Handler для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Добавляем handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Уменьшаем уровень логирования для некоторых библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)

    logging.info("Логирование успешно настроено")
    logging.info(f"Логи сохраняются в: {config.LOG_FILE}")

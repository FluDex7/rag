from __future__ import annotations

import asyncpg
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

import config
import logging

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_db_async() -> None:
    """
    Инициализация пула соединений и создание таблиц (если их еще нет).
    """
    global _pool

    if _pool is not None:
        return

    logger.info("Создание пула соединений asyncpg...")
    _pool = await asyncpg.create_pool(dsn=config.DATABASE_URL, min_size=1, max_size=10)

    async with _pool.acquire() as conn:
        # Включаем расширение для работы с таймстампами (на всякий случай)
        await conn.execute("SET TIME ZONE 'UTC';")

        # Создаем таблицы, если их нет
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(100),
                full_name VARCHAR(255),
                role VARCHAR(20) NOT NULL DEFAULT 'junior',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP NULL,
                consultation_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogs (
                dialog_id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                llm_model_used VARCHAR(50),
                total_tokens_used INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id BIGSERIAL PRIMARY KEY,
                dialog_id BIGINT NOT NULL REFERENCES dialogs(dialog_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                context TEXT NOT NULL,
                sender_type VARCHAR(10) NOT NULL,
                sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                telegram_message_id BIGINT,
                tokens_used INTEGER NOT NULL DEFAULT 0
            );
            """
        )

    logger.info("asyncpg: пул соединений создан, таблицы проверены")


@asynccontextmanager
async def get_conn():
    """
    Асинхронный контекстный менеджер для получения соединения.
    """
    global _pool
    if _pool is None:
        await init_db_async()

    assert _pool is not None
    conn = await _pool.acquire()
    try:
        yield conn
    finally:
        await _pool.release(conn)


async def get_or_create_user(
    telegram_id: int,
    username: Optional[str],
    full_name: Optional[str],
) -> Dict[str, Any]:
    """
    Получить пользователя по telegram_id или создать нового.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (telegram_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE
                SET username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name
            RETURNING *;
            """,
            telegram_id,
            username,
            full_name,
        )
        return dict(row)


async def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1;",
            telegram_id,
        )
        return dict(row) if row else None


async def ensure_senior_or_admin(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Проверка, что пользователь существует и имеет роль senior или admin.
    """
    user = await get_user_by_telegram_id(telegram_id)
    if not user:
        return None
    if user["role"] not in ("senior", "admin"):
        return None
    if user["is_blocked"]:
        return None
    return user


async def create_or_get_active_dialog(user_id: int, llm_model_used: str = "gpt-4") -> Dict[str, Any]:
    """
    Получить активный диалог пользователя или создать новый.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM dialogs
            WHERE user_id = $1 AND status = 'active'
            ORDER BY started_at DESC
            LIMIT 1;
            """,
            user_id,
        )
        if row:
            return dict(row)

        row = await conn.fetchrow(
            """
            INSERT INTO dialogs (user_id, status, llm_model_used)
            VALUES ($1, 'active', $2)
            RETURNING *;
            """,
            user_id,
            llm_model_used,
        )
        return dict(row)


async def save_message(
    dialog_id: int,
    user_id: int,
    text: str,
    sender_type: str,
    telegram_message_id: Optional[int] = None,
    tokens_used: int = 0,
) -> Dict[str, Any]:
    """
    Сохранение сообщения в таблицу messages.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO messages (dialog_id, user_id, context, sender_type, telegram_message_id, tokens_used)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *;
            """,
            dialog_id,
            user_id,
            text,
            sender_type,
            telegram_message_id,
            tokens_used,
        )
        return dict(row)


async def update_user_stats(user_id: int, last_activity, increment_consultation: bool = False) -> None:
    """
    Обновление статистики пользователя.
    """
    async with get_conn() as conn:
        if increment_consultation:
            await conn.execute(
                """
                UPDATE users
                SET consultation_count = consultation_count + 1,
                    last_activity = $2
                WHERE user_id = $1;
                """,
                user_id,
                last_activity,
            )
        else:
            await conn.execute(
                """
                UPDATE users
                SET last_activity = $2
                WHERE user_id = $1;
                """,
                user_id,
                last_activity,
            )


async def add_tokens_to_dialog(dialog_id: int, tokens_used: int) -> None:
    """
    Увеличить счетчик токенов в диалоге.
    """
    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE dialogs
            SET total_tokens_used = total_tokens_used + $2
            WHERE dialog_id = $1;
            """,
            dialog_id,
            tokens_used,
        )


async def update_user_role(telegram_id: int, role: str) -> bool:
    """
    Обновить роль пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        role: Новая роль (junior, senior, admin)
    
    Returns:
        True если успешно обновлено
    """
    if role not in ("junior", "senior", "admin"):
        return False
    
    async with get_conn() as conn:
        result = await conn.execute(
            """
            UPDATE users
            SET role = $2
            WHERE telegram_id = $1;
            """,
            telegram_id,
            role,
        )
        return result == "UPDATE 1"


async def toggle_user_block(telegram_id: int, is_blocked: bool) -> bool:
    """
    Заблокировать/разблокировать пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        is_blocked: True для блокировки, False для разблокировки
    
    Returns:
        True если успешно обновлено
    """
    async with get_conn() as conn:
        result = await conn.execute(
            """
            UPDATE users
            SET is_blocked = $2
            WHERE telegram_id = $1;
            """,
            telegram_id,
            is_blocked,
        )
        return result == "UPDATE 1"


async def get_all_users(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Получить список всех пользователей
    
    Args:
        limit: Максимальное количество пользователей
    
    Returns:
        Список пользователей
    """
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, telegram_id, username, full_name, role, 
                   is_active, is_blocked, created_at, last_activity, consultation_count
            FROM users
            ORDER BY created_at DESC
            LIMIT $1;
            """,
            limit,
        )
        return [dict(row) for row in rows]


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """
    Получить пользователя по username
    
    Args:
        username: Username пользователя (без @)
    
    Returns:
        Словарь с данными пользователя или None
    """
    # Убираем @ если есть
    username = username.lstrip('@')
    
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, telegram_id, username, full_name, role, 
                   is_active, is_blocked, created_at, last_activity, consultation_count
            FROM users
            WHERE username = $1;
            """,
            username,
        )
        return dict(row) if row else None



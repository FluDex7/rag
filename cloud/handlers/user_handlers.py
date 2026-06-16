import asyncio
import logging
from datetime import datetime
from pathlib import Path

import config
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from database.async_db import (
    add_tokens_to_dialog,
    create_or_get_active_dialog,
    ensure_senior_or_admin,
    get_all_users,
    get_or_create_user,
    get_user_by_telegram_id,
    get_user_by_username,
    save_message,
    toggle_user_block,
    update_user_role,
    update_user_stats,
)
from services.pdf_processor import process_pdf_and_upload_to_qdrant
from services.qdrant_service import QdrantService
from services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = Router()
rag_service = RAGService()
qdrant_service = QdrantService()


class AddQAStates(StatesGroup):
    """Состояния для добавления Q&A"""

    waiting_question = State()
    waiting_answer = State()


class AdminStates(StatesGroup):
    """Состояния для админ-панели"""

    waiting_pdf = State()
    waiting_user_selection_method = State()
    waiting_telegram_id_for_role = State()
    waiting_username_for_role = State()
    waiting_role_selection = State()
    waiting_telegram_id_for_block = State()
    waiting_username_for_block = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    try:
        # Создаем или обновляем пользователя
        user = await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
            or f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip(),
        )

        # Обновляем last_activity
        await update_user_stats(
            user_id=user["user_id"], last_activity=datetime.utcnow()
        )

        # Приветственное сообщение
        welcome_text = """👋 Добро пожаловать!

Я помогу разобраться в вопросах налогового законодательства РФ — просто напишите свой вопрос, и я найду ответ на основе Налогового кодекса.

Доступные команды:
/help - справка
/add_qa - добавить вопрос-ответ в базу знаний (senior, admin)"""

        await message.answer(welcome_text)
        logger.info(f"Пользователь {message.from_user.id} запустил бота")

    except Exception as e:
        logger.error(f"Ошибка в cmd_start: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = """📚 Справка

Я консультирую по Налоговому кодексу РФ. Просто напишите вопрос — я найду релевантные статьи и дам развёрнутый ответ.

Команды:
/help - эта справка
/add_qa - добавить вопрос-ответ в базу знаний (senior, admin)
/admin - панель управления (admin)

Примеры вопросов:
• Какие штрафы предусмотрены за неуплату налогов?
• Что такое налоговая база?
• Порядок подачи налоговой декларации"""

    await message.answer(help_text)


@router.message(Command("add_qa"))
async def cmd_add_qa(message: Message, state: FSMContext):
    """Обработчик команды /add_qa - добавление Q&A"""
    try:
        user = await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
            or f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip(),
        )

        # Проверяем права (только senior и admin)
        if user["role"] not in ("senior", "admin"):
            await message.answer(
                "❌ У вас нет прав для добавления Q&A. Эта функция доступна только для senior и admin."
            )
            return

        if user["is_blocked"]:
            await message.answer("❌ Ваш аккаунт заблокирован.")
            return

        # Переходим в состояние ожидания вопроса
        await state.set_state(AddQAStates.waiting_question)
        await message.answer(
            "📝 Добавление нового Q&A в базу знаний.\n\n" "Введите вопрос:"
        )

    except Exception as e:
        logger.error(f"Ошибка в cmd_add_qa: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(AddQAStates.waiting_question)
async def process_question(message: Message, state: FSMContext):
    """Обработка вопроса для Q&A"""
    await state.update_data(question=message.text)
    await state.set_state(AddQAStates.waiting_answer)
    await message.answer("Теперь введите ответ на этот вопрос:")


@router.message(AddQAStates.waiting_answer)
async def process_answer(message: Message, state: FSMContext):
    """Обработка ответа для Q&A и сохранение в Qdrant"""
    try:
        data = await state.get_data()
        question = data.get("question")
        answer = message.text

        if not question:
            await message.answer("Ошибка: вопрос не найден. Начните заново с /add_qa")
            await state.clear()
            return

        # Получаем информацию о пользователе
        user = await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
            or f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip(),
        )

        added_by = f"{user.get('username') or user.get('full_name')} (ID: {user.get('telegram_id')})"

        # Добавляем в Qdrant
        success = qdrant_service.add_qa(question, answer, added_by)

        if success:
            await message.answer(
                f"✅ Q&A успешно добавлено в базу знаний!\n\n"
                f"Вопрос: {question}\n"
                f"Ответ: {answer[:200]}..."
            )
            logger.info(
                f"Пользователь {message.from_user.id} добавил Q&A: {question[:50]}..."
            )
        else:
            await message.answer(
                "❌ Произошла ошибка при добавлении Q&A. Попробуйте позже."
            )

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при добавлении Q&A: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.message(F.text & ~F.text.startswith("/"))
async def process_question(message: Message):
    """Обработка текстовых вопросов пользователей"""
    try:
        question = message.text.strip()

        if not question:
            await message.answer("Пожалуйста, задайте вопрос.")
            return

        user = await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
            or f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip(),
        )

        if user["is_blocked"]:
            await message.answer("❌ Ваш аккаунт заблокирован.")
            return

        dialog = await create_or_get_active_dialog(
            user_id=user["user_id"], llm_model_used=config.OPENAI_MODEL
        )

        await save_message(
            dialog_id=dialog["dialog_id"],
            user_id=user["user_id"],
            text=question,
            sender_type="user",
            telegram_message_id=message.message_id,
        )

        await message.bot.send_chat_action(message.chat.id, "typing")

        logger.info(
            f"Обработка вопроса от пользователя {message.from_user.id}: {question[:50]}..."
        )
        answer, tokens_used, search_results = rag_service.get_answer(question)

        sent_message = await message.answer(answer)

        await add_tokens_to_dialog(
            dialog_id=dialog["dialog_id"], tokens_used=tokens_used
        )

        await save_message(
            dialog_id=dialog["dialog_id"],
            user_id=user["user_id"],
            text=answer,
            sender_type="bot",
            telegram_message_id=sent_message.message_id,
            tokens_used=tokens_used,
        )

        await update_user_stats(
            user_id=user["user_id"],
            last_activity=datetime.utcnow(),
            increment_consultation=True,
        )

        logger.info(
            f"Ответ отправлен пользователю {message.from_user.id}, использовано токенов: {tokens_used}"
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке вопроса: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при обработке вашего вопроса. Попробуйте позже."
        )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """Обработчик команды /admin - админ-панель"""
    try:
        user = await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
            or f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip(),
        )

        # Проверяем права (только admin)
        if user["role"] != "admin":
            await message.answer(
                "❌ У вас нет прав доступа к админ-панели. Эта функция доступна только для admin."
            )
            return

        if user["is_blocked"]:
            await message.answer("❌ Ваш аккаунт заблокирован.")
            return

        # Очищаем состояние, если было ожидание файла
        current_state = await state.get_state()
        if current_state == AdminStates.waiting_pdf:
            await state.clear()
            await message.answer("✅ Состояние ожидания файла отменено.")

        # Создаем inline клавиатуру
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Обновить знания",
                        callback_data="admin_update_knowledge",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Управление пользователями", callback_data="admin_users"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Статистика", callback_data="admin_stats"
                    ),
                    InlineKeyboardButton(
                        text="❌ Закрыть", callback_data="admin_close"
                    ),
                ],
            ]
        )

        admin_text = """🔐 Админ-панель

Доступные действия:

🔄 Обновить знания
• Удаляет все векторы из Qdrant
• Парсит загруженный PDF файл
• Разбивает на главы и статьи
• Фильтрует короткие статьи
• Загружает в Qdrant

📊 Статистика
• Показывает статистику системы

Выберите действие:"""

        await message.answer(admin_text, reply_markup=keyboard)
        logger.info(f"Админ {message.from_user.id} открыл админ-панель")

    except Exception as e:
        logger.error(f"Ошибка в cmd_admin: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data == "admin_update_knowledge")
async def callback_update_knowledge(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Обновить знания'"""
    try:
        # Проверяем права
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await callback.answer()

        # Переводим в состояние ожидания PDF файла
        await state.set_state(AdminStates.waiting_pdf)

        # Создаем клавиатуру для отмены
        cancel_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data="admin_cancel_update"
                    )
                ]
            ]
        )

        await callback.message.answer(
            "📄 Отправьте PDF файл для обновления знаний.\n\n"
            "Файл будет сохранен и обработан:\n"
            "• Удаление всех векторов из Qdrant\n"
            "• Парсинг PDF\n"
            "• Разбивка на главы и статьи\n"
            "• Фильтрация коротких статей\n"
            "• Загрузка в Qdrant\n\n"
            "Процесс может занять несколько минут.",
            reply_markup=cancel_keyboard,
        )

        logger.info(f"Админ {callback.from_user.id} начал процесс обновления знаний")

    except Exception as e:
        logger.error(f"Ошибка в callback_update_knowledge: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(AdminStates.waiting_pdf, F.document)
async def process_pdf_file(message: Message, state: FSMContext):
    """Обработка загруженного PDF файла"""
    try:
        # Проверяем права
        user = await get_user_by_telegram_id(message.from_user.id)
        if not user or user["role"] != "admin":
            await message.answer("❌ У вас нет прав доступа.")
            await state.clear()
            return

        # Проверяем, что это PDF файл
        document = message.document
        if not document:
            await message.answer("❌ Файл не найден. Отправьте PDF файл.")
            return

        # Проверяем расширение
        file_name = document.file_name or "document"
        if not file_name.lower().endswith(".pdf"):
            await message.answer("❌ Файл должен быть в формате PDF (.pdf)")
            return

        # Создаем папку files/ если её нет
        files_dir = Path("files")
        files_dir.mkdir(exist_ok=True)

        # Генерируем уникальное имя файла с временной меткой
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = Path(file_name).stem
        unique_filename = f"{base_name}_{timestamp}.pdf"
        file_path = files_dir / unique_filename

        # Отправляем сообщение о начале загрузки
        status_msg = await message.answer(
            f"📥 Загружаю файл: {file_name}\n"
            f"💾 Сохраняю как: {unique_filename}\n\n"
            f"⏳ Пожалуйста, подождите..."
        )

        # Скачиваем файл
        file = await message.bot.get_file(document.file_id)
        await message.bot.download_file(file.file_path, destination=str(file_path))

        await status_msg.edit_text(
            f"✅ Файл сохранен: {unique_filename}\n\n"
            f"🔄 Начинаю обработку...\n"
            f"Это может занять несколько минут."
        )

        # Очищаем коллекцию
        await message.bot.send_chat_action(message.chat.id, "typing")
        cleared = qdrant_service.clear_collection()

        if not cleared:
            await status_msg.edit_text("❌ Ошибка при очистке коллекции Qdrant")
            await state.clear()
            return

        # Обрабатываем PDF в отдельной задаче, чтобы не блокировать бота
        loop = asyncio.get_event_loop()
        success, result_message = await loop.run_in_executor(
            None,
            process_pdf_and_upload_to_qdrant,
            str(file_path),
            None,  # chapter_filter - загружаем ВСЕ главы
            None,  # max_words - без ограничения по размеру
        )

        # Обновляем сообщение с результатом
        await status_msg.edit_text(result_message)

        # Очищаем состояние
        await state.clear()

        logger.info(
            f"Админ {message.from_user.id} обновил знания из файла {unique_filename}. Успех: {success}"
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке PDF файла: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при обработке файла:\n{str(e)}")
        await state.clear()


@router.message(AdminStates.waiting_pdf)
async def process_wrong_file_type(message: Message):
    """Обработка неправильного типа файла"""
    await message.answer(
        "❌ Пожалуйста, отправьте PDF файл (документ).\n\n"
        "Или используйте /admin для возврата в админ-панель."
    )


@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Обработчик кнопки 'Статистика'"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        try:
            collection_info = qdrant_service.client.get_collection(
                qdrant_service.collection_name
            )
            points_count = collection_info.points_count
        except Exception:
            points_count = "недоступно"

        stats_text = f"""📊 Статистика системы

🔍 Qdrant:
• Коллекция: {qdrant_service.collection_name}
• Векторов: {points_count}

🤖 Бот:
• Модель embeddings: {config.OPENAI_EMBEDDING_MODEL}
• Модель LLM: {config.OPENAI_MODEL}
• Макс. результатов поиска: {config.MAX_SEARCH_RESULTS}
"""

        await callback.answer()
        await callback.message.answer(stats_text)

    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await callback.answer("❌ Ошибка при получении статистики", show_alert=True)


@router.callback_query(F.data == "admin_close")
async def callback_admin_close(callback: CallbackQuery):
    """Обработчик кнопки 'Закрыть'"""
    await callback.answer()
    await callback.message.delete()


@router.callback_query(F.data == "admin_cancel_update")
async def callback_cancel_update(callback: CallbackQuery, state: FSMContext):
    """Обработчик отмены обновления знаний"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await state.clear()
        await callback.answer("❌ Обновление знаний отменено")
        await callback.message.edit_text("❌ Обновление знаний отменено.")

    except Exception as e:
        logger.error(f"Ошибка при отмене обновления: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "admin_users")
async def callback_admin_users(callback: CallbackQuery):
    """Обработчик кнопки 'Управление пользователями'"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await callback.answer()

        # Создаем клавиатуру управления пользователями
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="👤 Выдать роль", callback_data="admin_set_role"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚫 Заблокировать пользователя",
                        callback_data="admin_block_user",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
            ]
        )

        users_text = """👥 Управление пользователями

Доступные действия:

👤 Выдать роль
• Изменить роль пользователя (junior/senior/admin)
• Поиск: по ID, username или из списка

🚫 Заблокировать пользователя
• Заблокировать или разблокировать пользователя
• Поиск: по ID, username или из списка

Выберите действие:"""

        await callback.message.edit_text(users_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка в callback_admin_users: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "admin_set_role")
async def callback_set_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Выдать роль'"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await callback.answer()

        # Переводим в состояние выбора способа поиска
        await state.set_state(AdminStates.waiting_user_selection_method)
        await state.update_data(action="set_role")

        # Создаем клавиатуру выбора способа поиска
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔢 По Telegram ID", callback_data="select_by_id_role"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👤 По username", callback_data="select_by_username_role"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📋 Выбрать из списка",
                        callback_data="select_from_list_role",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")],
            ]
        )

        await callback.message.edit_text(
            "👤 Выдача роли пользователю\n\n" "Выберите способ поиска пользователя:",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка в callback_set_role: {e}")
        await callback.answer("Ошибка", show_alert=True)


# Обработчики выбора способа поиска для роли
@router.callback_query(F.data == "select_by_id_role")
async def callback_select_by_id_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора поиска по ID для роли"""
    await callback.answer()
    await state.set_state(AdminStates.waiting_telegram_id_for_role)
    await callback.message.edit_text(
        "👤 Выдача роли пользователю\n\n"
        "Введите Telegram ID пользователя (число):\n\n"
        "Пример: 123456789"
    )


@router.callback_query(F.data == "select_by_username_role")
async def callback_select_by_username_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора поиска по username для роли"""
    await callback.answer()
    await state.set_state(AdminStates.waiting_username_for_role)
    await callback.message.edit_text(
        "👤 Выдача роли пользователю\n\n"
        "Введите username пользователя (с @ или без):\n\n"
        "Пример: @username или username"
    )


@router.callback_query(F.data == "select_from_list_role")
async def callback_select_from_list_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора из списка для роли"""
    try:
        await callback.answer()

        # Получаем список пользователей
        users = await get_all_users(limit=50)

        if not users:
            await callback.message.edit_text("❌ Пользователи не найдены.")
            return

        # Создаем клавиатуру со списком пользователей
        keyboard_buttons = []
        for user in users[:20]:  # Ограничиваем 20 пользователями
            username = user.get("username") or user.get("full_name") or "Без имени"
            telegram_id = user.get("telegram_id")
            role = user.get("role", "unknown")
            blocked = "🚫" if user.get("is_blocked") else "✅"

            button_text = f"{blocked} {username} ({telegram_id}) - {role}"
            if len(button_text) > 60:
                button_text = f"{blocked} {username[:20]}... ({telegram_id})"

            keyboard_buttons.append(
                [
                    InlineKeyboardButton(
                        text=button_text, callback_data=f"user_role_{telegram_id}"
                    )
                ]
            )

        keyboard_buttons.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_set_role")]
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await callback.message.edit_text(
            f"📋 Выберите пользователя из списка:\n\n"
            f"Всего пользователей: {len(users)}\n"
            f"Показано: {min(20, len(users))}",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка при показе списка пользователей: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.message(AdminStates.waiting_telegram_id_for_role, F.text.regexp(r"^\d+$"))
async def process_telegram_id_for_role(message: Message, state: FSMContext):
    """Обработка Telegram ID для выдачи роли"""
    try:
        telegram_id = int(message.text.strip())

        # Проверяем, существует ли пользователь
        target_user = await get_user_by_telegram_id(telegram_id)
        if not target_user:
            await message.answer(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден.\n\n"
                f"Пользователь должен сначала использовать команду /start."
            )
            await state.clear()
            return

        # Сохраняем telegram_id в состоянии
        await state.update_data(
            telegram_id=telegram_id,
            username=target_user.get("username") or target_user.get("full_name"),
        )

        # Переводим в состояние выбора роли
        await state.set_state(AdminStates.waiting_role_selection)

        # Создаем клавиатуру выбора роли
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👶 Junior", callback_data="role_junior")],
                [InlineKeyboardButton(text="👔 Senior", callback_data="role_senior")],
                [InlineKeyboardButton(text="👑 Admin", callback_data="role_admin")],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data="admin_cancel_role"
                    )
                ],
            ]
        )

        current_role = target_user.get("role", "unknown")
        await message.answer(
            f"👤 Пользователь: {target_user.get('username') or target_user.get('full_name') or 'Без имени'}\n"
            f"📱 Telegram ID: {telegram_id}\n"
            f"🎭 Текущая роль: {current_role}\n\n"
            f"Выберите новую роль:",
            reply_markup=keyboard,
        )

    except ValueError:
        await message.answer("❌ Неверный формат. Введите число (Telegram ID).")
    except Exception as e:
        logger.error(f"Ошибка при обработке Telegram ID для роли: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.message(AdminStates.waiting_telegram_id_for_role)
async def process_invalid_telegram_id_for_role(message: Message):
    """Обработка неверного формата Telegram ID"""
    await message.answer(
        "❌ Неверный формат. Введите Telegram ID (число).\n\n"
        "Или используйте /admin для возврата в админ-панель."
    )


@router.message(AdminStates.waiting_username_for_role)
async def process_username_for_role(message: Message, state: FSMContext):
    """Обработка username для выдачи роли"""
    try:
        username = message.text.strip().lstrip("@")

        # Проверяем, существует ли пользователь
        target_user = await get_user_by_username(username)
        if not target_user:
            await message.answer(
                f"❌ Пользователь с username @{username} не найден.\n\n"
                f"Пользователь должен сначала использовать команду /start."
            )
            await state.clear()
            return

        telegram_id = target_user.get("telegram_id")

        # Сохраняем telegram_id в состоянии
        await state.update_data(
            telegram_id=telegram_id,
            username=target_user.get("username") or target_user.get("full_name"),
        )

        # Переводим в состояние выбора роли
        await state.set_state(AdminStates.waiting_role_selection)

        # Создаем клавиатуру выбора роли
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👶 Junior", callback_data="role_junior")],
                [InlineKeyboardButton(text="👔 Senior", callback_data="role_senior")],
                [InlineKeyboardButton(text="👑 Admin", callback_data="role_admin")],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data="admin_cancel_role"
                    )
                ],
            ]
        )

        current_role = target_user.get("role", "unknown")
        await message.answer(
            f"👤 Пользователь: @{target_user.get('username') or 'Без username'}\n"
            f"📱 Telegram ID: {telegram_id}\n"
            f"🎭 Текущая роль: {current_role}\n\n"
            f"Выберите новую роль:",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке username для роли: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.callback_query(F.data.startswith("user_role_"))
async def callback_user_selected_for_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора пользователя из списка для роли"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        # Извлекаем telegram_id из callback_data
        telegram_id = int(callback.data.replace("user_role_", ""))

        # Получаем пользователя
        target_user = await get_user_by_telegram_id(telegram_id)
        if not target_user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Сохраняем telegram_id в состоянии
        await state.update_data(
            telegram_id=telegram_id,
            username=target_user.get("username") or target_user.get("full_name"),
        )

        # Переводим в состояние выбора роли
        await state.set_state(AdminStates.waiting_role_selection)

        # Создаем клавиатуру выбора роли
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👶 Junior", callback_data="role_junior")],
                [InlineKeyboardButton(text="👔 Senior", callback_data="role_senior")],
                [InlineKeyboardButton(text="👑 Admin", callback_data="role_admin")],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data="admin_cancel_role"
                    )
                ],
            ]
        )

        current_role = target_user.get("role", "unknown")
        await callback.answer()
        await callback.message.edit_text(
            f"👤 Пользователь: {target_user.get('username') or target_user.get('full_name') or 'Без имени'}\n"
            f"📱 Telegram ID: {telegram_id}\n"
            f"🎭 Текущая роль: {current_role}\n\n"
            f"Выберите новую роль:",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка при выборе пользователя из списка: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("role_"))
async def callback_select_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора роли"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        # Извлекаем роль из callback_data
        role = callback.data.replace("role_", "")

        # Получаем данные из состояния
        data = await state.get_data()
        telegram_id = data.get("telegram_id")

        if not telegram_id:
            await callback.answer("❌ Ошибка: Telegram ID не найден", show_alert=True)
            await state.clear()
            return

        # Обновляем роль
        success = await update_user_role(telegram_id, role)

        if success:
            role_names = {
                "junior": "👶 Junior",
                "senior": "👔 Senior",
                "admin": "👑 Admin",
            }
            await callback.answer(f"✅ Роль изменена на {role_names.get(role, role)}")
            await callback.message.edit_text(
                f"✅ Роль успешно изменена!\n\n"
                f"👤 Пользователь: {data.get('username', 'N/A')}\n"
                f"📱 Telegram ID: {telegram_id}\n"
                f"🎭 Новая роль: {role_names.get(role, role)}"
            )
            logger.info(
                f"Админ {callback.from_user.id} изменил роль пользователя {telegram_id} на {role}"
            )
        else:
            await callback.answer("❌ Ошибка при изменении роли", show_alert=True)
            await callback.message.edit_text(
                "❌ Не удалось изменить роль. Пользователь не найден."
            )

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при выборе роли: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()


@router.callback_query(F.data == "admin_cancel_role")
async def callback_cancel_role(callback: CallbackQuery, state: FSMContext):
    """Обработчик отмены выдачи роли"""
    await callback.answer("❌ Отменено")
    await state.clear()
    await callback.message.edit_text("❌ Выдача роли отменена.")


@router.callback_query(F.data == "admin_block_user")
async def callback_block_user(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Заблокировать пользователя'"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await callback.answer()

        # Переводим в состояние выбора способа поиска
        await state.set_state(AdminStates.waiting_user_selection_method)
        await state.update_data(action="block_user")

        # Создаем клавиатуру выбора способа поиска
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔢 По Telegram ID", callback_data="select_by_id_block"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👤 По username", callback_data="select_by_username_block"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📋 Выбрать из списка",
                        callback_data="select_from_list_block",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")],
            ]
        )

        await callback.message.edit_text(
            "🚫 Блокировка/разблокировка пользователя\n\n"
            "Выберите способ поиска пользователя:",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка в callback_block_user: {e}")
        await callback.answer("Ошибка", show_alert=True)


# Обработчики выбора способа поиска для блокировки
@router.callback_query(F.data == "select_by_id_block")
async def callback_select_by_id_block(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора поиска по ID для блокировки"""
    await callback.answer()
    await state.set_state(AdminStates.waiting_telegram_id_for_block)
    await callback.message.edit_text(
        "🚫 Блокировка/разблокировка пользователя\n\n"
        "Введите Telegram ID пользователя (число):\n\n"
        "Пример: 123456789"
    )


@router.callback_query(F.data == "select_by_username_block")
async def callback_select_by_username_block(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора поиска по username для блокировки"""
    await callback.answer()
    await state.set_state(AdminStates.waiting_username_for_block)
    await callback.message.edit_text(
        "🚫 Блокировка/разблокировка пользователя\n\n"
        "Введите username пользователя (с @ или без):\n\n"
        "Пример: @username или username"
    )


@router.callback_query(F.data == "select_from_list_block")
async def callback_select_from_list_block(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора из списка для блокировки"""
    try:
        await callback.answer()

        # Получаем список пользователей
        users = await get_all_users(limit=50)

        if not users:
            await callback.message.edit_text("❌ Пользователи не найдены.")
            return

        # Создаем клавиатуру со списком пользователей
        keyboard_buttons = []
        for user in users[:20]:  # Ограничиваем 20 пользователями
            username = user.get("username") or user.get("full_name") or "Без имени"
            telegram_id = user.get("telegram_id")
            role = user.get("role", "unknown")
            blocked = "🚫" if user.get("is_blocked") else "✅"

            button_text = f"{blocked} {username} ({telegram_id}) - {role}"
            if len(button_text) > 60:
                button_text = f"{blocked} {username[:20]}... ({telegram_id})"

            keyboard_buttons.append(
                [
                    InlineKeyboardButton(
                        text=button_text, callback_data=f"user_block_{telegram_id}"
                    )
                ]
            )

        keyboard_buttons.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_block_user")]
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await callback.message.edit_text(
            f"📋 Выберите пользователя из списка:\n\n"
            f"Всего пользователей: {len(users)}\n"
            f"Показано: {min(20, len(users))}",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Ошибка при показе списка пользователей: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.message(AdminStates.waiting_telegram_id_for_block, F.text.regexp(r"^\d+$"))
async def process_telegram_id_for_block(message: Message, state: FSMContext):
    """Обработка Telegram ID для блокировки"""
    try:
        telegram_id = int(message.text.strip())

        # Проверяем, существует ли пользователь
        target_user = await get_user_by_telegram_id(telegram_id)
        if not target_user:
            await message.answer(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден.\n\n"
                f"Пользователь должен сначала использовать команду /start."
            )
            await state.clear()
            return

        # Нельзя заблокировать другого админа
        if target_user["role"] == "admin" and telegram_id != message.from_user.id:
            await message.answer("❌ Нельзя заблокировать другого администратора.")
            await state.clear()
            return

        current_blocked = target_user.get("is_blocked", False)
        new_blocked = not current_blocked

        # Обновляем статус блокировки
        success = await toggle_user_block(telegram_id, new_blocked)

        if success:
            status_text = "заблокирован" if new_blocked else "разблокирован"
            emoji = "🚫" if new_blocked else "✅"
            await message.answer(
                f"{emoji} Пользователь {status_text}!\n\n"
                f"👤 Пользователь: {target_user.get('username') or target_user.get('full_name') or 'Без имени'}\n"
                f"📱 Telegram ID: {telegram_id}\n"
                f"🎭 Роль: {target_user.get('role')}\n"
                f"🔒 Статус: {'Заблокирован' if new_blocked else 'Активен'}"
            )
            logger.info(
                f"Админ {message.from_user.id} {'заблокировал' if new_blocked else 'разблокировал'} пользователя {telegram_id}"
            )
        else:
            await message.answer("❌ Ошибка при изменении статуса блокировки.")

        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат. Введите число (Telegram ID).")
    except Exception as e:
        logger.error(f"Ошибка при обработке блокировки: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.message(AdminStates.waiting_telegram_id_for_block)
async def process_invalid_telegram_id_for_block(message: Message):
    """Обработка неверного формата Telegram ID для блокировки"""
    await message.answer(
        "❌ Неверный формат. Введите Telegram ID (число).\n\n"
        "Или используйте /admin для возврата в админ-панель."
    )


@router.message(AdminStates.waiting_username_for_block)
async def process_username_for_block(message: Message, state: FSMContext):
    """Обработка username для блокировки"""
    try:
        username = message.text.strip().lstrip("@")

        # Проверяем, существует ли пользователь
        target_user = await get_user_by_username(username)
        if not target_user:
            await message.answer(
                f"❌ Пользователь с username @{username} не найден.\n\n"
                f"Пользователь должен сначала использовать команду /start."
            )
            await state.clear()
            return

        telegram_id = target_user.get("telegram_id")

        # Нельзя заблокировать другого админа
        if target_user["role"] == "admin" and telegram_id != message.from_user.id:
            await message.answer("❌ Нельзя заблокировать другого администратора.")
            await state.clear()
            return

        current_blocked = target_user.get("is_blocked", False)
        new_blocked = not current_blocked

        # Обновляем статус блокировки
        success = await toggle_user_block(telegram_id, new_blocked)

        if success:
            status_text = "заблокирован" if new_blocked else "разблокирован"
            emoji = "🚫" if new_blocked else "✅"
            await message.answer(
                f"{emoji} Пользователь {status_text}!\n\n"
                f"👤 Пользователь: @{target_user.get('username') or 'Без username'}\n"
                f"📱 Telegram ID: {telegram_id}\n"
                f"🎭 Роль: {target_user.get('role')}\n"
                f"🔒 Статус: {'Заблокирован' if new_blocked else 'Активен'}"
            )
            logger.info(
                f"Админ {message.from_user.id} {'заблокировал' if new_blocked else 'разблокировал'} пользователя {telegram_id}"
            )
        else:
            await message.answer("❌ Ошибка при изменении статуса блокировки.")

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при обработке блокировки по username: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()


@router.callback_query(F.data.startswith("user_block_"))
async def callback_user_selected_for_block(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора пользователя из списка для блокировки"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        # Извлекаем telegram_id из callback_data
        telegram_id = int(callback.data.replace("user_block_", ""))

        # Получаем пользователя
        target_user = await get_user_by_telegram_id(telegram_id)
        if not target_user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Нельзя заблокировать другого админа
        if target_user["role"] == "admin" and telegram_id != callback.from_user.id:
            await callback.answer(
                "❌ Нельзя заблокировать другого администратора", show_alert=True
            )
            return

        current_blocked = target_user.get("is_blocked", False)
        new_blocked = not current_blocked

        # Обновляем статус блокировки
        success = await toggle_user_block(telegram_id, new_blocked)

        if success:
            status_text = "заблокирован" if new_blocked else "разблокирован"
            emoji = "🚫" if new_blocked else "✅"
            await callback.answer(f"{emoji} Пользователь {status_text}!")
            await callback.message.edit_text(
                f"{emoji} Пользователь {status_text}!\n\n"
                f"👤 Пользователь: {target_user.get('username') or target_user.get('full_name') or 'Без имени'}\n"
                f"📱 Telegram ID: {telegram_id}\n"
                f"🎭 Роль: {target_user.get('role')}\n"
                f"🔒 Статус: {'Заблокирован' if new_blocked else 'Активен'}"
            )
            logger.info(
                f"Админ {callback.from_user.id} {'заблокировал' if new_blocked else 'разблокировал'} пользователя {telegram_id}"
            )
        else:
            await callback.answer("❌ Ошибка при изменении статуса", show_alert=True)

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при выборе пользователя из списка для блокировки: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "admin_back")
async def callback_admin_back(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад'"""
    try:
        user = await get_user_by_telegram_id(callback.from_user.id)
        if not user or user["role"] != "admin":
            await callback.answer("❌ Нет прав доступа", show_alert=True)
            return

        await callback.answer()

        # Очищаем состояние
        await state.clear()

        # Возвращаемся в главную админ-панель
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Обновить знания",
                        callback_data="admin_update_knowledge",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Управление пользователями", callback_data="admin_users"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Статистика", callback_data="admin_stats"
                    ),
                    InlineKeyboardButton(
                        text="❌ Закрыть", callback_data="admin_close"
                    ),
                ],
            ]
        )

        admin_text = """🔐 Админ-панель

Доступные действия:

🔄 Обновить знания
• Удаляет все векторы из Qdrant
• Парсит загруженный PDF файл
• Разбивает на главы и статьи
• Фильтрует короткие статьи
• Загружает в Qdrant

👥 Управление пользователями
• Выдача ролей
• Блокировка пользователей

📊 Статистика
• Показывает статистику системы

Выберите действие:"""

        await callback.message.edit_text(admin_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка в callback_admin_back: {e}")
        await callback.answer("Ошибка", show_alert=True)

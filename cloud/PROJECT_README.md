# Техническая документация: RAG-бот (Cloud / OpenAI)

## Обзор

Telegram-бот для консультаций по Налоговому кодексу РФ (глава 16). Использует RAG: ищет релевантные статьи в Qdrant, передаёт их как контекст в GPT-4, возвращает ответ пользователю.

## Стек

- **Bot**: aiogram 3, asyncio, FSM (MemoryStorage)
- **LLM**: OpenAI GPT-4 (`gpt-4`)
- **Embeddings**: OpenAI `text-embedding-3-small` (1536d)
- **Векторная БД**: Qdrant
- **СУБД**: PostgreSQL через asyncpg (пул 1–10 соединений)
- **PDF**: PyMuPDF
- **Деплой**: Docker Compose (postgres + qdrant + bot)

## Структура файлов

```
cloud/
├── bot.py                  # Точка входа: init DB → create Bot → polling
├── config.py               # Все переменные окружения и константы
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
│
├── database/
│   ├── __init__.py         # SQLAlchemy sync engine (SessionLocal)
│   ├── async_db.py         # asyncpg: пул, CRUD, все SQL-запросы
│   └── models.py           # SQLAlchemy ORM модели (User, Dialog, Message)
│
├── handlers/
│   └── user_handlers.py    # Все команды, FSM-состояния, callback-хендлеры
│
├── services/
│   ├── openai_service.py   # Вызов GPT-4 API
│   ├── qdrant_service.py   # Embeddings + поиск + add_qa
│   ├── rag_service.py      # Оркестрация: поиск контекста → генерация ответа
│   └── pdf_processor.py    # PDF → текст → главы/статьи → embeddings → Qdrant
│
└── utils/
    └── logger.py           # Настройка логирования (file + stdout)
```

## Конфигурация (config.py)

### Переменные окружения (.env)

| Переменная | Обязательна | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | — |
| `OPENAI_API_KEY` | да | — |
| `QDRANT_URL` | нет | `http://localhost:6333` |
| `QDRANT_COLLECTION_NAME` | нет | `tax_code_chapter_16` |
| `POSTGRES_HOST` | нет | `localhost` |
| `POSTGRES_PORT` | нет | `5432` |
| `POSTGRES_DB` | нет | `tax_bot_db` |
| `POSTGRES_USER` | нет | `tax_bot` |
| `POSTGRES_PASSWORD` | нет | `tax_bot_pass` |
| `LOG_LEVEL` | нет | `INFO` |

### Константы

```python
OPENAI_MODEL = "gpt-4"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
MAX_CONTEXT_LENGTH = 12000    # символов контекста для промпта
MAX_SEARCH_RESULTS = 3        # top-K из Qdrant
MAX_TOKENS_RESPONSE = 2000    # max_tokens для GPT-4
```

## База данных

### Схема таблиц (создаётся в async_db.py через asyncpg)

```sql
CREATE TABLE users (
    user_id       BIGSERIAL PRIMARY KEY,
    telegram_id   BIGINT UNIQUE NOT NULL,
    username      VARCHAR(100),
    full_name     VARCHAR(255),
    role          VARCHAR(20) NOT NULL DEFAULT 'junior',   -- junior | senior | admin
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    is_blocked    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP NULL,
    consultation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dialogs (
    dialog_id        BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    started_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at         TIMESTAMP NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'active',  -- active | resolved
    llm_model_used   VARCHAR(50),
    total_tokens_used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    message_id          BIGSERIAL PRIMARY KEY,
    dialog_id           BIGINT NOT NULL REFERENCES dialogs(dialog_id) ON DELETE CASCADE,
    user_id             BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    context             TEXT NOT NULL,
    sender_type         VARCHAR(10) NOT NULL,  -- user | bot
    sent_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    telegram_message_id BIGINT,
    tokens_used         INTEGER NOT NULL DEFAULT 0
);
```

### Функции async_db.py

| Функция | Что делает |
|---|---|
| `init_db_async()` | Создаёт пул asyncpg, создаёт таблицы |
| `get_conn()` | Контекстный менеджер для получения соединения |
| `get_or_create_user(telegram_id, username, full_name)` | INSERT ON CONFLICT DO UPDATE, возвращает dict |
| `get_user_by_telegram_id(telegram_id)` | SELECT по telegram_id |
| `ensure_senior_or_admin(telegram_id)` | Проверяет role in (senior, admin) и не заблокирован |
| `create_or_get_active_dialog(user_id, llm_model_used)` | Берёт активный диалог или создаёт новый |
| `save_message(dialog_id, user_id, text, sender_type, ...)` | INSERT в messages |
| `update_user_stats(user_id, last_activity, increment_consultation)` | Обновляет last_activity, опционально +1 к consultation_count |
| `add_tokens_to_dialog(dialog_id, tokens_used)` | Инкрементирует total_tokens_used |
| `update_user_role(telegram_id, role)` | Меняет роль, возвращает bool |
| `toggle_user_block(telegram_id, is_blocked)` | Меняет флаг блокировки |
| `get_all_users(limit=100)` | Список пользователей |
| `get_user_by_username(username)` | Поиск по username (без @) |

## Потоки данных

### Вопрос пользователя → ответ

```
Telegram → process_question()
  │
  ├─ get_user_by_telegram_id() → проверка блокировки
  ├─ create_or_get_active_dialog(llm_model_used=config.OPENAI_MODEL)
  ├─ save_message(sender_type="user")
  │
  ├─ RAGService.get_answer(question)
  │   ├─ QdrantService.get_context_for_prompt(question)
  │   │   ├─ _get_embedding(question) → OpenAI embeddings API → вектор 1536d
  │   │   ├─ client.query_points(limit=3) → top-3 статьи
  │   │   └─ собирает контекст (до 12000 символов)
  │   │
  │   └─ OpenAIService.generate_response(question, context)
  │       └─ chat.completions.create(model="gpt-4", max_tokens=2000, temperature=0.7)
  │           messages: [system: "Ты консультант по НК РФ", user: промпт с контекстом]
  │
  ├─ message.answer(ответ)
  ├─ add_tokens_to_dialog()
  ├─ save_message(sender_type="bot", tokens_used=N)
  └─ update_user_stats(increment_consultation=True)
```

### Загрузка базы знаний (PDF)

```
Админ загружает PDF через /admin → "Обновить знания"
  │
  ├─ Скачивание файла через bot API
  ├─ qdrant_service.clear_collection()
  │
  └─ process_pdf_and_upload_to_qdrant(file_path)
      ├─ pdf_to_text() → извлечение текста (PyMuPDF)
      ├─ clean_text() → удаление мусора (реклама, URL, телефоны)
      ├─ split_into_chapters_and_articles() → парсинг структуры НК РФ
      │   regex: "Глава N.N. ..." → главы
      │   regex: "Статья N.N. ..." → статьи внутри глав
      ├─ select_chapters_with_metadata(filter, max_words)
      │
      └─ Загрузка в Qdrant:
          ├─ Удаление старой коллекции
          ├─ Создание новой (size=1536, distance=COSINE)
          └─ Для каждой статьи (батчами по 32):
              ├─ OpenAI embeddings.create()
              └─ qdrant.upsert(PointStruct с payload)
```

Payload каждого вектора в Qdrant:
```python
{
    "text": "полный текст (глава + статья + содержание)",
    "chapter_title": "Глава 16. Виды налоговых правонарушений...",
    "chapter_number": 16.0,
    "article_title": "Статья 116. ...",
    "article_content": "1. Нарушение...\n2. ...",
    "word_count": 450
}
```

## FSM-состояния (handlers/user_handlers.py)

### AddQAStates

```
/add_qa → [проверка: senior/admin, не заблокирован]
  → waiting_question → пользователь вводит вопрос
  → waiting_answer → пользователь вводит ответ
  → qdrant_service.add_qa() → очистка состояния
```

### AdminStates

```
/admin → [проверка: admin, не заблокирован] → inline-меню

Обновить знания:
  admin_update_knowledge → waiting_pdf → загрузка PDF → обработка → очистка

Управление пользователями → admin_users:
  ├─ admin_set_role → waiting_user_selection_method
  │   ├─ select_by_id_role    → waiting_telegram_id_for_role → ввод ID → выбор роли
  │   ├─ select_by_username_role → waiting_username_for_role → ввод @username → выбор роли
  │   └─ select_from_list_role → список пользователей → выбор роли
  │       → role_junior / role_senior / role_admin → update_user_role()
  │
  └─ admin_block_user → (аналогично)
      ├─ select_by_id_block    → waiting_telegram_id_for_block → toggle_user_block()
      ├─ select_by_username_block → waiting_username_for_block → toggle_user_block()
      └─ select_from_list_block → список → toggle_user_block()
```

## Команды бота

| Команда | Доступ | Действие |
|---|---|---|
| `/start` | все | Регистрация/обновление пользователя, welcome-сообщение |
| `/help` | все | Справка с примерами вопросов |
| `/add_qa` | senior, admin | Добавление Q&A пары в Qdrant |
| `/admin` | admin | Админ-панель (inline-клавиатура) |

## Роли и права

| Возможность | junior | senior | admin |
|---|---|---|---|
| Задавать вопросы | + | + | + |
| Добавлять Q&A | - | + | + |
| Админ-панель | - | - | + |
| Обновлять базу знаний | - | - | + |
| Управлять пользователями | - | - | + |
| Блокировать пользователей | - | - | + |

Ограничение: admin не может заблокировать другого admin.

## Инициализация сервисов

```python
# handlers/user_handlers.py (на уровне модуля)
rag_service = RAGService()        # → QdrantService() + OpenAIService()
qdrant_service = QdrantService()  # для прямого доступа (add_qa, clear_collection, stats)
```

`RAGService` создаёт собственные экземпляры `QdrantService` и `OpenAIService` внутри.

## Docker

### docker-compose.yml — три сервиса:

| Сервис | Образ | Healthcheck | Порт |
|---|---|---|---|
| postgres | postgres:16-alpine | `pg_isready -U tax_bot -d tax_bot_db` | 5432 |
| qdrant | qdrant/qdrant:latest | `wget -qO- http://localhost:6333/readyz` | 6333 |
| bot | build: . | — | — |

Bot стартует только после healthcheck postgres и qdrant (`condition: service_healthy`).

Переменные окружения в compose переопределяют дефолты config.py:
- `POSTGRES_HOST: postgres` (имя сервиса вместо localhost)
- `QDRANT_URL: http://qdrant:6333`

Volume `./files:/app/files` — для загруженных PDF.

### Запуск

```bash
cd cloud
# создать .env с TELEGRAM_BOT_TOKEN и OPENAI_API_KEY
docker compose up -d
```

## Зависимости (requirements.txt)

```
pymupdf>=1.23.0
qdrant-client>=1.7.0
aiogram>=3.0.0
openai>=1.0.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
asyncpg==0.31.0
python-dotenv>=1.0.0
```

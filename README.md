# RAG-бот для консультаций по Налоговому кодексу РФ

Telegram-бот с RAG (Retrieval-Augmented Generation) для ответов на вопросы по Налоговому кодексу РФ. Проект реализован в двух вариантах: облачном (OpenAI) и локальном (Ollama + sentence-transformers).

## Архитектура

```
rag/
├── cloud/          # Облачная версия (OpenAI API)
├── local/          # Локальная версия (Ollama)
└── README.md
```

Обе версии полностью автономны и имеют одинаковую структуру:

```
bot.py              # Точка входа
config.py           # Конфигурация
Dockerfile
docker-compose.yml
requirements.txt
database/
├── async_db.py     # Асинхронные операции с PostgreSQL (asyncpg)
└── models.py       # SQLAlchemy модели
handlers/
└── user_handlers.py  # Обработчики Telegram-команд
services/
├── rag_service.py    # Оркестрация RAG-пайплайна
├── qdrant_service.py # Работа с векторной БД
├── pdf_processor.py  # Парсинг PDF и загрузка в Qdrant
├── openai_service.py # (cloud) Генерация ответов через GPT-4
└── llm_service.py    # (local) Генерация ответов через Ollama
utils/
└── logger.py
```

## Сравнение версий

| | Cloud | Local |
|---|---|---|
| LLM | GPT-4 (OpenAI API) | Ollama (Mistral 7B и др.) |
| Embeddings | text-embedding-3-small (1536d) | rubert-tiny2 (312d) |
| Требования | API-ключ OpenAI | GPU рекомендуется |
| Стоимость | Платно (по токенам) | Бесплатно |

## Стек

- **Telegram**: aiogram 3
- **Векторная БД**: Qdrant
- **СУБД**: PostgreSQL (asyncpg)
- **PDF**: PyMuPDF

## Запуск (Docker)

### Cloud-версия

```bash
cd cloud
```

Создайте `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token
OPENAI_API_KEY=your_key
```

```bash
docker compose up -d
```

### Local-версия

Убедитесь, что Ollama запущен на хосте и модель скачана:

```bash
ollama pull mistral:7b
```

```bash
cd local
```

Создайте `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token
OLLAMA_MODEL=mistral:7b
```

```bash
docker compose up -d
```

Ollama работает на хосте, бот обращается к нему через `host.docker.internal`.

## Загрузка базы знаний

Через админ-панель бота (команда `/admin`) можно загрузить PDF-файл с текстом НК РФ. Бот автоматически:

1. Извлечёт текст из PDF
2. Разобьёт на главы и статьи
3. Создаст embeddings
4. Загрузит в Qdrant

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Начать работу |
| `/help` | Справка |
| `/add_qa` | Добавить Q&A в базу (senior/admin) |
| `/admin` | Админ-панель (admin) |

## Роли пользователей

- **junior** — может задавать вопросы
- **senior** — может добавлять Q&A в базу знаний
- **admin** — полный доступ: управление пользователями, обновление базы знаний, статистика

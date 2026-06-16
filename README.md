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

```bash
cd local
```

Создайте `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token
OLLAMA_MODEL=mistral:7b
```

```bash
./setup.sh
```

Скрипт автоматически:
- Проверит установку Ollama
- Настроит доступ из Docker (`OLLAMA_HOST=0.0.0.0`)
- Скачает модель, если её нет
- Запустит `docker compose up -d`

Если Ollama не установлена — скрипт установит её автоматически.

#### GPU (опционально)

Для ускорения эмбеддингов на NVIDIA GPU установите [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```

Без GPU бот работает на CPU, но эмбеддинги считаются медленнее.

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

## Назначение первого администратора

Первый admin назначается напрямую через PostgreSQL, поскольку все новые пользователи получают роль `junior` по умолчанию.

1. Напишите боту `/start`, чтобы зарегистрироваться в базе.

2. Узнайте свой `telegram_id` (например, через [@userinfobot](https://t.me/userinfobot)).

3. Выполните SQL-запрос внутри контейнера:

```bash
docker exec <postgres-container> psql -U tax_bot -d tax_bot_db \
  -c "UPDATE users SET role = 'admin' WHERE telegram_id = <ваш_telegram_id>;"
```

Для cloud-версии имя контейнера — `cloud-postgres-1`, для local — `local-postgres-1`.

Пример:

```bash
docker exec cloud-postgres-1 psql -U tax_bot -d tax_bot_db \
  -c "UPDATE users SET role = 'admin' WHERE telegram_id = 123456789;"
```

4. Проверьте результат:

```bash
docker exec cloud-postgres-1 psql -U tax_bot -d tax_bot_db \
  -c "SELECT telegram_id, username, role FROM users;"
```

После этого напишите боту `/admin` — откроется панель управления. Дальнейшее управление ролями доступно прямо через бота.

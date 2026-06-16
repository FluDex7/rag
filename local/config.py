import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не установлен в переменных окружения!")

LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
EMBEDDING_DIMENSION = 1024
EMBEDDING_MAX_TOKENS = 512
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cuda")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:7b")
OLLAMA_TIMEOUT = 600

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "tax_code")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "tax_bot_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "tax_bot")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "tax_bot_pass")

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot_local.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

SYSTEM_PROMPT = """Ты - помощник-консультант по Налоговому кодексу Российской Федерации.
Твоя задача - давать точные и понятные ответы на вопросы пользователей, основываясь на предоставленном контексте из Налогового кодекса.

Инструкции:
1. Отвечай только на основе предоставленного контекста
2. Если в контексте нет информации для ответа, честно скажи об этом
3. Используй простой и понятный язык
4. При необходимости ссылайся на конкретные статьи НК РФ
5. Если вопрос не относится к налоговому законодательству, вежливо укажи на это

Контекст из Налогового кодекса:
{context}

Вопрос пользователя: {question}

Дай развернутый и точный ответ:"""

MAX_CONTEXT_LENGTH = 12000
MAX_SEARCH_RESULTS = 3
MAX_TOKENS_RESPONSE = 2000

TEMPERATURE = 0.7
TOP_P = 0.9
TOP_K = 40

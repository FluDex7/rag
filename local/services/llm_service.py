import json
import logging

import config
import requests

logger = logging.getLogger(__name__)


class LLMService:

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.OLLAMA_TIMEOUT
        logger.info(f"LLMService инициализирован: {self.model} @ {self.base_url}")
        self._check_ollama_connection()

    def _check_ollama_connection(self):
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name") for m in models]
                if any(self.model in name for name in model_names):
                    logger.info(f"Модель {self.model} найдена")
                else:
                    logger.warning(
                        f"Модель {self.model} не найдена. Доступные: {model_names}"
                    )
            else:
                logger.warning(f"Ollama недоступен (статус {response.status_code})")
        except Exception as e:
            logger.error(f"Ошибка подключения к Ollama: {e}")

    def generate_answer(self, context: str, question: str) -> dict:
        try:
            prompt = config.SYSTEM_PROMPT.format(context=context, question=question)

            logger.info(f"Запрос к Ollama (модель: {self.model})")
            logger.debug(f"Длина промпта: {len(prompt)} символов")

            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": config.TEMPERATURE,
                        "top_p": config.TOP_P,
                        "top_k": config.TOP_K,
                        "num_predict": config.MAX_TOKENS_RESPONSE,
                    },
                },
                timeout=self.timeout,
            )

            if response.status_code != 200:
                error_msg = (
                    f"Ollama вернул ошибку: {response.status_code} - {response.text}"
                )
                logger.error(error_msg)
                return {"answer": None, "tokens_used": 0, "error": error_msg}

            result = response.json()
            answer = result.get("response", "").strip()
            tokens_used = result.get("eval_count", 0) + result.get(
                "prompt_eval_count", 0
            )

            logger.info(f"Ответ получен от Ollama ({tokens_used} токенов)")
            return {"answer": answer, "tokens_used": tokens_used, "error": None}

        except requests.exceptions.Timeout:
            error_msg = f"Таймаут при запросе к Ollama (>{self.timeout}s)"
            logger.error(error_msg)
            return {"answer": None, "tokens_used": 0, "error": error_msg}
        except Exception as e:
            error_msg = f"Ошибка генерации ответа через Ollama: {e}"
            logger.error(error_msg)
            return {"answer": None, "tokens_used": 0, "error": error_msg}

    def generate_answer_stream(self, context: str, question: str):
        try:
            prompt = config.SYSTEM_PROMPT.format(context=context, question=question)

            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {
                        "temperature": config.TEMPERATURE,
                        "top_p": config.TOP_P,
                        "top_k": config.TOP_K,
                        "num_predict": config.MAX_TOKENS_RESPONSE,
                    },
                },
                timeout=self.timeout,
                stream=True,
            )

            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    if "response" in chunk:
                        yield chunk["response"]
                    if chunk.get("done", False):
                        break

        except Exception as e:
            logger.error(f"Ошибка при потоковой генерации: {e}")
            yield f"[Ошибка: {e}]"

from openai import OpenAI
from typing import Optional
import config
import logging

logger = logging.getLogger(__name__)


class OpenAIService:
    """Сервис для работы с OpenAI API"""
    
    def __init__(self):
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.OPENAI_MODEL
        logger.info(f"OpenAIService инициализирован с моделью {self.model}")
    
    def generate_response(self, question: str, context: str) -> tuple[Optional[str], int]:
        """
        Генерация ответа с использованием GPT-4
        
        Args:
            question: Вопрос пользователя
            context: Контекст из векторной БД
        
        Returns:
            Кортеж (ответ, количество использованных токенов)
        """
        try:
            # Формируем промпт
            system_prompt = config.SYSTEM_PROMPT.format(
                context=context,
                question=question
            )
            
            # Вызываем API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Ты помощник-консультант по Налоговому кодексу РФ."},
                    {"role": "user", "content": system_prompt}
                ],
                max_tokens=config.MAX_TOKENS_RESPONSE,
                temperature=0.7
            )
            
            # Извлекаем ответ
            answer = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            
            logger.info(f"Сгенерирован ответ, использовано токенов: {tokens_used}")
            return answer, tokens_used
            
        except Exception as e:
            logger.error(f"Ошибка при генерации ответа OpenAI: {e}")
            return None, 0


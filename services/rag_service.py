from services.qdrant_service import QdrantService
from services.openai_service import OpenAIService
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """Сервис для RAG (Retrieval-Augmented Generation)"""
    
    def __init__(self):
        self.qdrant_service = QdrantService()
        self.openai_service = OpenAIService()
        logger.info("RAGService успешно инициализирован")
    
    def get_answer(self, question: str) -> tuple[str, int, list]:
        """
        Получить ответ на вопрос пользователя
        
        Args:
            question: Вопрос пользователя
        
        Returns:
            Кортеж (ответ, количество токенов, список найденных документов)
        """
        # 1. Ищем релевантный контекст в Qdrant
        logger.info(f"Поиск контекста для вопроса: {question[:50]}...")
        context, search_results = self.qdrant_service.get_context_for_prompt(question)
        
        # 2. Генерируем ответ через OpenAI
        logger.info("Генерация ответа через OpenAI...")
        answer, tokens_used = self.openai_service.generate_response(question, context)
        
        if not answer:
            answer = "Извините, произошла ошибка при генерации ответа. Попробуйте позже."
            tokens_used = 0
        
        return answer, tokens_used, search_results


import logging

from services.openai_service import OpenAIService
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class RAGService:

    def __init__(self):
        self.qdrant_service = QdrantService()
        self.openai_service = OpenAIService()
        logger.info("RAGService инициализирован")

    def get_answer(self, question: str) -> tuple[str, int, list]:
        logger.info(f"Поиск контекста для вопроса: {question[:50]}...")
        context, search_results = self.qdrant_service.get_context_for_prompt(question)

        answer, tokens_used = self.openai_service.generate_response(question, context)

        if not answer:
            answer = (
                "Извините, произошла ошибка при генерации ответа. Попробуйте позже."
            )
            tokens_used = 0

        return answer, tokens_used, search_results

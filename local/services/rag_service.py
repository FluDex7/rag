import logging

from services.llm_service import LLMService
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class RAGService:

    def __init__(self):
        self.qdrant_service = QdrantService()
        self.llm_service = LLMService()
        logger.info("RAGService инициализирован")

    def get_answer(self, question: str) -> tuple[str, int, list]:
        logger.info(f"Поиск контекста для вопроса: {question[:50]}...")
        context, search_results = self.qdrant_service.get_context_for_prompt(question)

        result = self.llm_service.generate_answer(context, question)

        answer = result.get("answer")
        tokens_used = result.get("tokens_used", 0)
        error = result.get("error")

        if error or not answer:
            answer = "Извините, произошла ошибка при генерации ответа."
            tokens_used = 0

        return answer, tokens_used, search_results

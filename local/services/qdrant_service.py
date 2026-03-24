import logging
from typing import Dict, List
from uuid import uuid4

import config
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class QdrantService:

    def __init__(self):
        self.client = QdrantClient(url=config.QDRANT_URL, check_compatibility=False)
        self.collection_name = config.QDRANT_COLLECTION_NAME
        self.embedding_model = SentenceTransformer(
            config.LOCAL_EMBEDDING_MODEL, device=config.EMBEDDING_DEVICE
        )
        self.embedding_dimension = config.EMBEDDING_DIMENSION
        logger.info(f"QdrantService инициализирован, коллекция: {self.collection_name}")

    def _get_embedding(self, text: str) -> List[float]:
        try:
            max_chars = config.EMBEDDING_MAX_TOKENS * 4
            if len(text) > max_chars:
                text = text[:max_chars]
                logger.warning(f"Текст обрезан до {max_chars} символов")

            return self.embedding_model.encode(text).tolist()
        except Exception as e:
            logger.error(f"Ошибка получения embedding: {e}")
            raise

    def search(self, query: str, limit: int = None) -> List[Dict]:
        if limit is None:
            limit = config.MAX_SEARCH_RESULTS

        try:
            query_vector = self._get_embedding(query)

            response = self.client.query_points(
                collection_name=self.collection_name, query=query_vector, limit=limit
            )

            formatted_results = []
            for result in response.points:
                formatted_results.append(
                    {
                        "score": result.score,
                        "text": result.payload.get("text", ""),
                        "chapter_title": result.payload.get("chapter_title", ""),
                        "article_title": result.payload.get("article_title", ""),
                        "article_content": result.payload.get("article_content", ""),
                        "word_count": result.payload.get("word_count", 0),
                    }
                )

            logger.info(
                f"Найдено {len(formatted_results)} результатов для запроса: {query[:50]}..."
            )
            return formatted_results

        except Exception as e:
            logger.error(f"Ошибка поиска в Qdrant: {e}")
            return []

    def add_qa(self, question: str, answer: str, added_by: str = None) -> bool:
        try:
            qa_text = f"Вопрос: {question}\nОтвет: {answer}"
            vector = self._get_embedding(qa_text)

            point = PointStruct(
                id=str(uuid4()),
                vector=vector,
                payload={
                    "text": qa_text,
                    "chapter_title": "Добавленные Q&A",
                    "article_title": question,
                    "article_content": answer,
                    "word_count": len(qa_text.split()),
                    "is_qa": True,
                    "added_by": added_by or "unknown",
                },
            )

            self.client.upsert(collection_name=self.collection_name, points=[point])

            logger.info(f"Q&A добавлено в Qdrant: {question[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Ошибка добавления Q&A в Qdrant: {e}")
            return False

    def get_context_for_prompt(
        self, query: str, max_length: int = None
    ) -> tuple[str, List[Dict]]:
        if max_length is None:
            max_length = config.MAX_CONTEXT_LENGTH

        results = self.search(query, limit=config.MAX_SEARCH_RESULTS)

        if not results:
            return "Контекст не найден.", []

        context_parts = []
        current_length = 0

        for i, result in enumerate(results, 1):
            result_text = f"{result['article_title']}\n{result['article_content']}"

            if current_length + len(result_text) > max_length:
                break

            context_parts.append(f"[{i}] {result_text}")
            current_length += len(result_text)

        context = "\n\n".join(context_parts)
        logger.debug(f"Контекст: {len(context)} символов")

        return context, results

    def clear_collection(self) -> bool:
        try:
            collections = self.client.get_collections().collections
            if any(c.name == self.collection_name for c in collections):
                self.client.delete_collection(self.collection_name)
                logger.info(f"Коллекция {self.collection_name} удалена")
            return True
        except Exception as e:
            logger.error(f"Ошибка при очистке коллекции: {e}")
            return False

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from typing import List, Dict
from uuid import uuid4
from openai import OpenAI
import config
import logging

logger = logging.getLogger(__name__)


class QdrantService:
    """Сервис для работы с векторной базой данных Qdrant"""
    
    def __init__(self):
        self.client = QdrantClient(url=config.QDRANT_URL, check_compatibility=False)
        self.collection_name = config.QDRANT_COLLECTION_NAME
        
        # Используем OpenAI для embeddings
        self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.embedding_model = config.OPENAI_EMBEDDING_MODEL
        self.embedding_dimension = config.EMBEDDING_DIMENSION
        
        logger.info(f"QdrantService инициализирован:")
        logger.info(f"  - Qdrant URL: {config.QDRANT_URL}")
        logger.info(f"  - Коллекция: {self.collection_name}")
        logger.info(f"  - Embeddings: OpenAI ({self.embedding_model})")
    
    def _get_embedding(self, text: str) -> List[float]:
        """
        Получить embedding через OpenAI API
        
        Args:
            text: Текст для преобразования в вектор
            
        Returns:
            Вектор embedding
        """
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Ошибка получения embedding от OpenAI: {e}")
            raise
    
    def search(self, query: str, limit: int = None) -> List[Dict]:
        """
        Поиск в векторной базе данных
        
        Args:
            query: Поисковый запрос
            limit: Количество результатов (по умолчанию из config)
        
        Returns:
            Список найденных документов с метаданными
        """
        if limit is None:
            limit = config.MAX_SEARCH_RESULTS
        
        try:
            # Создаем embedding для запроса через OpenAI
            query_vector = self._get_embedding(query)
            
            # Выполняем поиск используя query_points
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit
            )
            
            # Форматируем результаты
            formatted_results = []
            for result in response.points:
                formatted_results.append({
                    'score': result.score,
                    'text': result.payload.get('text', ''),
                    'chapter_title': result.payload.get('chapter_title', ''),
                    'article_title': result.payload.get('article_title', ''),
                    'article_content': result.payload.get('article_content', ''),
                    'word_count': result.payload.get('word_count', 0)
                })
            
            logger.info(f"Найдено {len(formatted_results)} результатов для запроса: {query[:50]}...")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Ошибка поиска в Qdrant: {e}")
            return []
    
    def add_qa(self, question: str, answer: str, added_by: str = None) -> bool:
        """
        Добавление Q&A пары в векторную базу
        
        Args:
            question: Вопрос
            answer: Ответ
            added_by: Кто добавил (для метаданных)
        
        Returns:
            True если успешно добавлено
        """
        try:
            # Формируем текст для embedding
            qa_text = f"Вопрос: {question}\nОтвет: {answer}"
            
            # Создаем embedding через OpenAI
            vector = self._get_embedding(qa_text)
            
            # Создаем point
            point = PointStruct(
                id=str(uuid4()),
                vector=vector,
                payload={
                    'text': qa_text,
                    'chapter_title': 'Добавленные Q&A',
                    'article_title': question,
                    'article_content': answer,
                    'word_count': len(qa_text.split()),
                    'is_qa': True,
                    'added_by': added_by or 'unknown'
                }
            )
            
            # Добавляем в коллекцию
            self.client.upsert(
                collection_name=self.collection_name,
                points=[point]
            )
            
            logger.info(f"Q&A добавлено в Qdrant: {question[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка добавления Q&A в Qdrant: {e}")
            return False
    
    def get_context_for_prompt(self, query: str, max_length: int = None) -> tuple[str, List[Dict]]:
        """
        Получить контекст из Qdrant для промпта
        
        Args:
            query: Поисковый запрос
            max_length: Максимальная длина контекста (символов)
        
        Returns:
            Кортеж (отформатированный контекст для промпта, список найденных документов)
        """
        if max_length is None:
            max_length = config.MAX_CONTEXT_LENGTH
        
        # Ищем релевантные документы
        results = self.search(query, limit=config.MAX_SEARCH_RESULTS)
        
        if not results:
            return "Контекст не найден.", []
        
        # Формируем контекст
        context_parts = []
        current_length = 0
        
        for i, result in enumerate(results, 1):
            # Формируем текст результата
            result_text = f"{result['article_title']}\n{result['article_content']}"
            
            # Проверяем, не превысим ли лимит
            if current_length + len(result_text) > max_length:
                break
            
            context_parts.append(f"[{i}] {result_text}")
            current_length += len(result_text)
        
        context = "\n\n".join(context_parts)
        logger.debug(f"Сформирован контекст длиной {len(context)} символов")
        
        return context, results
    
    def clear_collection(self) -> bool:
        """
        Очищает коллекцию (удаляет все векторы)
        
        Returns:
            True если успешно очищено
        """
        try:
            collections = self.client.get_collections().collections
            if any(c.name == self.collection_name for c in collections):
                self.client.delete_collection(self.collection_name)
                logger.info(f"Коллекция {self.collection_name} удалена")
                return True
            else:
                logger.warning(f"Коллекция {self.collection_name} не существует")
                return True  # Считаем успехом, если коллекции нет
        except Exception as e:
            logger.error(f"Ошибка при очистке коллекции: {e}")
            return False


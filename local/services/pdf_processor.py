import logging
import re
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import config
import pymupdf
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


def extract_chapter_number(chapter_title: str) -> float:
    match = re.match(r"Глава (\d+(?:\.\d+)?)", chapter_title)
    if match:
        return float(match.group(1))
    return 0.0


def pdf_to_text(pdf_path: str) -> str:
    pdf_document = pymupdf.open(pdf_path)
    full_text = []

    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        text = page.get_text()
        full_text.append(f"\n--- Страница {page_num + 1} ---\n")
        full_text.append(text)

    result_text = "".join(full_text)
    pdf_document.close()

    return result_text


def clean_text(input_text: str) -> str:
    text = input_text

    text = re.sub(r"--- Страница \d+ ---\n?", "", text)
    text = re.sub(
        r"^Бесплатная юридическая консультация по телефонам:\s*\n?",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^[•\s]*\d+\s*\(\d{3,4}\)\s*\d{3}[-\s]?\d{2}[-\s]?\d{2}.*$\n?",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^Комментарии к статьям на сайте\s*\n?", "", text, flags=re.MULTILINE
    )
    text = re.sub(r"^.*https?://[^\s]+.*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def split_into_chapters_and_articles(text: str) -> Dict:
    chapter_pattern = r"^(Глава \d+(?:\.\d+)?\..*)$"
    chapters_matches = list(re.finditer(chapter_pattern, text, re.MULTILINE))

    header = text[: chapters_matches[0].start()].strip() if chapters_matches else ""
    chapters = []

    for i, chapter_match in enumerate(chapters_matches):
        chapter_start = chapter_match.start()
        chapter_end = (
            chapters_matches[i + 1].start()
            if i + 1 < len(chapters_matches)
            else len(text)
        )
        chapter_text = text[chapter_start:chapter_end].strip()

        lines = chapter_text.split("\n")
        chapter_title_lines = [lines[0]]

        for line in lines[1:]:
            if re.match(r"^Статья\s*$", line.strip()) or re.match(
                r"^Статья \d+(?:\.\d+)?\.", line
            ):
                break
            if line.strip():
                chapter_title_lines.append(line)
            else:
                break

        chapter_title = " ".join(chapter_title_lines).strip()

        article_pattern = r"^(?:Статья\s*$|Статья \d+(?:\.\d+)?\.)"
        articles_matches = list(
            re.finditer(article_pattern, chapter_text, re.MULTILINE)
        )

        articles = []

        for j, article_match in enumerate(articles_matches):
            article_start = article_match.start()
            article_end = (
                articles_matches[j + 1].start()
                if j + 1 < len(articles_matches)
                else len(chapter_text)
            )
            article_text = chapter_text[article_start:article_end].strip()

            article_lines = article_text.split("\n")
            article_title_lines = []
            article_content_start = 0

            for idx, line in enumerate(article_lines):
                line_stripped = line.strip()
                if re.match(r"^\d+\.\s+\S", line_stripped):
                    article_content_start = idx
                    break
                if line_stripped:
                    article_title_lines.append(line_stripped)

            article_title = " ".join(article_title_lines)
            article_content = "\n".join(article_lines[article_content_start:]).strip()

            if article_title and (article_content or len(article_title_lines) > 1):
                articles.append({"title": article_title, "content": article_content})

        chapters.append({"title": chapter_title, "articles": articles})

    return {"header": header, "chapters": chapters}


def select_chapters_with_metadata(
    structured_data: Dict,
    chapters_filter=None,
    max_words: Optional[int] = None,
    min_words: Optional[int] = None,
) -> List[Dict]:
    selected_chapter_numbers: set[float] = set()
    chapter_range = None
    select_all = False

    if chapters_filter is None or chapters_filter == "*" or chapters_filter == "все":
        select_all = True
    elif isinstance(chapters_filter, int):
        selected_chapter_numbers.add(float(chapters_filter))
    elif isinstance(chapters_filter, (tuple, list)) and len(chapters_filter) == 2:
        chapter_range = (float(chapters_filter[0]), float(chapters_filter[1]))
    elif isinstance(chapters_filter, str):
        if "-" in chapters_filter:
            parts = chapters_filter.split("-")
            chapter_range = (float(parts[0].strip()), float(parts[1].strip()))
        else:
            selected_chapter_numbers.add(float(chapters_filter))

    result = []

    for chapter in structured_data["chapters"]:
        chapter_title = chapter["title"]
        chapter_num = extract_chapter_number(chapter_title)

        include_chapter = False
        if select_all:
            include_chapter = True
        elif chapter_range:
            include_chapter = chapter_range[0] <= chapter_num <= chapter_range[1]
        else:
            include_chapter = chapter_num in selected_chapter_numbers

        if include_chapter:
            for article in chapter["articles"]:
                article_title = article["title"]
                article_content = article["content"]
                full_text = f"{chapter_title}. {article_title}. {article_content}"
                word_count = len(full_text.split())

                if max_words and word_count > max_words:
                    continue
                if min_words and word_count < min_words:
                    continue

                result.append(
                    {
                        "text": full_text,
                        "chapter_title": chapter_title,
                        "chapter_number": chapter_num,
                        "article_title": article_title,
                        "article_content": article_content,
                        "word_count": word_count,
                    }
                )

    return result


def process_pdf_and_upload_to_qdrant(
    pdf_path: str, chapter_filter=None, max_words: Optional[int] = None
) -> tuple[bool, str]:
    try:
        if not Path(pdf_path).exists():
            return False, f"Файл {pdf_path} не найден"

        logger.info(f"Начало обработки PDF: {pdf_path}")

        raw_text = pdf_to_text(pdf_path)
        logger.info(f"Извлечено {len(raw_text)} символов")

        clean_txt = clean_text(raw_text)
        logger.info(f"После очистки: {len(clean_txt)} символов")

        structured_data = split_into_chapters_and_articles(clean_txt)
        total_chapters = len(structured_data["chapters"])
        total_articles = sum(len(ch["articles"]) for ch in structured_data["chapters"])
        logger.info(f"Найдено глав: {total_chapters}, статей: {total_articles}")

        articles = select_chapters_with_metadata(
            structured_data, chapters_filter=chapter_filter, max_words=max_words
        )
        logger.info(f"Отобрано статей: {len(articles)}")

        if not articles:
            return False, "Не найдено статей для загрузки"

        client = QdrantClient(url=config.QDRANT_URL, check_compatibility=False)
        embedding_model = SentenceTransformer(
            config.LOCAL_EMBEDDING_MODEL, device=config.EMBEDDING_DEVICE
        )
        vector_size = config.EMBEDDING_DIMENSION
        collection_name = config.QDRANT_COLLECTION_NAME
        max_chars = config.EMBEDDING_MAX_TOKENS * 4

        collections = client.get_collections().collections
        if any(c.name == collection_name for c in collections):
            client.delete_collection(collection_name)

        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info(f"Коллекция {collection_name} создана")

        batch_size = 32
        points = []
        for i, article in enumerate(articles):
            try:
                text = article["text"]
                if len(text) > max_chars:
                    text = text[:max_chars]
                vector = embedding_model.encode(text).tolist()
            except Exception as e:
                logger.error(f"Ошибка создания embedding для статьи {i+1}: {e}")
                continue

            point = PointStruct(
                id=str(uuid4()),
                vector=vector,
                payload={
                    "text": article["text"],
                    "chapter_title": article["chapter_title"],
                    "chapter_number": article["chapter_number"],
                    "article_title": article["article_title"],
                    "article_content": article["article_content"],
                    "word_count": article["word_count"],
                },
            )
            points.append(point)

            if len(points) >= batch_size or i == len(articles) - 1:
                client.upsert(collection_name=collection_name, points=points)
                logger.info(f"Загружено: {i + 1}/{len(articles)}")
                points = []

        collection_info = client.get_collection(collection_name)
        total_points = collection_info.points_count

        success_msg = (
            f"Обновление знаний завершено!\n\n"
            f"Статистика:\n"
            f"- Обработано глав: {total_chapters}\n"
            f"- Всего статей: {total_articles}\n"
            f"- Загружено статей: {len(articles)}\n"
            f"- Векторов в Qdrant: {total_points}"
        )

        logger.info(f"Успешно загружено {len(articles)} статей в Qdrant")
        return True, success_msg

    except Exception as e:
        error_msg = f"Ошибка при обновлении знаний: {str(e)}"
        logger.error(f"Ошибка обработки PDF: {e}", exc_info=True)
        return False, error_msg

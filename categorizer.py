"""Определение категории вакансии по тексту сообщения."""
import re

from config import CATEGORIES, EXCLUDE_PATTERNS, VACANCY_SIGNALS


def normalize(text: str) -> str:
    """Приводим текст к нижнему регистру и убираем лишние пробелы."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _kw_hit(text: str, kw: str) -> bool:
    """Короткие ключи (например, 'qa') ищем как отдельное слово,
    длинные — обычным вхождением подстроки."""
    if len(kw) <= 3 and kw.isalnum():
        return re.search(rf"\b{re.escape(kw)}\b", text) is not None
    return kw in text


def is_resumo_or_resume(text: str) -> bool:
    """Сообщение похоже на пост фрилансера, ищущего работу (а не на заказ)."""
    t = normalize(text)
    return any(pat in t for pat in EXCLUDE_PATTERNS)


def categorize(text: str) -> list[str]:
    """Возвращает ключи категорий, под которые подходит текст."""
    t = f" {normalize(text)} "
    return [key for key, cat in CATEGORIES.items() if any(_kw_hit(t, kw) for kw in cat["keywords"])]


def looks_like_vacancy(text: str) -> bool:
    """Сообщение похоже на заказ/вакансию, а не на саморекламу."""
    t = normalize(text)
    if is_resumo_or_resume(t):
        return False
    return any(sig in t for sig in VACANCY_SIGNALS)


def filter_vacancy(text: str) -> list[str] | None:
    """Главная точка входа: ключи категорий или None, если сообщение не подходит."""
    if len(text.strip()) < 15:  # слишком короткие сообщения не берем
        return None
    cats = categorize(text)
    if not cats:
        return None
    if not looks_like_vacancy(text):
        return None
    return cats


def format_categories(keys: list[str]) -> str:
    return ", ".join(
        f"{CATEGORIES[k]['emoji']} {CATEGORIES[k]['title']}" for k in keys
    )

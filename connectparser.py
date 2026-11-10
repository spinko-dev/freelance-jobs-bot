"""
Парсер ленты заказов connect.tg (агрегатор заказов из Telegram-чатов и Kwork).

Читает разделы вида https://connect.tg/orders/development/chatbot-developer,
обе фильтрует новые заказы (по UUID из URL), складывает их в очередь SQLite
с категорией раздела. Описание и бюджет берутся из карточки прямо в HTML.
"""
import logging
import re
import zlib
from html import unescape

from categorizer import format_categories
from config import CATEGORIES
from db import has_history, is_seen, mark_seen, push_job

log = logging.getLogger("connectparser")

# На каком клиенте (прямом/через прокси) последний раз успешн ополучили раздел
_preferred_client: dict[str, object] = {}

BASE = "https://connect.tg"

# Какие разделы слушать и в какую категорию отправлять заказы.
# Добавите сюда проводник новый (slug и category из словаря CATEGORIES в config.py) — заработает сразу.
SITE_SECTIONS: list[tuple[str, list[str]]] = [
    ("development/chatbot-developer", ["chatbots"]),
    ("development/telegram-developer", ["chatbots", "scripts"]),
    ("development/qa-engineer", ["qa"]),
    ("design/motion-designer", ["video_edit"]),
    # ("development/fullstack-developer", ["scripts"]),
    # ("development/frontend-developer", ["scripts"]),
    # ("development/devops-engineer", ["scripts"]),
]

# chat_id для таблицы seen — один на весь connect.tg, чтобы заказ из двух
# разделов не приходил дважды.
CHAT_ID = zlib.crc32(b"connect.tg") | 0x80000000

ARTICLE_RE = re.compile(r'<article class="order-card-shadow.*?</article>', re.S)
UUID_RE = re.compile(r'href="/orders/([0-9a-f-]{36})"')
TITLE_RE = re.compile(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', re.S)
DESC_RE = re.compile(r'<p class="line-clamp-2[^"]*"[^>]*>(.*?)</p>', re.S)
BUDGET_RE = re.compile(
    r'>(ОТ|ДО)</span>\s*<span class="font-semibold[^"]*">(.*?)</span>', re.S
)
WHITESPACE_RE = re.compile(r"\s+")


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return unescape(text).strip()


def parse_cards(html: str) -> list[dict]:
    """Разбирает HTML раздела: [{uuid, title, desc, budget}, ...]."""
    orders = []
    seen_uuids = set()
    for card in ARTICLE_RE.findall(html):
        uuid_m = UUID_RE.search(card)
        title_m = TITLE_RE.search(card)
        if not uuid_m or not title_m:
            continue
        uuid = uuid_m.group(1)
        if uuid in seen_uuids:
            continue
        seen_uuids.add(uuid)
        desc_m = DESC_RE.search(card)
        budget_parts = [
            (kind.lower(), strip_tags(amount))
            for kind, amount in BUDGET_RE.findall(card)
        ]
        budget = " — ".join(f"{k} {v}" for k, v in budget_parts) if budget_parts else ""

        title = WHITESPACE_RE.sub(" ", strip_tags(title_m.group(1)))
        desc = WHITESPACE_RE.sub(" ", strip_tags(desc_m.group(1))) if desc_m else ""
        lines = [title]
        if budget:
            lines.append(f"Бюджет: {budget}")
        if desc:
            lines.append(desc)
        orders.append({
            "uuid": uuid,
            "title": title,
            "text": "\n".join(lines)[:3800],
        })
    return orders


def poll_section(client, section: str, categories: list[str], alt_client=None) -> list[dict]:
    """Забирает и разбирает один раздел. Возвращает список заказов или [].
    client — основной (обычно через прокси). Если через него не вышло и есть
    alt_client — пробуем напрямую: connect.tg провайдеры обычно НЕ блокируют.
    """
    prefer = _preferred_client.get(section)
    if prefer is not None:
        other = alt_client if prefer is client else client
        candidates = [c for c in (prefer, other) if c]
    else:
        candidates = [c for c in (client, alt_client) if c]
    last_error = None
    resp = None
    used = None
    for c in candidates:
        url = f"{BASE}/orders/{section}"
        try:
            resp = c.get(url)
            used = c
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if resp is None:
        log.warning(
            "connect.tg %s: запрос не прошёл (%s) — проверьте Throne/check.bat",
            section, str(last_error)[:100],
        )
        return []
    _preferred_client[section] = used
    tag = "через прокси" if used is client else "напрямую"
    log.info("connect.tg %s: связь OK (%s)", section, tag)
    if resp.status_code != 200:
        log.warning("connect.tg %s: HTTP %s", section, resp.status_code)
        return []

    return parse_cards(resp.text)


def poll_sections(client, alt_client=None) -> int:
    """Обходит ВСЕ разделы SITE_SECTIONS (alt_client — запасной без прокси).

    Важно: точка отсчёта снимается по ОБЩЕМУ списку заказов всех разделов,
    чтобы при первом запуске не разослать всю старую ленту разом.
    """
    all_orders: list[tuple[str, list[str], dict]] = []
    for section, categories in SITE_SECTIONS:
        try:
            orders = poll_section(client, section, categories, alt_client=alt_client)
        except Exception:  # noqa: BLE001
            log.exception("connect.tg: ошибка раздела %s", section)
            continue
        for order in orders:
            all_orders.append((section, categories, order))

    if not all_orders:
        return 0

    # Первый контакт с сайтом: молча запоминаем всё, что есть сейчас
    if not has_history(CHAT_ID):
        for _section, _cats, order in all_orders:
            mark_seen(CHAT_ID, zlib.crc32(order["uuid"].encode()))
        log.info(
            "connect.tg: точка отсчёта зафиксирована (%d заказов по %d разделам). Дальше — только новое.",
            len(all_orders), len(SITE_SECTIONS),
        )
        return 0

    sent = 0
    for _section, categories, order in all_orders:
        msg_id = zlib.crc32(order["uuid"].encode())
        if not mark_seen(CHAT_ID, msg_id):
            continue
        key_check = [c for c in categories if c in CATEGORIES] or ["scripts"]
        push_job(
            categories=key_check,
            chat_title="Connect (connect.tg)",
            chat_link=f"{BASE}/orders/{order['uuid']}",
            text=order["text"],
        )
        sent += 1
        log.info(
            "Новый заказ connect.tg: %s → %s",
            order["title"][:60],
            format_categories(key_check),
        )
    return sent

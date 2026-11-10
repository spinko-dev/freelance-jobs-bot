"""
Веб-парсер публичных каналов Telegram — БЕЗ api_id/api_hash и без логина.

Читает публичную веб-версию каналов (https://t.me/s/имя_канала) обычными
HTTP-запросами, отбирает вакансии по категориям и складывает в очередь,
откуда их рассылает bot.py.

Запуск: python webparser.py   (или start_webparser.bat)
В .env указывайте каналы в SOURCE_CHATS через запятую, например:
    SOURCE_CHATS=@freelance_tut,@it_vacancy
Имена с числовыми ID (вида -1001234...) здесь не работают — только @username.
"""
import logging
import re
import time
import zlib
from html import unescape

import httpx

from categorizer import filter_vacancy, format_categories
from config import PROXY_URL, SOURCE_CHATS, WEB_POLL_INTERVAL
from connectparser import poll_sections
from db import cleanup_queue, has_history, is_seen, mark_seen, push_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("webparser")

# Сколько страниц назад уходим, чтобы ничего не пропустить
# между двумя соседними опросами (20 постов на страницу).
MAX_BACKTRACK_PAGES = 4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ru,en;q=0.9",
}

POST_RE = re.compile(r'data-post="([A-Za-z0-9_]+)/(\d+)"')
TEXT_RE = re.compile(r'<div class="[^"]*tgme_widget_message_text[^"]*"[^>]*>')


# ------------------------------------------------------------------
#  Разбор HTML
# ------------------------------------------------------------------

def _extract_div_balanced(html: str, start: int) -> str | None:
    """Возвращает содержимое тега <div>, открытого в позиции start,
    с учётом вложенности (просто искать </div> недостаточно)."""
    depth = 1
    pos = start + 4  # пропускаем '<div'
    while pos < len(html):
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start:nxt_close]
            pos = nxt_close + 6
    return None


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out).strip()


def parse_posts(html: str) -> list[tuple[int, str]]:
    """Извлекает (номер_поста, текст) в порядке возрастания."""
    posts = []
    for m in POST_RE.finditer(html):
        try:
            seq = int(m.group(2))
        except ValueError:
            continue
        # Текст сообщения находится внутри текущего блока (до следующего data-post)
        nxt = POST_RE.search(html, m.end())
        block_end = nxt.start() if nxt else len(html)
        block = html[m.end():block_end]
        tm = TEXT_RE.search(block)
        if not tm:
            continue  # пост без текста (например, только фото или сервисное)
        raw = _extract_div_balanced(block, tm.start())
        if raw is None:
            continue
        text = strip_tags(raw)
        if text:
            posts.append((seq, text))
    # возможны дубли (закреплённые сообщения рисуются дважды) — оставляем по одному
    dedup: dict[int, str] = {}
    for seq, text in posts:
        dedup.setdefault(seq, text)
    return sorted(dedup.items())


def chat_numeric_id(username: str) -> int:
    """Стабильный числовой ID канала для таблицы seen."""
    return zlib.crc32(username.lower().encode()) | 0x40000000


# ------------------------------------------------------------------
#  Опрос каналов
# ------------------------------------------------------------------

def fetch_posts(client: httpx.Client, username: str) -> list[tuple[int, str]] | None:
    url = f"https://t.me/s/{username}"
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        log.warning("Сеть недоступна (%s): %s", username, exc)
        return None
    if resp.status_code == 404:
        log.warning("Канал @%s не найден или не публичный — проверьте имя.", username)
        return None
    if resp.status_code == 429:
        retry = int(resp.headers.get("Retry-After", 60))
        log.warning("Telegram просит подождать %s сек (429) — канал @%s пропускаю.", retry, username)
        return None
    if resp.status_code != 200:
        log.warning("HTTP %s для @%s", resp.status_code, username)
        return None
    return parse_posts(resp.text)


def poll_channel(client: httpx.Client, username: str) -> int:
    """Опрашивает канал (в т.ч. назад по истории), возвращает число новых вакансий."""
    chat_id = chat_numeric_id(username)
    url = f"https://t.me/s/{username}"

    collected: dict[int, str] = {}
    goto_oldest = None
    for page in range(MAX_BACKTRACK_PAGES + 1):
        page_url = url if goto_oldest is None else f"{url}?before={goto_oldest}"
        try:
            resp = client.get(page_url)
        except httpx.HTTPError as exc:
            log.warning("Ошибка запроса @%s: %s", username, exc)
            return 0
        if resp.status_code == 404:
            log.warning("Канал @%s не найден или непубличный.", username)
            return 0
        if resp.status_code == 429:
            log.warning("429 (слишком часто) — канал @%s пропущу до следующего круга.", username)
            return 0
        if resp.status_code != 200:
            log.warning("HTTP %s для @%s", resp.status_code, username)
            return 0

        page_posts = parse_posts(resp.text)
        if not page_posts:
            break
        fresh = [(s, t) for s, t in page_posts if s not in collected]
        if not fresh:
            break
        collected.update(dict(fresh))
        oldest = min(s for s, _ in page_posts)
        # Если на этой странице есть хотя бы одно уже виденное сообщение —
        # дальше назад идти смысла нет. Иначе — пробуем предыдущую страницу.
        if any(is_seen(chat_id, s) for s, _ in page_posts) or goto_oldest == oldest:
            break
        goto_oldest = oldest

    if not collected:
        return 0

    first_run = not has_history(chat_id)
    if first_run:
        # Первый круг: запоминаем текущие посты как точку отсчёта,
        # ничего не отправляем — иначе бот зальёт вам неделю старых вакансий.
        for seq, _ in sorted(collected.items()):
            mark_seen(chat_id, seq)
        log.info("@%s: зафиксирована точка отсчёта (%d постов). Дальше — только новое.", username, len(collected))
        return 0

    sent = 0
    for seq, text in sorted(collected.items()):
        if not mark_seen(chat_id, seq):
            continue  # уже отправляли
        cats = filter_vacancy(text)
        if not cats:
            continue
        push_job(
            categories=cats,
            chat_title=f"@{username}",
            chat_link=f"https://t.me/{username}/{seq}",
            text=text[:3500],
        )
        sent += 1
        log.info("Вакансия #%d с @%s → %s", seq, username, format_categories(cats))
    return sent


def main() -> None:
    sources = []
    for raw in SOURCE_CHATS:
        if isinstance(raw, str):
            sources.append(raw)
        else:
            log.warning("Числовой ID %s нельзя парсить через веб — используйте @username.", raw)
    if not sources:
        raise SystemExit(
            "Ошибка: в .env SOURCE_CHATS пуст или содержит только числовые ID.\n"
            "Укажите @username публичных КАНАЛОВ, например:\n"
            "SOURCE_CHATS=@freelance_vakansii,@distantsionnaya_rabota"
        )

    log.info(
        "Веб-парсер запущен: %d канал(ов), опрос каждые %d сек.",
        len(sources),
        WEB_POLL_INTERVAL,
    )
    round_no = 0
    plain_client = httpx.Client(
        headers=HEADERS, timeout=30, follow_redirects=True,
    )  # без прокси — connect.tg провайдер обычно не блокирует

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True, proxy=PROXY_URL) as client:
        while True:
            round_no += 1
            total = 0
            for username in sources:
                try:
                    total += poll_channel(client, username)
                except Exception:  # noqa: BLE001
                    log.exception("Ошибка опроса @%s", username)
                time.sleep(2)  # вежливая пауза между каналами

            # connect.tg — лента заказов (см. connectparser.py, SITE_SECTIONS)
            try:
                total += poll_sections(client, alt_client=plain_client)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка опроса connect.tg")
            log.info("Круг %d завершён: новых вакансий — %d. Следующий опрос через %d сек.", round_no, total, WEB_POLL_INTERVAL)
            cleanup_queue()
            time.sleep(WEB_POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Веб-парсер остановлен.")

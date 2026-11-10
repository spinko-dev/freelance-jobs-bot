"""
Парсер открытых чатов: юзер-клиент (Telethon) слушает чаты из SOURCE_CHATS,
отбирает вакансии по категориям и пишет их в очередь (SQLite), откуда их
забирает bot.py и рассылает подписчикам.

Первый запуск: предложит войти по номеру телефона и коду из Telegram,
создаст файл сессии — дальше запускается без логина.
"""
import asyncio
import logging

from telethon import TelegramClient, events

import categorizer
from config import API_HASH, API_ID, SESSION_NAME, SOURCE_CHATS, MIN_MESSAGE_LEN
from categorizer import filter_vacancy, format_categories
from db import cleanup_seen, cleanup_queue, mark_seen, push_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("parser")


def build_message_link(chat, message_id: int) -> str | None:
    """Пытаемся собрать прямую ссылку на сообщение (работает для публичных чатов)."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    return None


def truncate(text: str, limit: int = 3500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def process_message(client: TelegramClient, chat, event) -> None:
    text = (event.raw_text or "").strip()
    if len(text) < MIN_MESSAGE_LEN:
        return
    if not mark_seen(chat.id, event.message.id):
        return  # уже видели такое сообщение
    cats = filter_vacancy(text)
    if not cats:
        return
    push_job(
        categories=cats,
        chat_title=getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id),
        chat_link=build_message_link(chat, event.message.id),
        text=truncate(text),
    )
    log.info("Новая вакансия (%s) из чата «%s»", format_categories(cats), getattr(chat, "title", chat.id))


async def main() -> None:
    if not API_ID or not API_HASH:
        raise SystemExit("Ошибка: заполните API_ID и API_HASH в .env (см. .env.example).")
    if not SOURCE_CHATS:
        raise SystemExit(
            "Ошибка: укажите чаты-источники в .env, например:\n"
            "SOURCE_CHATS=@freelance_vakansii,@it_vacancy"
        )

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    log.info("Юзер-клиент запущен как %s", (await client.get_me()).first_name)

    entities = []
    for source in SOURCE_CHATS:
        try:
            entities.append(await client.get_entity(source))
            log.info("Подключён чат-источник: %s", source)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось подключить %s: %s", source, exc)

    if not entities:
        raise SystemExit("Ни один чат из SOURCE_CHATS не подключён — проверьте имена/ID.")

    @client.on(events.NewMessage(chats=entities))
    async def on_new_message(event) -> None:
        try:
            process_message(client, await event.get_chat(), event)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обработки сообщения")

    # Периодическая чистка баз
    async def cleaner() -> None:
        while True:
            await asyncio.sleep(6 * 3600)
            cleanup_seen()
            cleanup_queue()

    asyncio.create_task(cleaner())

    log.info("Слежу за %d чат(ами). Оставьте окно открытым.", len(entities))
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Парсер остановлен.")

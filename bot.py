"""
Telegram-бот вакансий: работает напрямую с Telegram Bot API (long polling),
без тяжёлых фреймворков — достаточно httpx.

Слушает очередь SQLite и рассылает новые вакансии пользователям
с учётом их включённых категорий.

Запуск: python bot.py
Команды: /start — меню, /settings — вкл/выкл категории, /stats — статус.
"""
import asyncio
import logging
import sqlite3
import time

import httpx

import categorizer
from config import BOT_TOKEN, CATEGORIES, OWNER_ID, PROXY_URL, QUEUE_POLL_INTERVAL
import db
from db import ensure_user, fetch_pending, get_settings, set_all, set_category

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

API = "https://api.telegram.org/bot{token}/{method}"


# ------------------------------------------------------------------
#  Клавиатуры
# ------------------------------------------------------------------

def build_settings_kb(user_id: int) -> dict:
    enabled = get_settings(user_id)
    rows = [
        [
            {
                "text": f"{'✅' if enabled[key] else '▫️'} {cat['emoji']} {cat['title']}",
                "callback_data": f"toggle:{key}",
            }
        ]
        for key, cat in CATEGORIES.items()
    ]
    rows.append(
        [
            {"text": "✅ Включить все", "callback_data": "all:1"},
            {"text": "◻️ Выключить все", "callback_data": "all:0"},
        ]
    )
    return {"inline_keyboard": rows}


def build_main_menu() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "⚙️ Настройки категорий", "callback_data": "settings"}],
        ]
    }


# ------------------------------------------------------------------
#  Telegram Bot API (httpx)
# ------------------------------------------------------------------

def call_tg_sync(client: httpx.Client, method: str, **payload) -> dict:
    resp = client.post(API.format(token=BOT_TOKEN, method=method), json=payload or None)
    data = resp.json()
    if not data.get("ok"):
        desc = data.get("description", "?")
        raise RuntimeError(f"{method} failed: {desc}")
    return data["result"]


class TelegramBot:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=60, proxy=PROXY_URL)

    def call(self, method: str, **payload) -> dict:
        return call_tg_sync(self.client, method, **payload)

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.call("sendMessage", **payload)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self.call("answerCallbackQuery", **payload)

    def edit_keyboard(self, message: dict, reply_markup: dict) -> None:
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]
        try:
            self.call(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except RuntimeError as exc:
            if "message is not modified" in str(exc):
                return
            raise

    def poll_updates(self, offset: int) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=50)  # long polling

    def close(self) -> None:
        self.client.close()


# ------------------------------------------------------------------
#  Обработка сообщений и кнопок
# ------------------------------------------------------------------

def allowed(user_id: int) -> bool:
    return OWNER_ID is None or user_id == OWNER_ID


def handle_update(bot: TelegramBot, update: dict) -> None:
    if "callback_query" in update:
        cb = update["callback_query"]
        user_id = cb["from"]["id"]
        if not allowed(user_id):
            bot.answer_callback(cb["id"], "Нет доступа")
            return
        data = cb.get("data", "")
        if data == "settings":
            bot.edit_keyboard(cb["message"], build_settings_kb(user_id))
        elif data.startswith("toggle:"):
            key = data.split(":", 1)[1]
            if key not in CATEGORIES:
                bot.answer_callback(cb["id"], "Неизвестная категория")
                return
            current = get_settings(user_id)
            set_category(user_id, key, not current[key])
            bot.edit_keyboard(cb["message"], build_settings_kb(user_id))
            state = "включена ✅" if not current[key] else "выключена ◻️"
            bot.answer_callback(cb["id"], f"Категория {state}")
        elif data.startswith("all:"):
            enabled = data.split(":", 1)[1] == "1"
            set_all(user_id, enabled)
            bot.edit_keyboard(cb["message"], build_settings_kb(user_id))
            bot.answer_callback(cb["id"], "Включено всё ✅" if enabled else "Выключено всё ◻️")
        else:
            bot.answer_callback(cb["id"])
        return

    if "message" not in update:
        return
    msg = update["message"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")
    if not text.startswith("/"):
        return

    if not allowed(user_id):
        bot.send_message(user_id, "Бот приватный. Доступ только для владельца.")
        return
    ensure_user(user_id)

    if text in ("/start", "/help"):
        enabled = [k for k, on in get_settings(user_id).items() if on]
        bot.send_message(
            user_id,
            "👋 Привет! Я ищу для вас вакансии по фрилансу в открытых чатах Telegram.\n\n"
            "Как только появляется заказ по вашей тематике — сразу пришлю сюда.\n\n"
            f"Сейчас активны категории:\n{categorizer.format_categories(enabled) or 'нет'}\n\n"
            "⚙️ Категории настраиваются кнопкой ниже или командой /settings",
            reply_markup=build_main_menu(),
        )
    elif text == "/settings":
        bot.send_message(
            user_id,
            "⚙️ Выберите категории, по которым присылать вакансии:",
            reply_markup=build_settings_kb(user_id),
        )
    elif text == "/stats":
        enabled = [k for k, on in get_settings(user_id).items() if on]
        bot.send_message(
            user_id,
            "📊 Статус подписки:\n\n"
            f"Включено категорий: {len(enabled)} из {len(CATEGORIES)}\n"
            "➕ Добавить/убрать: /settings",
        )


# ------------------------------------------------------------------
#  Рассылка вакансий из очереди
# ------------------------------------------------------------------

def format_job(row: tuple) -> str:
    _id, created_at, cats_raw, chat_title, chat_link, text = row
    cats = [c for c in cats_raw.split(",") if c in CATEGORIES]
    cat_names = categorizer.format_categories(cats) or "другое"
    lines = [
        "🔥 НОВАЯ ВАКАНСИЯ",
        "",
        f"🗂 Категория: {cat_names}",
        f"💬 Чат: {chat_title or '—'}",
    ]
    if chat_link:
        lines.append(f"🔗 Открыть сообщение: {chat_link}")
    lines += ["", "--------------------------", text]
    return "\n".join(lines)


def all_users() -> dict[int, dict[str, bool]]:
    """Все пользователи бота с их настройками."""
    conn = sqlite3.connect("data.db", timeout=30)
    try:
        ids = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM settings")]
    finally:
        conn.close()
    return {u: get_settings(u) for u in ids}


def queue_worker(bot: TelegramBot) -> None:
    """Цикл рассылки: забирает вакансии из очереди и доставляет подписчикам.
    Пометка «разослано» ставится только после успешной доставки."""
    while True:
        try:
            rows = fetch_pending()
            if rows:
                users = all_users()
                for row in rows:
                    job_id, _created, cats_raw = row[0], row[1], row[2]
                    job_cats = set(filter(None, cats_raw.split(",")))
                    matched = [
                        uid for uid, settings in users.items()
                        if any(settings.get(c, False) for c in job_cats)
                    ]
                    if not matched:
                        continue  # никому не релевантно — оставляем лежать в очереди
                    delivered = True
                    for user_id in matched:
                        try:
                            bot.send_message(user_id, format_job(row))
                        except Exception as exc:  # noqa: BLE001
                            delivered = False
                            log.warning("Не доставлено %s: %s", user_id, exc)
                    if delivered:
                        db.mark_sent(job_id)
                    # иначе: остаётся в очереди, попробуем снова в следующем круге
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в queue_worker")
        time.sleep(QUEUE_POLL_INTERVAL)


# ------------------------------------------------------------------
#  Точка входа
# ------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Ошибка: заполните BOT_TOKEN в .env (токен от @BotFather).")

    bot = TelegramBot()

    # Старт с повторами: VPN/прокси может «подвисать» в момент запуска
    me = None
    for attempt in range(1, 6):
        try:
            me = bot.call("getMe")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Попытка %d/5: не удалось связаться с Telegram (%s). "
                "Проверьте Throne включён (или PROXY_URL), повтор через 10 сек…",
                attempt, str(exc)[:120],
            )
            time.sleep(10)
    if me is None:
        raise SystemExit(
            "Telegram недоступен через 5 попыток. Включите Throne и запустите "
            "check.bat — когда проверка пройдёт, запустите бота снова."
        )
    log.info("Бот запущен: @%s. Жду вакансии из очереди…", me["username"])

    offset = 0
    # Обновления обрабатываем в фоне, чтобы рассылка не блокировала команды
    import threading

    worker = threading.Thread(target=queue_worker, args=(bot,), daemon=True)
    worker.start()

    try:
        while True:
            try:
                updates = bot.poll_updates(offset)
            except Exception as exc:  # noqa: BLE001
                # Сетевой сбой (например, выключили Throne) — пережидаем и не падаем
                log.warning("Связь с Telegram потеряна (%s). Повтор через 15 сек…", str(exc)[:120])
                time.sleep(15)
                continue
            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                try:
                    handle_update(bot, update)
                except Exception:  # noqa: BLE001
                    log.exception("Ошибка обработки update")
    finally:
        bot.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Бот остановлен.")

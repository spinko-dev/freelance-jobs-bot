"""SQLite-хранилище: дедупликация, очередь вакансий, настройки пользователей."""
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

DB_PATH = "data.db"

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    categories  TEXT NOT NULL,       -- ключи категорий через запятую
    chat_title  TEXT,
    chat_link   TEXT,                -- прямая ссылка на сообщение, если можно
    text        TEXT NOT NULL,
    sent        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_queue_sent ON queue(sent, id);

CREATE TABLE IF NOT EXISTS settings (
    user_id   INTEGER NOT NULL,
    category  TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, category)
);
"""


def get_conn() -> sqlite3.Connection:
    """Отдельное соединение на поток (aiogram и telethon работают в разных задачах)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


# ---------- Дедупликация ----------

def is_seen(chat_id: int, message_id: int) -> bool:
    cur = get_conn().execute(
        "SELECT 1 FROM seen WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    )
    return cur.fetchone() is not None


def has_history(chat_id: int) -> bool:
    """True, если по этому чату уже есть срез сообщений (базовая линия снята)."""
    cur = get_conn().execute("SELECT 1 FROM seen WHERE chat_id = ? LIMIT 1", (chat_id,))
    return cur.fetchone() is not None


def unseen_count(chat_id: int) -> int:
    """Сколько сообщений чата уже в seen (для логов)."""
    cur = get_conn().execute("SELECT COUNT(*) FROM seen WHERE chat_id = ?", (chat_id,))
    return cur.fetchone()[0]


def mark_seen(chat_id: int, message_id: int) -> bool:
    """True, если сообщение видели впервые."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT OR IGNORE INTO seen(chat_id, message_id, ts) VALUES (?, ?, ?)",
        (chat_id, message_id, int(time.time())),
    )
    conn.commit()
    return cur.rowcount == 1


def cleanup_seen(keep_days: int = 7) -> None:
    """Защита от бесконечного роста таблицы seen."""
    cutoff = int(time.time()) - keep_days * 86400
    get_conn().execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
    get_conn().commit()


# ---------- Очередь ----------

def push_job(categories: list[str], chat_title: str | None, chat_link: str | None, text: str) -> None:
    get_conn().execute(
        "INSERT INTO queue(created_at, categories, chat_title, chat_link, text) VALUES (?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            ",".join(categories),
            chat_title,
            chat_link,
            text,
        ),
    )
    get_conn().commit()


def fetch_pending(limit: int = 20) -> list[tuple]:
    """Берёт неотправленные вакансии (NOT помечая их) — бот разошлёт и подтвердит."""
    conn = get_conn()
    cur = conn.execute(
        "SELECT id, created_at, categories, chat_title, chat_link, text "
        "FROM queue WHERE sent = 0 ORDER BY id LIMIT ?",
        (limit,),
    )
    return cur.fetchall()


def mark_sent(job_id: int) -> None:
    """Вызывается ботом только после успешной доставки пользователю."""
    get_conn().execute("UPDATE queue SET sent = 1 WHERE id = ?", (job_id,))
    get_conn().commit()


def cleanup_queue(keep_days: int = 14) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    get_conn().execute(
        "DELETE FROM queue WHERE created_at < ?", (cutoff,)
    )
    get_conn().commit()


# ---------- Настройки пользователя ----------

# Импорт внизу, чтобы избежать циклической зависимости
from config import CATEGORIES  # noqa: E402

CATEGORIES_KEYS = list(CATEGORIES.keys())


def ensure_user(user_id: int) -> None:
    """Создаёт настройки по умолчанию (все категории включены)."""
    conn = get_conn()
    for key in CATEGORIES_KEYS:
        conn.execute(
            "INSERT OR IGNORE INTO settings(user_id, category, enabled) VALUES (?, ?, 1)",
            (user_id, key),
        )
    conn.commit()


def get_settings(user_id: int) -> dict[str, bool]:
    cur = get_conn().execute(
        "SELECT category, enabled FROM settings WHERE user_id = ?", (user_id,)
    )
    defaults = {k: True for k in CATEGORIES_KEYS}
    for key, enabled in cur.fetchall():
        defaults[key] = bool(enabled)
    return defaults


def set_category(user_id: int, key: str, enabled: bool) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO settings(user_id, category, enabled) VALUES (?, ?, ?)",
        (user_id, key, int(enabled)),
    )
    get_conn().commit()


def set_all(user_id: int, enabled: bool) -> None:
    get_conn().execute("UPDATE settings SET enabled = ? WHERE user_id = ?", (int(enabled), user_id))
    get_conn().commit()

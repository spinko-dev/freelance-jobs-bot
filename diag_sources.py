# Диагностика ленты: (1) queue за 5 дней по источникам, (2) живость каналов сейчас
# Запуск на сервере:  cd /opt/freelance_jobs_bot && venv/bin/python diag_sources.py
import time
from datetime import datetime, timedelta, timezone

import httpx

from config import PROXY_URL, SOURCE_CHATS
from db import get_conn
from webparser import HEADERS, fetch_posts

print("=" * 60)
print("А. Что дала очередь (queue) за последние 5 дней (по источникам):")
conn = get_conn()
cutoff = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
rows = conn.execute(
    "SELECT chat_title, COUNT(*), MAX(created_at) FROM queue "
    "WHERE created_at >= ? GROUP BY chat_title ORDER BY 2 DESC",
    (cutoff,),
).fetchall()
if not rows:
    print("  (пусто — ни одной вакансии в очередь не попало)")
for title, cnt, last in rows:
    print(f"  {cnt:4d}  {title}   (последняя: {str(last)[:19]})")
print(f"  Итого: {sum(r[1] for r in rows)}")

print()
print("=" * 60)
print("Б. Живость каналов прямо сейчас:")
sources = [s.strip().lstrip("@") for s in SOURCE_CHATS if isinstance(s, str)]
with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True, proxy=PROXY_URL) as client:
    for name in sources:
        try:
            posts = fetch_posts(client, name)
        except Exception as e:
            print(f"  X @{name} — ошибка: {e}")
            continue
        if not posts:
            print(f"  X @{name} — постов не получено (мертв/непубличный?)")
            continue
        newest_seq, newest_text = posts[-1]
        print(f"  ok @{name} — {len(posts)} постов, свежий пост #{newest_seq}")
        print(f"        топ-пост: {newest_text[:80].replace(chr(10), ' / ')}")
        time.sleep(2)

print()
print("=" * 60)
print("Пояснение: если в разделе Б каналы возвращают посты, а в разделе А")
print("их 0 — значит посты есть, но фильтр их не пропускает; в этом случае")
print("проверьте, какие категории включены в боте (/start) и насколько активны")
print("эти каналы вообще.")

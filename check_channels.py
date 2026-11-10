# Финальная проверка каналов из .env через webparser (без записи в базу)
# Показывает: доступность канала, число последних постов, пример текста.
import time

import httpx

from config import PROXY_URL, SOURCE_CHATS
from webparser import HEADERS, fetch_posts

sources = [s.strip().lstrip("@") for s in SOURCE_CHATS if isinstance(s, str)]
print(f"Прокси: {PROXY_URL}\nКаналов в списке: {len(sources)}\n" + "=" * 55)

voted = 0
with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True, proxy=PROXY_URL) as client:
    for name in sources:
        posts = fetch_posts(client, name)
        if posts is None:
            print(f"\u274c @{name} — недоступен (HTTP-ошибка/не публичный)")
        elif not posts:
            print(f"\u26a0\ufe0f @{name} — доступен, но постов не найдено (пустой?)")
        else:
            seq, text = posts[-1]
            sample = text[:120].replace("\n", " / ")
            print(f"\u2714\ufe0f @{name} — OK, последних постов: {len(posts)}")
            print(f"   последний пост #{seq}: {sample}")
            voted += 1
        time.sleep(2)

print("=" * 55)
print(f"Готово: рабочих каналов {voted} из {len(sources)}")

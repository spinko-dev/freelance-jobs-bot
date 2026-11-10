# Симуляция: какие из СЕЙЧАС опубликованных постов фильтр посчитал бы вакансиями
# (только просмотр, очередь/базу не трогает)

import time

import httpx

from categorizer import filter_vacancy, format_categories
from config import PROXY_URL, SOURCE_CHATS
from webparser import HEADERS, fetch_posts

sources = [s.strip().lstrip("@") for s in SOURCE_CHATS if isinstance(s, str)]
caught = 0
with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True, proxy=PROXY_URL) as client:
    for name in sources:
        posts = fetch_posts(client, name)
        if not posts:
            continue
        for seq, text in posts:
            cats = filter_vacancy(text)
            if not cats:
                continue
            caught += 1
            head = text[:100].replace("\n", " / ")
            print(f"\n@{name} #{seq} → {format_categories(cats)}\n   {head}")
        time.sleep(2)

print("\n" + "=" * 55)
print(f"Из последних постов всех каналов фильтр бы пропустил: {caught} шт.")

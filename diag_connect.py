# Диагностика connect.tg: два пути (напрямую и через прокси), по 1 запросу каждый
import httpx

from config import PROXY_URL
from webparser import HEADERS

URL = "https://connect.tg/orders/development/chatbot-developer"

clients = [
    ("НАПРЯМУЮ (без прокси)", dict(timeout=15, follow_redirects=True)),
    ("ЧЕРЕЗ ПРОКСИ (Throne)", dict(timeout=15, follow_redirects=True, proxy=PROXY_URL)),
]

for label, kwargs in clients:
    try:
        with httpx.Client(headers=HEADERS, **kwargs) as c:
            r = c.get(URL)
        print(f"{label}: HTTP {r.status_code} | размер {len(r.text)}")
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: ОШИБКА: {str(exc)[:140]}")

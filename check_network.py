"""Диагностика сети: доступны ли Telegram-сервисы с текущей машины.

Запуск:  venv\\Scripts\\python check_network.py   (или check.bat)
Пример:  Throne включён  -> всё ОК, можно запускать парсер и бота.
         Throne выключен -> скажет, что без VPN работать не будет.
"""
import sys

import httpx

from config import PROXY_URL

CHECKS = [
    ("Веб-версия каналов (для webparser.py)", "https://t.me/s/telegram"),
    ("Bot API (для bot.py)",                  "https://api.telegram.org"),
]

OK = "\u2714\ufe0f"
FAIL = "\u274c"


def main() -> None:
    print(f"Прокси из .env: {PROXY_URL or 'не задан (идём напрямую)'}")
    print("-" * 50)
    with httpx.Client(timeout=15, follow_redirects=True, proxy=PROXY_URL) as client:
        all_ok = True
        for label, url in CHECKS:
            try:
                resp = client.get(url)
                status = "OK" if resp.status_code < 500 else f"HTTP {resp.status_code}"
                ok = resp.status_code < 500
            except Exception as exc:  # noqa: BLE001
                status = str(exc).split(":")[-1].strip() or "нет связи"
                ok = False
            print(f"{OK if ok else FAIL} {label} — {status}")
            all_ok = all_ok and ok

    print("-" * 50)
    if all_ok:
        print("Всё предоставляет → можно запускать webparser.bat и start_bot.bat.")
    else:
        print("Telegram недоступен напрямую. Включите Ваш режим VPN (Throne в режиме TUN)")
        print("или заполните PROXY_URL в .env, затем запустите проверку снова.")


if __name__ == "__main__":
    sys.exit(main())

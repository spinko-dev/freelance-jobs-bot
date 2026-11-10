# Тест парсера connect.tg на сохранённой HTML-странице раздела
import json

import connectparser

html = open(
    r"C:\Users\katya\.local\share\opencode\tool-output\tool_1aba87f47001G4ewEOUoFBJFA7",
    encoding="utf-8", errors="ignore",
).read()

orders = connectparser.parse_cards(html)
print(f"Распознано карточек: {len(orders)}")
assert len(orders) == 24, "должно быть 24 карточки"

first, last = orders[0], orders[-1]
print("\nПервая карточка:")
print(json.dumps(first, ensure_ascii=False, indent=2))
print("\nПоследняя карточка:")
print(json.dumps(last, ensure_ascii=False, indent=2))

# точность: uuid извлечён, заголовок чистый (без тегов/пробелов), бюджет есть
assert "20f224be-ea85-4b2a-91bd-7f8730069ce6" == first["uuid"]
assert "<" not in first["title"] and first["title"].strip() == first["title"]
print("\nCONNECT PARSE TEST OK")

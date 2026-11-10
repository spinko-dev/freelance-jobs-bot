# Тест HTML-парсера веб-версии t.me/s (без сети, на мок-данных)
from webparser import parse_posts

html = (
    '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
    'data-post="testjob/101" id="100">'
    '<div class="tgme_widget_message_text js-message_text" dir="auto">'
    'Требуется видеомонтажер, бюджет 5000 руб, включая цветокоррекцию</div>'
    '<div class="tgme_widget_message_footer"></div></div></div>'
    '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
    'data-post="testjob/102" id="101">'
    '<div class="tgme_widget_message_text js-message_text" dir="auto">'
    'Ищу переводчика <b>с английского</b> на русский<br/>оплата $50</div></div></div>'
    '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
    'data-post="testjob/103" id="102">'
    '<div>только фото, без текста</div></div></div>'
    '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
    'data-post="testjob/101" id="100">'  # закреп — дубликат 101
    '<div class="tgme_widget_message_text js-message_text" dir="auto">'
    'Требуется видеомонтажер, бюджет 5000 руб, включая цветокоррекцию</div>'
    '</div></div>'
)

posts = parse_posts(html)
for seq, text in posts:
    print(seq, "->", text.replace("\n", " | "))

assert [s for s, _ in posts] == [101, 102], "сортировка/дедуп сломаны"
assert "видеомонтажер" in posts[0][1] and "цветокоррекцию" in posts[0][1], "теги не сняты"
assert "английского" in posts[1][1] and "\n" in posts[1][1], "br не обработан"
assert len(posts) == 2, "дубль закрепа не убран или мусор попал"
print("PARSE TEST OK")

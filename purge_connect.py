# Аварийная очистка: удаляет из очереди все неотправленные заказы Connect
import db

conn = db.get_conn()
count = conn.execute(
    "SELECT COUNT(*) FROM queue WHERE sent=0 AND chat_title LIKE 'Connect%'"
).fetchone()[0]
conn.execute("DELETE FROM queue WHERE sent=0 AND chat_title LIKE 'Connect%'")
conn.commit()
print(f"Из очереди удалено неотправленных заказов Connect: {count}")
rest = conn.execute("SELECT COUNT(*) FROM queue WHERE sent=0").fetchone()[0]
print(f"Осталось неразосланных всего: {rest}")

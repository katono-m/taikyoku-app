import sqlite3
con = sqlite3.connect(r"database/app.db")
cur = con.cursor()

print("=== SETTING ===")
print("NULL club_id:", cur.execute("SELECT COUNT(*) FROM setting WHERE club_id IS NULL").fetchone()[0])
print("重複(should be 0):", cur.execute("""
  SELECT COUNT(*) FROM (
    SELECT club_id, key, COUNT(*) c
    FROM setting
    GROUP BY club_id, key
    HAVING c > 1
  )
""").fetchone()[0])

print("\n=== STRENGTH ===")
print("NULL club_id:", cur.execute("SELECT COUNT(*) FROM strength WHERE club_id IS NULL").fetchone()[0])
print("重複 name(should be 0):", cur.execute("""
  SELECT COUNT(*) FROM (
    SELECT club_id, name, COUNT(*) c
    FROM strength
    GROUP BY club_id, name
    HAVING c > 1
  )
""").fetchone()[0])
print("重複 order(should be 0):", cur.execute("""
  SELECT COUNT(*) FROM (
    SELECT club_id, "order", COUNT(*) c
    FROM strength
    GROUP BY club_id, "order"
    HAVING c > 1
  )
""").fetchone()[0])

con.close()

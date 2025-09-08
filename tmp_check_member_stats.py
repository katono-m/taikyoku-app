import sqlite3
con = sqlite3.connect(r"database/app.db")
cur = con.cursor()
print("member_code NULL 件数:", cur.execute("SELECT COUNT(*) FROM member WHERE member_code IS NULL").fetchone()[0])
print("club_id NULL 件数:", cur.execute("SELECT COUNT(*) FROM member WHERE club_id IS NULL").fetchone()[0])
print("重複件数(should be 0):", cur.execute("SELECT COUNT(*) FROM (SELECT club_id, member_code, COUNT(*) c FROM member WHERE member_code IS NOT NULL GROUP BY club_id, member_code HAVING c>1)").fetchone()[0])
con.close()

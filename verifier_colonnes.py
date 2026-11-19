import sqlite3
import os

base = os.path.join(os.environ["LOCALAPPDATA"], "LogicielMagasin", "magasin.db")
conn = sqlite3.connect(base)

for table in ("customers", "payment_plans"):
    print("\nTable :", table)
    for row in conn.execute(f"PRAGMA table_info({table})"):
        print("-", row[1])

conn.close()

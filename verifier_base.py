import sqlite3
import os

base = os.path.join(os.environ["LOCALAPPDATA"], "LogicielMagasin", "magasin.db")
conn = sqlite3.connect(base)

print("Integrite :", conn.execute("PRAGMA integrity_check").fetchone()[0])
print("Version :", conn.execute("PRAGMA user_version").fetchone()[0])
print("Tables :")

for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print("-", row[0])

conn.close()

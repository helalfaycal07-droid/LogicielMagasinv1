"""Numéros lisibles : une série par type de document (VTE-000001, REC-000001...)."""

from .db import transaction


def next_number(conn, code: str, width: int = 6) -> str:
    with transaction(conn):
        row = conn.execute("SELECT last_number FROM document_sequences WHERE code = ?", (code,)).fetchone()
        number = 1 if row is None else row["last_number"] + 1
        conn.execute(
            "INSERT INTO document_sequences (code, last_number) VALUES (?, ?) "
            "ON CONFLICT(code) DO UPDATE SET last_number = excluded.last_number",
            (code, number),
        )
    return f"{code}-{number:0{width}d}"

"""Caisse : sessions, mouvements d'espèces, comptage et écart.

Règles :
- une vente en espèces exige une session ouverte ;
- le solde d'ouverture est proposé à partir de la clôture précédente ;
- tout écart de caisse exige une raison écrite ;
- la caisse ne devient pas négative.
"""

from .clock import now_iso
from .db import log_audit, transaction
from .errors import DifferenceNeedsReason, NoOpenSession, SessionAlreadyOpen, ValidationError
from .users import require_permission

DEFAULT_REGISTER = 1
_NOT_COUNTED = ("ouverture", "fermeture")


def current_session(conn, register_id: int = DEFAULT_REGISTER):
    return conn.execute(
        "SELECT * FROM cash_sessions WHERE register_id = ? AND status = 'open'", (register_id,)
    ).fetchone()


def require_open_session(conn, register_id: int = DEFAULT_REGISTER):
    session = current_session(conn, register_id)
    if session is None:
        raise NoOpenSession("Aucune session de caisse ouverte : ouvrez la caisse avant de vendre en espèces.")
    return session


def suggested_opening(conn, register_id: int = DEFAULT_REGISTER) -> int:
    """Solde compté à la dernière clôture (0 si la caisse n'a jamais été fermée)."""
    row = conn.execute(
        "SELECT counted_centimes FROM cash_sessions WHERE register_id = ? AND status = 'closed' "
        "ORDER BY id DESC LIMIT 1", (register_id,)
    ).fetchone()
    return row["counted_centimes"] if row else 0


def expected_balance(conn, session_id: int) -> int:
    """Ouverture + recettes - dépenses. L'ouverture et la fermeture ne comptent pas comme recettes."""
    opening = conn.execute(
        "SELECT opening_centimes FROM cash_sessions WHERE id = ?", (session_id,)
    ).fetchone()["opening_centimes"]
    total = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) AS s FROM cash_movements "
        "WHERE session_id = ? AND kind NOT IN ('ouverture', 'fermeture')", (session_id,)
    ).fetchone()["s"]
    return opening + total


def open_session(conn, register_id: int = DEFAULT_REGISTER, opening_centimes: int | None = None,
                 at: str | None = None, actor_user_id: int | None = None) -> int:
    require_permission(conn, actor_user_id, "cash_open")
    at = at or now_iso()
    with transaction(conn):
        if current_session(conn, register_id):
            raise SessionAlreadyOpen("Une session est déjà ouverte sur cette caisse.")
        if opening_centimes is None:
            opening_centimes = suggested_opening(conn, register_id)
        if opening_centimes < 0:
            raise ValidationError("Le solde d'ouverture ne peut pas être négatif.")
        cursor = conn.execute(
            "INSERT INTO cash_sessions (register_id, opened_at, opening_centimes) VALUES (?,?,?)",
            (register_id, at, opening_centimes),
        )
        session_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO cash_movements (session_id, kind, amount_centimes, balance_before, "
            "balance_after, description, created_at) VALUES (?, 'ouverture', 0, ?, ?, 'Ouverture de caisse', ?)",
            (session_id, opening_centimes, opening_centimes, at),
        )
        log_audit(conn, "caisse_ouverte", "cash_sessions", session_id,
                  str(opening_centimes), at, actor_user_id)
    return session_id


def add_cash_movement(conn, session_id: int, kind: str, amount_centimes: int, *,
                      ref_type: str | None = None, ref_id: int | None = None,
                      ref_number: str | None = None, description: str | None = None,
                      at: str | None = None) -> int:
    """Ajoute une ligne à la piste d'audit de la caisse (solde avant / après)."""
    with transaction(conn):
        session = conn.execute("SELECT * FROM cash_sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None or session["status"] != "open":
            raise NoOpenSession("Cette session de caisse n'est pas ouverte.")
        before = expected_balance(conn, session_id)
        after = before + amount_centimes
        if after < 0:
            raise ValidationError("Cette opération rendrait la caisse négative : elle est refusée.")
        cursor = conn.execute(
            "INSERT INTO cash_movements (session_id, kind, amount_centimes, balance_before, balance_after, "
            "ref_type, ref_id, ref_number, description, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (session_id, kind, amount_centimes, before, after, ref_type, ref_id, ref_number,
             description, at or now_iso()),
        )
        return cursor.lastrowid


def record_expense(conn, session_id: int, amount_centimes: int, description: str,
                   at: str | None = None, actor_user_id: int | None = None) -> int:
    require_permission(conn, actor_user_id, "cash_expense")
    if amount_centimes <= 0 or not description.strip():
        raise ValidationError("Une dépense a besoin d'un montant positif et d'une description.")
    movement_id = add_cash_movement(
        conn, session_id, "depense", -amount_centimes,
        description=description, at=at,
    )
    log_audit(
        conn, "depense_caisse", "cash_movements", movement_id,
        f"{amount_centimes} — {description.strip()}", at, actor_user_id,
    )
    return movement_id


def session_summary(conn, session_id: int) -> dict:
    session = conn.execute("SELECT * FROM cash_sessions WHERE id = ?", (session_id,)).fetchone()
    by_kind = {
        r["kind"]: r["total"]
        for r in conn.execute(
            "SELECT kind, SUM(amount_centimes) AS total FROM cash_movements "
            "WHERE session_id = ? AND kind NOT IN ('ouverture','fermeture') GROUP BY kind", (session_id,)
        )
    }
    return {"opening": session["opening_centimes"], "by_kind": by_kind,
            "expected": expected_balance(conn, session_id), "status": session["status"]}


def close_session(conn, session_id: int, counted_centimes: int, reason: str | None = None,
                  at: str | None = None, actor_user_id: int | None = None) -> dict:
    """Ferme la session. Un écart (manque ou excédent) exige une raison écrite."""
    require_permission(conn, actor_user_id, "cash_close")
    at = at or now_iso()
    with transaction(conn):
        session = conn.execute("SELECT * FROM cash_sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None or session["status"] != "open":
            raise NoOpenSession("Cette session de caisse n'est pas ouverte.")
        expected = expected_balance(conn, session_id)
        difference = counted_centimes - expected
        if difference != 0 and not (reason and reason.strip()):
            raise DifferenceNeedsReason(
                "Écart de caisse : une raison écrite est obligatoire avant de fermer."
            )
        conn.execute(
            "UPDATE cash_sessions SET status = 'closed', closed_at = ?, counted_centimes = ?, "
            "expected_centimes = ?, difference_centimes = ?, difference_reason = ? WHERE id = ?",
            (at, counted_centimes, expected, difference, (reason or "").strip() or None, session_id),
        )
        conn.execute(
            "INSERT INTO cash_movements (session_id, kind, amount_centimes, balance_before, balance_after, "
            "description, created_at) VALUES (?, 'fermeture', 0, ?, ?, ?, ?)",
            (session_id, expected, counted_centimes,
             f"Fermeture : attendu {expected}, compté {counted_centimes}, écart {difference}", at),
        )
        log_audit(conn, "caisse_fermee", "cash_sessions", session_id,
                  f"ecart={difference}", at, actor_user_id)
    return {"expected": expected, "counted": counted_centimes, "difference": difference}

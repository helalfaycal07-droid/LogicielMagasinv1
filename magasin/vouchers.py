"""Avoirs clients émis par un retour et utilisés comme moyen de paiement."""

from .clock import now_iso
from .db import log_audit
from .errors import ValidationError


def voucher_balance(conn, customer_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) AS balance "
        "FROM customer_credit_movements WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    return row["balance"]


def record_voucher_issue(conn, customer_id: int, amount_centimes: int,
                         return_id: int, at: str) -> None:
    if amount_centimes <= 0:
        raise ValidationError("Le montant de l'avoir doit être positif.")
    conn.execute(
        "INSERT INTO customer_credit_movements "
        "(customer_id, amount_centimes, return_id, created_at) VALUES (?,?,?,?)",
        (customer_id, amount_centimes, return_id, at),
    )
    log_audit(conn, "avoir_emis", "sale_returns", return_id, str(amount_centimes), at)


def record_voucher_spend(conn, customer_id: int, amount_centimes: int,
                         sale_id: int, at: str) -> None:
    if amount_centimes <= 0 or amount_centimes > voucher_balance(conn, customer_id):
        raise ValidationError("Le montant de l'avoir dépasse le solde disponible du client.")
    conn.execute(
        "INSERT INTO customer_credit_movements "
        "(customer_id, amount_centimes, sale_id, created_at) VALUES (?,?,?,?)",
        (customer_id, -amount_centimes, sale_id, at),
    )
    log_audit(conn, "avoir_utilise", "sales", sale_id, str(amount_centimes), at)

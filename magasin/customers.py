"""Clients : fiche rapide, statut, dette calculée (jamais saisie)."""

import re

from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .sequences import next_number

STATUSES = ("actif", "en_retard", "contentieux", "bloque", "irrecouvrable", "archive")
CREDIT_BLOCKING = ("bloque", "contentieux", "irrecouvrable", "archive")

_MOBILE = re.compile(r"^0[5-7]\d{8}$")      # 10 chiffres : 05xx, 06xx, 07xx
_LANDLINE = re.compile(r"^0[2-4]\d{7}$")    # 9 chiffres : 02x, 03x, 04x


def normalize_phone(phone: str | None) -> str | None:
    """Contrôle un numéro algérien et le renvoie sous la forme 0XXXXXXXXX."""
    if phone is None or not phone.strip():
        return None
    digits = re.sub(r"[\s.\-()]", "", phone)
    if digits.startswith("+213"):
        digits = "0" + digits[4:]
    elif digits.startswith("00213"):
        digits = "0" + digits[5:]
    if not (_MOBILE.match(digits) or _LANDLINE.match(digits)):
        raise ValidationError(
            f"Numéro de téléphone invalide : {phone!r} (un mobile algérien a 10 chiffres, "
            "par exemple 0551234567)."
        )
    return digits


def create_customer(conn, name: str, phone: str | None = None, *, phone2: str | None = None,
                    address: str | None = None, credit_allowed: bool = False,
                    credit_limit_centimes: int | None = None, at: str | None = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Le client a besoin d'un nom.")
    phone = normalize_phone(phone)
    phone2 = normalize_phone(phone2)
    at = at or now_iso()
    with transaction(conn):
        code = next_number(conn, "CLI", 5)
        dossier = next_number(conn, "DOS", 5)
        cursor = conn.execute(
            "INSERT INTO customers (code, dossier_number, name, phone, phone2, address, "
            "credit_allowed, credit_limit_centimes, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (code, dossier, name, phone, phone2, address, int(credit_allowed),
             credit_limit_centimes, at),
        )
        customer_id = cursor.lastrowid
        log_audit(conn, "client_cree", "customers", customer_id, f"{code} / {dossier}", at)
    return customer_id


def get_customer(conn, customer_id: int):
    return conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()


def set_status(conn, customer_id: int, status: str, reason: str, at: str | None = None) -> None:
    """Chaque changement de statut est journalisé (qui, quand, pourquoi)."""
    if status not in STATUSES:
        raise ValidationError(f"Statut inconnu : {status}")
    if not reason or not reason.strip():
        raise ValidationError("Une raison est obligatoire pour changer le statut d'un client.")
    with transaction(conn):
        old = get_customer(conn, customer_id)["status"]
        conn.execute("UPDATE customers SET status = ? WHERE id = ?", (status, customer_id))
        log_audit(conn, "client_statut", "customers", customer_id, f"{old} -> {status} : {reason}", at)


def add_opening_balance(conn, customer_id: int, amount_centimes: int, reason: str,
                        at: str | None = None) -> None:
    """Reprise d'une dette ancienne : ligne datée « solde d'ouverture », jamais un champ libre."""
    if amount_centimes == 0 or not reason.strip():
        raise ValidationError("Un montant non nul et une raison sont obligatoires.")
    conn.execute(
        "INSERT INTO customer_adjustments (customer_id, amount_centimes, reason, created_at) VALUES (?,?,?,?)",
        (customer_id, amount_centimes, reason, at or now_iso()),
    )


def customer_debt(conn, customer_id: int) -> int:
    """Dette = ventes - paiements à la vente + reprises - versements. Toujours calculée."""
    row = conn.execute(
        """
        SELECT
          COALESCE((SELECT SUM(total_centimes) FROM sales
                    WHERE customer_id = :c AND status = 'validee'), 0)
        - COALESCE((SELECT SUM(sp.amount_centimes) FROM sale_payments sp
                    JOIN sales s ON s.id = sp.sale_id
                    WHERE s.customer_id = :c AND s.status = 'validee'), 0)
        + COALESCE((SELECT SUM(amount_centimes) FROM customer_adjustments WHERE customer_id = :c), 0)
        - COALESCE((SELECT SUM(amount_centimes) FROM customer_payments WHERE customer_id = :c), 0)
        AS debt
        """,
        {"c": customer_id},
    ).fetchone()
    return row["debt"]


def receivables(conn) -> list[dict]:
    """« Qui me doit de l'argent ? » : calculé localement, sans Internet."""
    result = []
    for c in conn.execute("SELECT * FROM customers WHERE status <> 'archive' ORDER BY name"):
        debt = customer_debt(conn, c["id"])
        if debt > 0:
            result.append({"customer_id": c["id"], "name": c["name"], "status": c["status"],
                           "debt_centimes": debt})
    return sorted(result, key=lambda r: -r["debt_centimes"])

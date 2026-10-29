"""Crédit : échéancier, versements, chèques, retards.

- La dette d'un client est calculée (voir customers.customer_debt), jamais saisie.
- Un versement s'impute d'abord sur les échéances les plus anciennes.
- Un chèque est une garantie de paiement : il ne réduit la dette que lorsqu'il est encaissé.
"""

import calendar
from datetime import date

from .cash import add_cash_movement, require_open_session
from .clock import now_iso, today as today_algeria
from .customers import customer_debt, get_customer
from .db import log_audit, transaction
from .errors import InvalidTransition, OverPayment, ValidationError
from .money import format_da, split_evenly
from .sales import PAYMENT_METHODS
from .sequences import next_number

CHEQUE_FLOW = {
    "en_dossier": {"remis_banque", "a_restituer"},
    "remis_banque": {"encaisse", "impaye"},
    "impaye": {"remis_banque", "a_restituer"},
    "a_restituer": {"restitue"},
    "encaisse": set(),
    "restitue": set(),
}


def add_months(d: date, months: int) -> date:
    """Ajoute des mois en gardant le jour (ramené à la fin du mois si besoin)."""
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ----------------------------------------------------------------- échéancier
def create_plan(conn, sale_id: int, installment_count: int, first_due_date: date,
                at: str | None = None) -> int:
    """Crée l'échéancier du reste dû d'une vente à crédit (la dernière mensualité absorbe l'arrondi)."""
    with transaction(conn):
        sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if sale is None or sale["customer_id"] is None:
            raise ValidationError("Un échéancier exige une vente liée à un client identifié.")
        if conn.execute("SELECT 1 FROM payment_plans WHERE sale_id = ?", (sale_id,)).fetchone():
            raise ValidationError("Cette vente a déjà un échéancier.")
        paid = conn.execute("SELECT COALESCE(SUM(amount_centimes),0) AS s FROM sale_payments WHERE sale_id = ?",
                            (sale_id,)).fetchone()["s"]
        principal = sale["total_centimes"] - paid
        if principal <= 0:
            raise ValidationError("Cette vente n'a pas de reste dû : pas d'échéancier à créer.")
        amounts = split_evenly(principal, installment_count)
        cursor = conn.execute(
            "INSERT INTO payment_plans (sale_id, customer_id, principal_centimes, installment_count, "
            "first_due_date, created_at) VALUES (?,?,?,?,?,?)",
            (sale_id, sale["customer_id"], principal, installment_count,
             first_due_date.isoformat(), at or now_iso()),
        )
        plan_id = cursor.lastrowid
        for index, amount in enumerate(amounts):
            conn.execute(
                "INSERT INTO installments (plan_id, number, due_date, amount_centimes) VALUES (?,?,?,?)",
                (plan_id, index + 1, add_months(first_due_date, index).isoformat(), amount),
            )
        log_audit(conn, "echeancier_cree", "payment_plans", plan_id,
                  f"{installment_count} x ~{format_da(amounts[0])}", at)
    return plan_id


def installment_status(row, today: date) -> str:
    """payee, en_retard, partielle ou a_venir."""
    if row["paid_centimes"] >= row["amount_centimes"]:
        return "payee"
    if row["due_date"] < today.isoformat():
        return "en_retard"
    if row["paid_centimes"] > 0:
        return "partielle"
    return "a_venir"


def list_installments(conn, plan_id: int | None = None, customer_id: int | None = None,
                      today: date | None = None) -> list[dict]:
    today = today or today_algeria()
    sql = ("SELECT i.*, p.sale_id, p.customer_id FROM installments i "
           "JOIN payment_plans p ON p.id = i.plan_id WHERE 1=1")
    args: list = []
    if plan_id is not None:
        sql += " AND i.plan_id = ?"
        args.append(plan_id)
    if customer_id is not None:
        sql += " AND p.customer_id = ?"
        args.append(customer_id)
    rows = conn.execute(sql + " ORDER BY i.due_date, i.plan_id, i.number", args).fetchall()
    return [{**dict(r), "reste_centimes": r["amount_centimes"] - r["paid_centimes"],
             "status": installment_status(r, today)} for r in rows]


# ------------------------------------------------------------------ versements
def _sales_with_balance(conn, customer_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.id,
               s.total_centimes
               - COALESCE((SELECT SUM(amount_centimes) FROM sale_payments WHERE sale_id = s.id), 0)
               - COALESCE((SELECT SUM(amount_centimes) FROM payment_allocations WHERE sale_id = s.id), 0)
               AS remaining
        FROM sales s WHERE s.customer_id = ? AND s.status = 'validee' ORDER BY s.id
        """, (customer_id,)).fetchall()
    return [dict(r) for r in rows if r["remaining"] > 0]


def _release_cheques(conn, installment_id: int, at: str) -> None:
    """Échéance soldée par un autre moyen : les chèques en attente sont à restituer au client."""
    conn.execute(
        "UPDATE cheques SET status = 'a_restituer' WHERE installment_id = ? "
        "AND status IN ('en_dossier', 'impaye')", (installment_id,))


def record_payment(conn, customer_id: int, amount_centimes: int, method: str = "especes", *,
                   installment_id: int | None = None, register_id: int = 1,
                   note: str | None = None, at: str | None = None, today: date | None = None) -> dict:
    """Versement d'un client : réduit la dette, s'impute sur les échéances les plus anciennes."""
    if amount_centimes <= 0:
        raise ValidationError("Le versement doit être supérieur à 0.")
    if method not in PAYMENT_METHODS:
        raise ValidationError(f"Mode de paiement inconnu : {method}")
    at = at or now_iso()
    with transaction(conn):
        if get_customer(conn, customer_id) is None:
            raise ValidationError("Client introuvable.")
        debt = customer_debt(conn, customer_id)
        if amount_centimes > debt:
            raise OverPayment(
                f"Le versement ({format_da(amount_centimes)}) dépasse la dette du client ({format_da(debt)}).")
        session = require_open_session(conn, register_id) if method == "especes" else None
        number = next_number(conn, "REC")
        cursor = conn.execute(
            "INSERT INTO customer_payments (number, customer_id, method, amount_centimes, session_id, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (number, customer_id, method, amount_centimes, session["id"] if session else None, note, at),
        )
        payment_id = cursor.lastrowid

        sales = _sales_with_balance(conn, customer_id)
        if installment_id is not None:
            target = conn.execute(
                "SELECT i.id, p.sale_id, p.customer_id FROM installments i JOIN payment_plans p ON p.id = i.plan_id "
                "WHERE i.id = ?", (installment_id,)).fetchone()
            if target is None or target["customer_id"] != customer_id:
                raise ValidationError("Cette échéance n'appartient pas à ce client.")
            sales.sort(key=lambda s: 0 if s["id"] == target["sale_id"] else 1)

        remaining = amount_centimes
        for sale in sales:
            if remaining == 0:
                break
            take_sale = min(remaining, sale["remaining"])
            left = take_sale
            insts = conn.execute(
                "SELECT i.* FROM installments i JOIN payment_plans p ON p.id = i.plan_id "
                "WHERE p.sale_id = ? AND i.paid_centimes < i.amount_centimes ORDER BY i.due_date, i.number",
                (sale["id"],)).fetchall()
            if installment_id is not None:
                insts.sort(key=lambda r: 0 if r["id"] == installment_id else 1)
            for inst in insts:
                if left == 0:
                    break
                part = min(left, inst["amount_centimes"] - inst["paid_centimes"])
                conn.execute("UPDATE installments SET paid_centimes = paid_centimes + ? WHERE id = ?",
                             (part, inst["id"]))
                conn.execute(
                    "INSERT INTO payment_allocations (payment_id, sale_id, installment_id, amount_centimes) "
                    "VALUES (?,?,?,?)", (payment_id, sale["id"], inst["id"], part))
                if part == inst["amount_centimes"] - inst["paid_centimes"]:
                    _release_cheques(conn, inst["id"], at)
                left -= part
            if left > 0:
                conn.execute(
                    "INSERT INTO payment_allocations (payment_id, sale_id, installment_id, amount_centimes) "
                    "VALUES (?,?,NULL,?)", (payment_id, sale["id"], left))
            remaining -= take_sale
        # S'il reste un montant, il règle une dette reprise (solde d'ouverture) : pas d'allocation.

        if method == "especes":
            add_cash_movement(conn, session["id"], "paiement_client", amount_centimes,
                              ref_type="CustomerPayment", ref_id=payment_id, ref_number=number,
                              description=f"Versement {number}", at=at)
        refresh_customer_status(conn, customer_id, today)
        log_audit(conn, "versement", "customer_payments", payment_id, f"{number} {amount_centimes}", at)
    return {"payment_id": payment_id, "number": number, "debt_after": customer_debt(conn, customer_id)}


# ---------------------------------------------------------------------- retards
def refresh_customer_status(conn, customer_id: int, today: date | None = None) -> str:
    """« En retard » est calculé à partir des échéances dépassées, pas saisi à la main."""
    today = today or today_algeria()
    overdue = any(i["status"] == "en_retard" for i in list_installments(conn, customer_id=customer_id, today=today))
    customer = get_customer(conn, customer_id)
    status = customer["status"]
    if overdue and status == "actif":
        status = "en_retard"
    elif not overdue and status == "en_retard":
        status = "actif"
    if status != customer["status"]:
        conn.execute("UPDATE customers SET status = ? WHERE id = ?", (status, customer_id))
        log_audit(conn, "client_statut_auto", "customers", customer_id,
                  f"{customer['status']} -> {status} (calcul automatique des retards)")
    return status


# ----------------------------------------------------------------------- chèques
def add_cheque(conn, customer_id: int, number: str, bank: str | None, amount_centimes: int,
               due_date: date, installment_id: int | None = None, note: str | None = None,
               at: str | None = None) -> int:
    if amount_centimes <= 0 or not number.strip():
        raise ValidationError("Un chèque a besoin d'un numéro et d'un montant positif.")
    cursor = conn.execute(
        "INSERT INTO cheques (customer_id, installment_id, number, bank, amount_centimes, due_date, note, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (customer_id, installment_id, number.strip(), bank, amount_centimes, due_date.isoformat(), note,
         at or now_iso()),
    )
    return cursor.lastrowid


def add_cheques_for_plan(conn, plan_id: int, first_number: int, bank: str | None = None,
                         at: str | None = None) -> list[int]:
    """Un chèque par mensualité, avec des numéros qui se suivent (modifiables ensuite)."""
    plan = conn.execute("SELECT * FROM payment_plans WHERE id = ?", (plan_id,)).fetchone()
    ids = []
    for inst in conn.execute("SELECT * FROM installments WHERE plan_id = ? ORDER BY number", (plan_id,)):
        ids.append(add_cheque(conn, plan["customer_id"], str(first_number + inst["number"] - 1), bank,
                              inst["amount_centimes"], date.fromisoformat(inst["due_date"]),
                              installment_id=inst["id"], at=at))
    return ids


def set_cheque_status(conn, cheque_id: int, new_status: str, *, register_id: int = 1,
                      today: date | None = None, at: str | None = None) -> None:
    """en_dossier -> remis_banque -> encaisse ou impaye ... ; l'encaissement crée le versement."""
    at = at or now_iso()
    with transaction(conn):
        cheque = conn.execute("SELECT * FROM cheques WHERE id = ?", (cheque_id,)).fetchone()
        if cheque is None:
            raise ValidationError("Chèque introuvable.")
        if new_status not in CHEQUE_FLOW.get(cheque["status"], set()):
            raise InvalidTransition(f"Un chèque « {cheque['status']} » ne peut pas passer à « {new_status} ».")
        if new_status == "encaisse":
            record_payment(conn, cheque["customer_id"], cheque["amount_centimes"], "cheque",
                           installment_id=cheque["installment_id"], register_id=register_id,
                           note=f"Chèque n°{cheque['number']}", at=at, today=today)
        conn.execute("UPDATE cheques SET status = ? WHERE id = ?", (new_status, cheque_id))
        log_audit(conn, "cheque_statut", "cheques", cheque_id, f"{cheque['status']} -> {new_status}", at)
        if new_status == "impaye":
            refresh_customer_status(conn, cheque["customer_id"], today)

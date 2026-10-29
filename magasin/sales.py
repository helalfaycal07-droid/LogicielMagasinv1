"""Ventes : une vente est une seule transaction (vente, lignes, stock, caisse, paiements)."""

from dataclasses import dataclass, field

from .cash import add_cash_movement, current_session, require_open_session
from .clock import now_iso
from .customers import CREDIT_BLOCKING, customer_debt, get_customer
from .db import log_audit, transaction
from .errors import (CreditNotAllowed, InsufficientStock, PaymentInsufficient, ValidationError)
from .money import format_da
from . import serials
from .sequences import next_number
from .settings import get_setting
from .stock import add_movement, stock_of

PAYMENT_METHODS = ("especes", "carte", "ccp", "virement", "cheque", "avoir")


@dataclass
class Line:
    product_id: int
    quantity: int = 1
    unit_price_centimes: int | None = None  # None = prix de la fiche produit
    imeis: list[str] | None = None          # obligatoire pour un produit suivi par IMEI (un IMEI par appareil)


@dataclass
class PaymentIn:
    method: str
    amount_centimes: int


@dataclass
class SaleResult:
    sale_id: int
    number: str
    total_centimes: int
    paid_centimes: int
    due_centimes: int
    tendered_centimes: int
    change_centimes: int
    warnings: list[str] = field(default_factory=list)


def create_sale(conn, lines: list[Line], payments: list[PaymentIn], *,
                customer_id: int | None = None, discount_centimes: int = 0,
                register_id: int = 1, at: str | None = None) -> SaleResult:
    if not lines:
        raise ValidationError("Le panier est vide.")
    if discount_centimes < 0:
        raise ValidationError("La remise ne peut pas être négative.")
    at = at or now_iso()
    warnings: list[str] = []

    with transaction(conn):
        # 1. Lignes et total
        items = []
        sold_serial_ids: list[int] = []
        subtotal = 0
        for line in lines:
            if line.quantity <= 0:
                raise ValidationError("La quantité doit être supérieure à 0.")
            product = conn.execute("SELECT * FROM products WHERE id = ?", (line.product_id,)).fetchone()
            if product is None or product["archived"]:
                raise ValidationError("Produit introuvable ou archivé.")
            unit = product["price_centimes"] if line.unit_price_centimes is None else line.unit_price_centimes
            if product["track_serial"]:
                sold_serial_ids.extend(serials.resolve_for_sale(conn, product["id"], line.imeis or [], line.quantity))
            elif line.imeis:
                raise ValidationError(f"« {product['name']} » n'est pas suivi par IMEI.")
            items.append((product, line.quantity, unit, unit * line.quantity))
            subtotal += unit * line.quantity
        total = subtotal - discount_centimes
        if total < 0:
            raise ValidationError("La remise dépasse le total.")

        # 2. Paiements : le rendu de monnaie ne peut venir que des espèces
        for p in payments:
            if p.method not in PAYMENT_METHODS:
                raise ValidationError(f"Mode de paiement inconnu : {p.method}")
            if p.amount_centimes <= 0:
                raise ValidationError("Un paiement doit avoir un montant positif.")
        tendered = sum(p.amount_centimes for p in payments)
        change = max(0, tendered - total)
        cash_total = sum(p.amount_centimes for p in payments if p.method == "especes")
        if change > cash_total:
            raise ValidationError("Le rendu de monnaie ne peut venir que d'un paiement en espèces.")
        applied = [[p.method, p.amount_centimes] for p in payments]
        remaining = change
        for entry in reversed(applied):
            if entry[0] == "especes" and remaining:
                cut = min(remaining, entry[1])
                entry[1] -= cut
                remaining -= cut
        applied = [(m, a) for m, a in applied if a > 0]
        paid = tendered - change
        due = total - paid

        # 3. Crédit : seulement pour un client identifié et autorisé
        if due > 0:
            if customer_id is None:
                raise PaymentInsufficient(f"Paiement insuffisant : il manque {format_da(due)}.")
            customer = get_customer(conn, customer_id)
            if customer is None:
                raise ValidationError("Client introuvable.")
            if not customer["credit_allowed"] or customer["status"] in CREDIT_BLOCKING:
                raise CreditNotAllowed("Le crédit n'est pas autorisé pour ce client.")
            limit = customer["credit_limit_centimes"]
            if limit is not None and customer_debt(conn, customer_id) + due > limit:
                raise CreditNotAllowed(f"Plafond de crédit dépassé (limite : {format_da(limit)}).")

        # 4. Session de caisse obligatoire pour les espèces
        cash_applied = sum(a for m, a in applied if m == "especes")
        session = require_open_session(conn, register_id) if cash_applied else current_session(conn, register_id)
        session_id = session["id"] if session else None

        # 5. Stock insuffisant : avertir, bloquer ou autoriser selon le réglage
        mode = get_setting(conn, "sale_when_insufficient", "warn")
        wanted: dict[int, int] = {}
        for product, qty, _unit, _tot in items:
            if product["track_serial"]:
                continue  # chaque appareil est vérifié un par un par son IMEI
            wanted[product["id"]] = wanted.get(product["id"], 0) + qty
        for product_id, qty in wanted.items():
            available = stock_of(conn, product_id)
            if available < qty:
                name = next(p["name"] for p, *_ in items if p["id"] == product_id)
                message = f"Stock insuffisant pour « {name} » : {available} disponible(s), {qty} demandé(s)."
                if mode == "block":
                    raise InsufficientStock(message)
                if mode == "warn":
                    warnings.append(message)

        # 6. Écriture : vente, lignes, paiements, stock, caisse
        number = next_number(conn, "VTE")
        cursor = conn.execute(
            "INSERT INTO sales (number, customer_id, status, total_centimes, tendered_centimes, "
            "change_centimes, session_id, created_at) VALUES (?,?, 'validee', ?,?,?,?,?)",
            (number, customer_id, total, tendered, change, session_id, at),
        )
        sale_id = cursor.lastrowid
        for product, qty, unit, line_total in items:
            conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price_centimes, total_centimes) "
                "VALUES (?,?,?,?,?)", (sale_id, product["id"], qty, unit, line_total),
            )
            add_movement(conn, product["id"], -qty, "vente", ref_type="Sale", ref_id=sale_id,
                         ref_number=number, at=at)
        serials.mark_sold(conn, sold_serial_ids, sale_id, at)
        for method, amount in applied:
            conn.execute("INSERT INTO sale_payments (sale_id, method, amount_centimes) VALUES (?,?,?)",
                         (sale_id, method, amount))
        if cash_applied:
            kind = "acompte" if due > 0 else "vente_especes"
            add_cash_movement(conn, session_id, kind, cash_applied, ref_type="Sale", ref_id=sale_id,
                              ref_number=number, description=f"Vente {number}", at=at)
        log_audit(conn, "vente_validee", "sales", sale_id, f"{number} total={total} du={due}", at)

    return SaleResult(sale_id, number, total, paid, due, tendered, change, warnings)


def sale_summary(conn, sale_id: int) -> dict:
    """Quatre montants toujours affichés : total, payé à la vente, payé depuis, reste dû."""
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    paid_at_sale = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) AS s FROM sale_payments WHERE sale_id = ?", (sale_id,)
    ).fetchone()["s"]
    paid_since = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) AS s FROM payment_allocations WHERE sale_id = ?", (sale_id,)
    ).fetchone()["s"]
    return {
        "number": sale["number"],
        "total": sale["total_centimes"],
        "paye_a_la_vente": paid_at_sale,
        "paye_depuis": paid_since,
        "reste_du": sale["total_centimes"] - paid_at_sale - paid_since,
        "montant_recu": sale["tendered_centimes"],
        "rendu": sale["change_centimes"],
    }

"""Devis enregistrés, valables 30 jours et convertibles en vente."""

from dataclasses import dataclass
from datetime import date, timedelta

from .clock import now_iso, today
from .db import log_audit, transaction
from .errors import ValidationError
from .sequences import next_number
from .users import require_permission

QUOTE_VALIDITY_DAYS = 30


@dataclass
class QuoteLine:
    product_id: int
    quantity: int
    unit_price_centimes: int | None = None


def create_quote(conn, lines: list[QuoteLine], *, recipient_name: str,
                 recipient_phone: str | None = None,
                 recipient_address: str | None = None,
                 customer_id: int | None = None, discount_centimes: int = 0,
                 at: str | None = None, actor_user_id: int | None = None) -> dict:
    """Enregistre un devis sans mouvement de stock, vente ni encaissement."""
    require_permission(conn, actor_user_id, "sell")
    recipient_name = (recipient_name or "").strip()
    recipient_phone = (recipient_phone or "").strip() or None
    recipient_address = (recipient_address or "").strip() or None
    if not recipient_name or len(recipient_name) > 160:
        raise ValidationError("Le destinataire du devis est obligatoire (160 caractères maximum).")
    if recipient_phone and len(recipient_phone) > 40:
        raise ValidationError("Le téléphone du destinataire ne peut pas dépasser 40 caractères.")
    if recipient_address and len(recipient_address) > 250:
        raise ValidationError("L'adresse du destinataire ne peut pas dépasser 250 caractères.")
    if not lines:
        raise ValidationError("Le devis ne contient aucun produit.")
    if discount_centimes < 0:
        raise ValidationError("La remise ne peut pas être négative.")
    if discount_centimes:
        require_permission(conn, actor_user_id, "approve_discount")
    at = at or now_iso()
    try:
        created_day = date.fromisoformat(at[:10])
    except (TypeError, ValueError) as exc:
        raise ValidationError("La date du devis est invalide.") from exc
    valid_until = (created_day + timedelta(days=QUOTE_VALIDITY_DAYS)).isoformat()

    with transaction(conn):
        if customer_id is not None:
            customer = conn.execute(
                "SELECT name, status FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            if customer is None or customer["status"] == "archive":
                raise ValidationError("Le client du devis est introuvable ou archivé.")
            if customer["name"] != recipient_name:
                raise ValidationError("Le destinataire du devis ne correspond pas au client sélectionné.")

        products = []
        subtotal = 0
        for line in lines:
            if line.quantity <= 0:
                raise ValidationError("La quantité du devis doit être supérieure à zéro.")
            product = conn.execute(
                "SELECT id, reference, name, price_centimes, tax_rate_bp, archived "
                "FROM products WHERE id = ?", (line.product_id,)
            ).fetchone()
            if product is None or product["archived"]:
                raise ValidationError("Un produit du devis est introuvable ou archivé.")
            unit_price = (
                product["price_centimes"]
                if line.unit_price_centimes is None else line.unit_price_centimes
            )
            if unit_price < 0:
                raise ValidationError("Le prix du devis ne peut pas être négatif.")
            if unit_price != product["price_centimes"]:
                require_permission(conn, actor_user_id, "approve_discount")
            products.append((product, line.quantity, unit_price))
            subtotal += line.quantity * unit_price
        total = subtotal - discount_centimes
        if total < 0:
            raise ValidationError("La remise dépasse le total du devis.")

        number = next_number(conn, "DEV")
        cursor = conn.execute(
            "INSERT INTO quotes "
            "(number, customer_id, recipient_name, recipient_phone, recipient_address, "
            "discount_centimes, total_centimes, created_at, valid_until) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                number, customer_id, recipient_name, recipient_phone, recipient_address,
                discount_centimes, total, at, valid_until,
            ),
        )
        quote_id = cursor.lastrowid
        for product, quantity, unit_price in products:
            conn.execute(
                "INSERT INTO quote_items "
                "(quote_id, product_id, reference, name, quantity, unit_price_centimes, "
                "tax_rate_bp, total_centimes) VALUES (?,?,?,?,?,?,?,?)",
                (
                    quote_id, product["id"], product["reference"], product["name"],
                    quantity, unit_price, product["tax_rate_bp"], quantity * unit_price,
                ),
            )
        log_audit(
            conn, "devis_cree", "quotes", quote_id,
            f"{number} total={total} valable_jusquau={valid_until}",
            at, actor_user_id,
        )
    return {
        "id": quote_id,
        "number": number,
        "customer_id": customer_id,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "recipient_address": recipient_address,
        "discount_centimes": discount_centimes,
        "total_centimes": total,
        "created_at": at,
        "valid_until": valid_until,
    }


def list_quotes(conn, *, open_only: bool = False) -> list[dict]:
    """Retourne les devis avec leur état d'expiration calculé à la date d'Algérie."""
    where = "WHERE q.status = 'open'" if open_only else ""
    rows = conn.execute(
        f"""
        SELECT q.id, q.number, q.customer_id, q.recipient_name, q.discount_centimes,
               q.total_centimes, q.status, q.created_at, q.valid_until,
               q.converted_sale_id, c.name AS customer_name
        FROM quotes q
        LEFT JOIN customers c ON c.id = q.customer_id
        {where}
        ORDER BY q.id DESC
        """
    ).fetchall()
    current_day = today().isoformat()
    return [
        {**dict(row), "expired": row["valid_until"] < current_day}
        for row in rows
    ]


def get_quote(conn, quote_id: int) -> dict:
    """Retourne l'en-tête et les lignes historiques d'un devis."""
    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ?", (quote_id,)
    ).fetchone()
    if quote is None:
        raise ValidationError("Devis introuvable.")
    result = dict(quote)
    result["lines"] = [
        dict(row) for row in conn.execute(
            "SELECT product_id, reference, name, quantity, unit_price_centimes, "
            "tax_rate_bp, total_centimes "
            "FROM quote_items WHERE quote_id = ? ORDER BY id",
            (quote_id,),
        )
    ]
    return result


def cancel_quote(conn, quote_id: int, *, at: str | None = None,
                 actor_user_id: int | None = None) -> None:
    """Annule un devis ouvert sans effacer ses détails ni ses références."""
    require_permission(conn, actor_user_id, "sell")
    at = at or now_iso()
    with transaction(conn):
        quote = conn.execute(
            "SELECT number, status FROM quotes WHERE id = ?", (quote_id,)
        ).fetchone()
        if quote is None:
            raise ValidationError("Devis introuvable.")
        if quote["status"] != "open":
            raise ValidationError("Seul un devis ouvert peut être annulé.")
        conn.execute(
            "UPDATE quotes SET status = 'cancelled' WHERE id = ?", (quote_id,)
        )
        log_audit(
            conn, "devis_annule", "quotes", quote_id, quote["number"], at, actor_user_id
        )


def validate_quote_for_sale(conn, quote_id: int, lines: list, *,
                            discount_centimes: int, customer_id: int | None,
                            at: str) -> dict:
    """Vérifie que la vente reprend exactement les lignes et conditions du devis."""
    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ?", (quote_id,)
    ).fetchone()
    if quote is None:
        raise ValidationError("Devis introuvable.")
    if quote["status"] != "open":
        raise ValidationError("Ce devis n'est plus ouvert.")
    if quote["valid_until"] < at[:10]:
        raise ValidationError("Ce devis a expiré ; créez un nouveau devis aux prix actuels.")
    if discount_centimes != quote["discount_centimes"]:
        raise ValidationError("La remise de la vente doit correspondre au devis.")
    if quote["customer_id"] is not None and customer_id not in (None, quote["customer_id"]):
        raise ValidationError("Le client sélectionné ne correspond pas au devis.")
    if quote["customer_id"] is not None:
        customer = conn.execute(
            "SELECT status FROM customers WHERE id = ?", (quote["customer_id"],)
        ).fetchone()
        if customer is None or customer["status"] == "archive":
            raise ValidationError("Le client de ce devis est archivé ou introuvable.")

    expected = [
        (row["product_id"], row["quantity"], row["unit_price_centimes"])
        for row in conn.execute(
            "SELECT product_id, quantity, unit_price_centimes FROM quote_items "
            "WHERE quote_id = ? ORDER BY id",
            (quote_id,),
        )
    ]
    actual = [
        (
            product["id"],
            quantity,
            unit_price,
        )
        for product, quantity, unit_price, _line_total in lines
    ]
    if actual != expected:
        raise ValidationError("Les articles et quantités de la vente doivent correspondre au devis.")
    return dict(quote)


def mark_quote_converted(conn, quote_id: int, sale_id: int, *,
                         at: str, actor_user_id: int | None = None) -> None:
    """Marque le devis converti dans la même transaction que la vente."""
    cursor = conn.execute(
        "UPDATE quotes SET status = 'converted', converted_sale_id = ? "
        "WHERE id = ? AND status = 'open'",
        (sale_id, quote_id),
    )
    if cursor.rowcount != 1:
        raise ValidationError("Le devis a déjà été utilisé ou annulé.")
    log_audit(
        conn, "devis_converti", "quotes", quote_id, f"vente_id={sale_id}", at, actor_user_id
    )

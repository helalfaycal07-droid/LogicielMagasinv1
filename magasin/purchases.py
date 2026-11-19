"""Réceptions fournisseur : mouvements de stock tracés par réception."""

from dataclasses import dataclass
import re
import sqlite3

from .clock import now_iso
from .cash import add_cash_movement, current_session
from .db import log_audit, transaction
from .errors import ValidationError
from .sequences import next_number
from .serials import add_units
from .stock import add_movement, stock_of
from .users import require_permission

PAYMENT_METHODS = {"especes", "virement", "cheque"}


@dataclass
class ReceiptLine:
    product_id: int
    quantity: int
    imeis: list[tuple[str, str | None]] | None = None
    unit_cost_centimes: int | None = None
    unit_price_centimes: int | None = None


def receive_stock(conn, lines: list[ReceiptLine], supplier: str, *,
                  supplier_reference: str | None = None, payment_centimes: int = 0,
                  payment_method: str = "especes", at: str | None = None,
                  actor_user_id: int | None = None) -> str:
    """Enregistre toute une réception atomiquement et retourne son numéro REC.

    Le versement règle d'abord le bon. S'il dépasse le total du bon, l'excédent est
    déduit de la dette existante du fournisseur (ancien solde ou bons plus anciens).
    """
    require_permission(conn, actor_user_id, "purchases")
    supplier = (supplier or "").strip()
    supplier_reference = (supplier_reference or "").strip() or None
    if not supplier:
        raise ValidationError("Saisissez le nom du fournisseur.")
    if not lines:
        raise ValidationError("La réception ne contient aucun produit.")
    if any(line.quantity <= 0 for line in lines):
        raise ValidationError("La quantité reçue doit être supérieure à zéro.")
    if payment_centimes < 0:
        raise ValidationError("Le règlement ne peut pas être négatif.")
    if len({line.product_id for line in lines}) != len(lines):
        raise ValidationError("Un produit ne peut apparaître qu'une fois dans une réception.")

    at = at or now_iso()
    with transaction(conn):
        supplier_row = conn.execute(
            "SELECT id, name, active FROM suppliers WHERE name = ? COLLATE NOCASE",
            (supplier,),
        ).fetchone()
        if supplier_row is None:
            cursor = conn.execute(
                "INSERT INTO suppliers (name, created_at, updated_at) VALUES (?,?,?)",
                (supplier, at, at),
            )
            supplier_id = cursor.lastrowid
        else:
            if not supplier_row["active"]:
                raise ValidationError("Ce fournisseur est archivé et ne peut pas être utilisé.")
            supplier_id = supplier_row["id"]
            supplier = supplier_row["name"]

        products = {}
        unit_costs = {}
        unit_prices = {}
        for line in lines:
            product = conn.execute(
                "SELECT id, name, reference, cost_centimes, price_centimes, track_serial, archived "
                "FROM products WHERE id = ?",
                (line.product_id,),
            ).fetchone()
            if product is None or product["archived"]:
                raise ValidationError("Un produit de la réception est introuvable ou archivé.")
            unit_cost = (
                product["cost_centimes"]
                if line.unit_cost_centimes is None else line.unit_cost_centimes
            )
            if unit_cost < 0:
                raise ValidationError("Le coût d'achat ne peut pas être négatif.")
            unit_price = (
                product["price_centimes"]
                if line.unit_price_centimes is None else line.unit_price_centimes
            )
            if unit_price < 0:
                raise ValidationError("Le prix de vente ne peut pas être négatif.")
            unit_costs[line.product_id] = unit_cost
            unit_prices[line.product_id] = unit_price
            if line.product_id not in products:
                products[line.product_id] = product
            if product["track_serial"]:
                if line.imeis is None or len(line.imeis) != line.quantity:
                    raise ValidationError(
                        f"« {product['name']} » est suivi par IMEI : fournissez un IMEI par appareil."
                    )
            elif line.imeis:
                raise ValidationError(
                    f"« {product['name']} » n'est pas suivi par IMEI."
                )

        total_centimes = sum(
            line.quantity * unit_costs[line.product_id] for line in lines
        )
        # Le versement règle d'abord ce bon ; le reste vient en déduction de l'ancienne dette.
        receipt_payment = min(payment_centimes, total_centimes)
        extra_payment = payment_centimes - receipt_payment
        if extra_payment > max(_supplier_due(conn, supplier_id), 0):
            raise ValidationError(
                "Le montant versé dépasse le total du bon plus la dette actuelle du fournisseur."
            )

        number = next_number(conn, "REC")
        cursor = conn.execute(
            "INSERT INTO purchase_receipts "
            "(number, supplier, supplier_reference, total_centimes, created_at, supplier_id) "
            "VALUES (?,?,?,?,?,?)",
            (number, supplier, supplier_reference, total_centimes, at, supplier_id),
        )
        receipt_id = cursor.lastrowid
        for line in lines:
            product = products[line.product_id]
            conn.execute(
                "INSERT INTO purchase_receipt_items "
                "(receipt_id, product_id, quantity, unit_cost_centimes, unit_price_centimes) "
                "VALUES (?,?,?,?,?)",
                (receipt_id, line.product_id, line.quantity,
                 unit_costs[line.product_id], unit_prices[line.product_id]),
            )
            if (
                unit_costs[line.product_id] != product["cost_centimes"]
                or unit_prices[line.product_id] != product["price_centimes"]
            ):
                conn.execute(
                    "UPDATE products SET cost_centimes = ?, price_centimes = ? WHERE id = ?",
                    (unit_costs[line.product_id], unit_prices[line.product_id], line.product_id),
                )
                log_audit(
                    conn,
                    "prix_produit_actualise_reception",
                    "products",
                    line.product_id,
                    f"{product['reference']} — achat {unit_costs[line.product_id]} "
                    f"centimes — vente {unit_prices[line.product_id]} centimes",
                    at,
                    actor_user_id,
                )
            note = "Réception fournisseur"
            if supplier_reference:
                note += f" — pièce fournisseur {supplier_reference}"
            if product["track_serial"]:
                add_units(
                    conn,
                    line.product_id,
                    line.imeis or [],
                    movement_type="reception_achat",
                    ref_type="Receipt",
                    ref_id=receipt_id,
                    ref_number=number,
                    counterparty=supplier,
                    note=note,
                    at=at,
                    actor_user_id=actor_user_id,
                )
            else:
                add_movement(
                    conn,
                    line.product_id,
                    line.quantity,
                    "reception_achat",
                    ref_type="Receipt",
                    ref_id=receipt_id,
                    ref_number=number,
                    counterparty=supplier,
                    note=note,
                    at=at,
                )

        if receipt_payment:
            record_supplier_payment(
                conn, receipt_id, receipt_payment, payment_method, at=at,
                actor_user_id=actor_user_id,
            )
        if extra_payment:
            record_supplier_payment_for_supplier(
                conn, supplier_id, extra_payment, payment_method,
                note=f"Excédent versé avec la réception {number}", at=at,
                actor_user_id=actor_user_id,
            )

        details = f"{number} — {supplier} — {len(lines)} produit(s)"
        if supplier_reference:
            details += f" — pièce fournisseur {supplier_reference}"
        log_audit(conn, "reception_fournisseur", "stock_movements", None,
                  details, at, actor_user_id)
    return number


def get_purchase_receipt(conn, receipt_id: int) -> dict | None:
    """Retourne une réception et ses lignes pour consultation ou correction."""
    receipt = conn.execute(
        "SELECT * FROM purchase_receipts WHERE id = ?", (receipt_id,)
    ).fetchone()
    if receipt is None:
        return None
    result = dict(receipt)
    result["items"] = []
    for row in conn.execute(
        "SELECT i.*, p.reference, p.name, p.track_serial "
        "FROM purchase_receipt_items i JOIN products p ON p.id = i.product_id "
        "WHERE i.receipt_id = ? ORDER BY i.id",
        (receipt_id,),
    ):
        item = dict(row)
        item["imeis"] = []
        if item["track_serial"]:
            item["imeis"] = [
                (serial["imei1"], serial["imei2"])
                for serial in conn.execute(
                    "SELECT s.imei1, s.imei2 FROM product_serials s "
                    "JOIN stock_movements m ON m.id = s.entry_movement_id "
                    "WHERE m.ref_id = ? AND m.ref_type IN ('Receipt','ReceiptAdjustment') "
                    "AND s.product_id = ? ORDER BY s.id",
                    (receipt_id, item["product_id"]),
                )
            ]
        result["items"].append(item)
    return result


def _resolve_receipt_supplier(conn, supplier: str, at: str) -> tuple[int, str]:
    supplier_row = conn.execute(
        "SELECT id, name, active FROM suppliers WHERE name = ? COLLATE NOCASE",
        (supplier,),
    ).fetchone()
    if supplier_row is None:
        cursor = conn.execute(
            "INSERT INTO suppliers (name, created_at, updated_at) VALUES (?,?,?)",
            (supplier, at, at),
        )
        return cursor.lastrowid, supplier
    if not supplier_row["active"]:
        raise ValidationError("Ce fournisseur est archivé et ne peut pas être utilisé.")
    return supplier_row["id"], supplier_row["name"]


def _receipt_serials(conn, receipt_id: int, product_id: int, *, in_stock: bool = False):
    sql = (
        "SELECT s.id, s.status FROM product_serials s "
        "JOIN stock_movements m ON m.id = s.entry_movement_id "
        "WHERE m.ref_id = ? AND m.ref_type IN ('Receipt','ReceiptAdjustment') "
        "AND s.product_id = ?"
    )
    args: list = [receipt_id, product_id]
    if in_stock:
        sql += " AND s.status = 'en_stock'"
    return conn.execute(sql + " ORDER BY s.id DESC", args).fetchall()


def _adjust_receipt_stock(conn, receipt_id: int, product, quantity: int, *,
                          supplier: str, at: str, actor_user_id: int | None,
                          imeis: list[tuple[str, str | None]] | None = None) -> None:
    if quantity == 0:
        return
    if not product["track_serial"]:
        movement_quantity = quantity
        if quantity < 0:
            movement_quantity = -min(abs(quantity), max(stock_of(conn, product["id"]), 0))
        if movement_quantity == 0:
            return
        add_movement(
            conn, product["id"], movement_quantity, "ajustement",
            ref_type="ReceiptAdjustment", ref_id=receipt_id,
            ref_number=conn.execute(
                "SELECT number FROM purchase_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["number"],
            counterparty=supplier,
            note="Correction de réception fournisseur",
            at=at,
        )
        return
    if quantity > 0:
        if imeis is None or len(imeis) != quantity:
            raise ValidationError(
                f"Fournissez {quantity} nouvel(aux) IMEI pour « {product['name']} »."
            )
        add_units(
            conn, product["id"], imeis,
            movement_type="reception_achat",
            ref_type="ReceiptAdjustment",
            ref_id=receipt_id,
            ref_number=conn.execute(
                "SELECT number FROM purchase_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["number"],
            counterparty=supplier,
            note="Correction de réception fournisseur",
            at=at,
            actor_user_id=actor_user_id,
        )
        return

    units = _receipt_serials(conn, receipt_id, product["id"], in_stock=True)
    for unit in units[:abs(quantity)]:
        add_movement(
            conn, product["id"], -1, "ajustement",
            ref_type="ReceiptAdjustment", ref_id=receipt_id,
            ref_number=conn.execute(
                "SELECT number FROM purchase_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["number"],
            counterparty=supplier,
            note=f"Annulation/correction réception — appareil {unit['id']}",
            at=at,
        )
        conn.execute(
            "UPDATE product_serials SET status = 'sorti', note = ? WHERE id = ?",
            ("Sortie liée à une correction de réception", unit["id"]),
        )


def update_purchase_receipt(conn, receipt_id: int, lines: list[ReceiptLine],
                            supplier: str, *, supplier_reference: str | None = None,
                            at: str | None = None,
                            actor_user_id: int | None = None) -> None:
    """Corrige une réception existante avec des mouvements de stock complémentaires."""
    require_permission(conn, actor_user_id, "purchases")
    supplier = (supplier or "").strip()
    supplier_reference = (supplier_reference or "").strip() or None
    if not supplier:
        raise ValidationError("Saisissez le nom du fournisseur.")
    if not lines or any(line.quantity <= 0 for line in lines):
        raise ValidationError("La réception doit contenir des quantités positives.")
    if len({line.product_id for line in lines}) != len(lines):
        raise ValidationError("Un produit ne peut apparaître qu'une fois dans une réception.")
    at = at or now_iso()

    with transaction(conn):
        receipt = conn.execute(
            "SELECT * FROM purchase_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if receipt is None:
            raise ValidationError("Réception fournisseur introuvable.")
        if receipt["status"] != "validee":
            raise ValidationError("Une réception annulée ne peut pas être modifiée.")
        supplier_id, supplier = _resolve_receipt_supplier(conn, supplier, at)

        old_items = {
            row["product_id"]: dict(row)
            for row in conn.execute(
                "SELECT i.*, p.name, p.reference, p.track_serial "
                "FROM purchase_receipt_items i JOIN products p ON p.id = i.product_id "
                "WHERE i.receipt_id = ?",
                (receipt_id,),
            )
        }
        new_items = {}
        products = {}
        for line in lines:
            product = conn.execute(
                "SELECT id, name, reference, cost_centimes, price_centimes, track_serial, archived "
                "FROM products WHERE id = ?",
                (line.product_id,),
            ).fetchone()
            if product is None or product["archived"]:
                raise ValidationError("Un produit de la réception est introuvable ou archivé.")
            unit_cost = product["cost_centimes"] if line.unit_cost_centimes is None \
                else line.unit_cost_centimes
            unit_price = product["price_centimes"] if line.unit_price_centimes is None \
                else line.unit_price_centimes
            if unit_cost < 0 or unit_price < 0:
                raise ValidationError("Les prix ne peuvent pas être négatifs.")
            if product["track_serial"] and not line.imeis and line.product_id not in old_items:
                raise ValidationError(
                    f"Fournissez un IMEI par appareil pour « {product['name']} »."
                )
            products[line.product_id] = product
            new_items[line.product_id] = {
                "quantity": line.quantity,
                "unit_cost_centimes": unit_cost,
                "unit_price_centimes": unit_price,
                "imeis": line.imeis,
            }

        number = receipt["number"]
        for product_id in old_items.keys() | new_items.keys():
            old_quantity = old_items.get(product_id, {}).get("quantity", 0)
            item = new_items.get(product_id)
            new_quantity = item["quantity"] if item else 0
            product = products.get(product_id)
            if product is None:
                product = conn.execute(
                    "SELECT id, name, reference, cost_centimes, price_centimes, "
                    "track_serial, archived FROM products WHERE id = ?",
                    (product_id,),
                ).fetchone()
            _adjust_receipt_stock(
                conn, receipt_id, product, new_quantity - old_quantity,
                supplier=supplier, at=at, actor_user_id=actor_user_id,
                imeis=item["imeis"] if item else None,
            )
            previous = old_items.get(product_id)
            if item and (
                previous is None
                or item["unit_cost_centimes"] != previous["unit_cost_centimes"]
                or item["unit_price_centimes"] != previous["unit_price_centimes"]
            ):
                conn.execute(
                    "UPDATE products SET cost_centimes = ?, price_centimes = ? WHERE id = ?",
                    (item["unit_cost_centimes"], item["unit_price_centimes"], product_id),
                )
                log_audit(
                    conn,
                    "prix_produit_actualise_reception",
                    "products",
                    product_id,
                    f"{product['reference']} — achat {item['unit_cost_centimes']} "
                    f"centimes — vente {item['unit_price_centimes']} centimes",
                    at,
                    actor_user_id,
                )

        total_centimes = sum(
            item["quantity"] * item["unit_cost_centimes"] for item in new_items.values()
        )
        conn.execute(
            "UPDATE purchase_receipts SET supplier = ?, supplier_id = ?, "
            "supplier_reference = ?, total_centimes = ?, created_at = ? WHERE id = ?",
            (supplier, supplier_id, supplier_reference, total_centimes, at, receipt_id),
        )
        conn.execute("DELETE FROM purchase_receipt_items WHERE receipt_id = ?", (receipt_id,))
        for product_id, item in new_items.items():
            conn.execute(
                "INSERT INTO purchase_receipt_items "
                "(receipt_id, product_id, quantity, unit_cost_centimes, unit_price_centimes) "
                "VALUES (?,?,?,?,?)",
                (receipt_id, product_id, item["quantity"], item["unit_cost_centimes"],
                 item["unit_price_centimes"]),
            )
        conn.execute(
            "UPDATE stock_movements SET created_at = ? "
            "WHERE ref_type = 'Receipt' AND ref_id = ?",
            (at, receipt_id),
        )
        log_audit(
            conn, "reception_fournisseur_modifiee", "purchase_receipts", receipt_id,
            f"{number} — {supplier} — total {total_centimes} centimes",
            at, actor_user_id,
        )


def cancel_purchase_receipt(conn, receipt_id: int, *,
                            at: str | None = None,
                            actor_user_id: int | None = None) -> None:
    """Annule une réception sans supprimer son historique ni ses écritures de caisse."""
    require_permission(conn, actor_user_id, "purchases")
    at = at or now_iso()
    with transaction(conn):
        receipt = conn.execute(
            "SELECT * FROM purchase_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if receipt is None:
            raise ValidationError("Réception fournisseur introuvable.")
        if receipt["status"] == "annulee":
            raise ValidationError("Cette réception est déjà annulée.")
        items = conn.execute(
            "SELECT i.quantity, p.id, p.name, p.track_serial "
            "FROM purchase_receipt_items i JOIN products p ON p.id = i.product_id "
            "WHERE i.receipt_id = ?",
            (receipt_id,),
        ).fetchall()
        for item in items:
            _adjust_receipt_stock(
                conn, receipt_id, item, -item["quantity"],
                supplier=receipt["supplier"], at=at, actor_user_id=actor_user_id,
            )
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_centimes), 0) FROM supplier_payments "
            "WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()[0]
        if paid:
            conn.execute(
                "INSERT INTO supplier_adjustments "
                "(supplier_id, receipt_id, amount_centimes, reason, created_at) "
                "VALUES (?,?,?,?,?)",
                (receipt["supplier_id"], receipt_id, -paid,
                 "Crédit fournisseur créé par l'annulation de la réception", at),
            )
        conn.execute(
            "UPDATE purchase_receipts SET status = 'annulee' WHERE id = ?", (receipt_id,)
        )
        log_audit(
            conn, "reception_fournisseur_annulee", "purchase_receipts", receipt_id,
            f"{receipt['number']} — {receipt['supplier']}",
            at, actor_user_id,
        )


def supplier_receipt_balances(conn):
    """Liste les réceptions avec leur total, paiements cumulés et montant restant dû."""
    return conn.execute(
        """
        SELECT r.id, r.supplier_id, r.number, r.supplier, r.supplier_reference, r.total_centimes,
               r.created_at, r.status,
               COALESCE(SUM(p.amount_centimes), 0) AS paid_centimes,
               CASE WHEN r.status = 'annulee' THEN 0
                    ELSE r.total_centimes - COALESCE(SUM(p.amount_centimes), 0) END
                   AS due_centimes,
               GROUP_CONCAT(DISTINCT p.method) AS methods
        FROM purchase_receipts r
        LEFT JOIN supplier_payments p ON p.receipt_id = r.id
        GROUP BY r.id
        ORDER BY r.id DESC
        """
    ).fetchall()


# ----------------------------------------------------------------------
# Dette globale fournisseur : bons + ancien solde (report) - versements
# ----------------------------------------------------------------------
def supplier_opening_due(conn, supplier_id: int) -> int:
    """Ancien solde restant (report antérieur au logiciel moins les versements déjà faits dessus)."""
    row = conn.execute(
        "SELECT opening_balance_centimes FROM suppliers WHERE id = ?", (supplier_id,)
    ).fetchone()
    if row is None:
        return 0
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) FROM supplier_opening_payments "
        "WHERE supplier_id = ?",
        (supplier_id,),
    ).fetchone()[0]
    return row[0] - paid


def supplier_balances(conn) -> list[dict]:
    """Solde global, règlements et nombre de réceptions ouvertes par fournisseur.

    Dette globale = bons validés - versements + ajustements + ancien solde restant.
    """
    grouped = {}

    def new_row(supplier_id: int, name: str) -> dict:
        return {
            "supplier_id": supplier_id,
            "supplier": name,
            "receipt_count": 0,
            "total_centimes": 0,
            "paid_centimes": 0,
            "due_centimes": 0,
            "methods": set(),
        }

    for receipt in supplier_receipt_balances(conn):
        if receipt["status"] != "validee":
            continue
        supplier_id = receipt["supplier_id"]
        row = grouped.setdefault(supplier_id, new_row(supplier_id, receipt["supplier"]))
        row["receipt_count"] += 1
        row["total_centimes"] += receipt["total_centimes"]
        row["paid_centimes"] += receipt["paid_centimes"]
        row["due_centimes"] += receipt["due_centimes"]
        row["methods"].update((receipt["methods"] or "").split(","))

    for credit in conn.execute(
        "SELECT supplier_id, SUM(amount_centimes) AS amount_centimes "
        "FROM supplier_adjustments GROUP BY supplier_id"
    ):
        row = grouped.get(credit["supplier_id"])
        if row is None:
            supplier = conn.execute(
                "SELECT name FROM suppliers WHERE id = ?", (credit["supplier_id"],)
            ).fetchone()
            row = grouped[credit["supplier_id"]] = new_row(
                credit["supplier_id"], supplier["name"]
            )
        row["due_centimes"] += credit["amount_centimes"]

    for opening in conn.execute(
        """
        SELECT s.id, s.name, s.opening_balance_centimes AS opening,
               COALESCE((SELECT SUM(o.amount_centimes) FROM supplier_opening_payments o
                         WHERE o.supplier_id = s.id), 0) AS opening_paid,
               (SELECT GROUP_CONCAT(DISTINCT o.method) FROM supplier_opening_payments o
                WHERE o.supplier_id = s.id) AS methods
        FROM suppliers s
        WHERE s.opening_balance_centimes <> 0
           OR EXISTS (SELECT 1 FROM supplier_opening_payments o WHERE o.supplier_id = s.id)
        """
    ):
        row = grouped.get(opening["id"])
        if row is None:
            row = grouped[opening["id"]] = new_row(opening["id"], opening["name"])
        row["total_centimes"] += opening["opening"]
        row["paid_centimes"] += opening["opening_paid"]
        row["due_centimes"] += opening["opening"] - opening["opening_paid"]
        row["methods"].update((opening["methods"] or "").split(","))

    for row in grouped.values():
        row["methods"] = ",".join(sorted(method for method in row["methods"] if method))
    return sorted(grouped.values(), key=lambda row: row["supplier"].casefold())


def _supplier_due(conn, supplier_id: int) -> int:
    """Dette globale actuelle d'un fournisseur (0 s'il n'a aucune écriture)."""
    for row in supplier_balances(conn):
        if row["supplier_id"] == supplier_id:
            return row["due_centimes"]
    return 0


def set_supplier_current_debt(conn, actor_user_id: int | None, supplier_id: int,
                              target_centimes: int, *, at: str | None = None) -> int:
    """Aligne la dette globale d'un fournisseur sur sa valeur réelle.

    L'écart est ajouté à l'ancien solde du fournisseur. Retourne le nouvel ancien solde.
    À utiliser pour reprendre une dette qui existait avant le logiciel.
    """
    require_permission(conn, actor_user_id, "purchases")
    at = at or now_iso()
    with transaction(conn):
        supplier = conn.execute(
            "SELECT id, name, opening_balance_centimes FROM suppliers WHERE id = ?",
            (supplier_id,),
        ).fetchone()
        if supplier is None:
            raise ValidationError("Fournisseur introuvable.")
        current = _supplier_due(conn, supplier_id)
        delta = target_centimes - current
        if delta == 0:
            return supplier["opening_balance_centimes"]
        new_opening = supplier["opening_balance_centimes"] + delta
        conn.execute(
            "UPDATE suppliers SET opening_balance_centimes = ?, updated_at = ? WHERE id = ?",
            (new_opening, at, supplier_id),
        )
        log_audit(
            conn, "dette_fournisseur_ajustee", "suppliers", supplier_id,
            f"{supplier['name']} — dette {current} -> {target_centimes} centimes",
            at, actor_user_id,
        )
        return new_opening


def _record_opening_payment(conn, supplier_id: int, supplier_name: str,
                            amount_centimes: int, method: str, *,
                            note: str | None, at: str,
                            actor_user_id: int | None) -> int:
    """Enregistre un versement qui réduit l'ancien solde d'un fournisseur."""
    if method not in PAYMENT_METHODS:
        raise ValidationError("Choisissez espèces, virement bancaire ou chèque.")
    session = current_session(conn) if method == "especes" else None
    if method == "especes" and session is None:
        raise ValidationError(
            "Ouvrez la caisse avant d'enregistrer un règlement fournisseur en espèces."
        )
    cursor = conn.execute(
        "INSERT INTO supplier_opening_payments "
        "(supplier_id, method, amount_centimes, cash_session_id, note, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (supplier_id, method, amount_centimes, session["id"] if session else None,
         (note or "").strip() or None, at),
    )
    payment_id = cursor.lastrowid
    if session is not None:
        add_cash_movement(
            conn,
            session["id"],
            "depense",
            -amount_centimes,
            ref_type="SupplierOpeningPayment",
            ref_id=payment_id,
            ref_number="SOLDE-INITIAL",
            description=f"Règlement ancien solde fournisseur {supplier_name}",
            at=at,
        )
    log_audit(
        conn,
        "reglement_ancien_solde_fournisseur",
        "supplier_opening_payments",
        payment_id,
        f"{supplier_name} — {method} — {amount_centimes}",
        at,
        actor_user_id,
    )
    return payment_id


def record_supplier_payment_for_supplier(conn, supplier_id: int, amount_centimes: int,
                                         method: str, *, note: str | None = None,
                                         at: str | None = None,
                                         actor_user_id: int | None = None) -> list[int]:
    """Répartit un versement fournisseur : d'abord l'ancien solde, puis les bons les plus anciens."""
    require_permission(conn, actor_user_id, "purchases")
    if amount_centimes <= 0:
        raise ValidationError("Le montant du règlement doit être supérieur à zéro.")
    at = at or now_iso()
    with transaction(conn):
        supplier = conn.execute(
            "SELECT name FROM suppliers WHERE id = ? AND active = 1", (supplier_id,)
        ).fetchone()
        if supplier is None:
            raise ValidationError("Fournisseur introuvable ou archivé.")
        receipts = conn.execute(
            """
            SELECT r.id, r.total_centimes
            FROM purchase_receipts r
            WHERE r.supplier_id = ? AND r.status = 'validee'
            ORDER BY r.id
            """,
            (supplier_id,),
        ).fetchall()
        debts = []
        total_due = 0
        for receipt in receipts:
            paid = conn.execute(
                "SELECT COALESCE(SUM(amount_centimes), 0) FROM supplier_payments "
                "WHERE receipt_id = ?",
                (receipt["id"],),
            ).fetchone()[0]
            due = receipt["total_centimes"] - paid
            total_due += due
            if due > 0:
                debts.append((receipt["id"], due))
        adjustments = conn.execute(
            "SELECT COALESCE(SUM(amount_centimes), 0) FROM supplier_adjustments "
            "WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()[0]
        opening_due = supplier_opening_due(conn, supplier_id)
        total_due += adjustments + opening_due
        if amount_centimes > total_due:
            raise ValidationError("Le règlement dépasse la dette totale du fournisseur.")

        payment_ids = []
        remainder = amount_centimes
        if opening_due > 0:
            applied = min(remainder, opening_due)
            payment_ids.append(_record_opening_payment(
                conn, supplier_id, supplier["name"], applied, method,
                note=note, at=at, actor_user_id=actor_user_id,
            ))
            remainder -= applied
        for receipt_id, due in debts:
            if remainder == 0:
                break
            applied = min(remainder, due)
            payment_ids.append(record_supplier_payment(
                conn, receipt_id, applied, method, note=note, at=at,
                actor_user_id=actor_user_id,
            ))
            remainder -= applied
        return payment_ids


def list_suppliers(conn, *, include_archived: bool = True) -> list[dict]:
    """Liste les fiches fournisseurs avec les achats, règlements et dettes cumulés.

    Les totaux, règlements et la dette incluent l'ancien solde du fournisseur.
    """
    where = "" if include_archived else "WHERE s.active = 1"
    return [dict(row) for row in conn.execute(
        f"""
        WITH totals AS (
            SELECT supplier_id, COUNT(*) AS receipt_count,
                   SUM(total_centimes) AS total_centimes
            FROM purchase_receipts
            WHERE supplier_id IS NOT NULL AND status = 'validee'
            GROUP BY supplier_id
        ), payments AS (
            SELECT r.supplier_id, SUM(p.amount_centimes) AS paid_centimes
            FROM purchase_receipts r
            JOIN supplier_payments p ON p.receipt_id = r.id
            WHERE r.supplier_id IS NOT NULL AND r.status = 'validee'
            GROUP BY r.supplier_id
        ), opening_pay AS (
            SELECT supplier_id, SUM(amount_centimes) AS paid_centimes
            FROM supplier_opening_payments
            GROUP BY supplier_id
        ), adjustments AS (
            SELECT supplier_id, SUM(amount_centimes) AS amount_centimes
            FROM supplier_adjustments
            GROUP BY supplier_id
        )
        SELECT s.id, s.name, s.phone, s.email, s.address, s.notes, s.active,
               s.created_at, s.updated_at,
               s.opening_balance_centimes,
               COALESCE(t.receipt_count, 0) AS receipt_count,
               COALESCE(t.total_centimes, 0) + s.opening_balance_centimes AS total_centimes,
               COALESCE(p.paid_centimes, 0) + COALESCE(op.paid_centimes, 0) AS paid_centimes,
               COALESCE(t.total_centimes, 0) + s.opening_balance_centimes
                   - COALESCE(p.paid_centimes, 0) - COALESCE(op.paid_centimes, 0)
                   + COALESCE(a.amount_centimes, 0) AS due_centimes
        FROM suppliers s
        LEFT JOIN totals t ON t.supplier_id = s.id
        LEFT JOIN payments p ON p.supplier_id = s.id
        LEFT JOIN opening_pay op ON op.supplier_id = s.id
        LEFT JOIN adjustments a ON a.supplier_id = s.id
        {where}
        ORDER BY s.active DESC, s.name COLLATE NOCASE
        """
    )]


def _validated_supplier_fields(name: str, phone: str | None, email: str | None,
                               address: str | None, notes: str | None) -> tuple[str, ...]:
    name = (name or "").strip()
    if not 2 <= len(name) <= 120:
        raise ValidationError("Le nom du fournisseur doit contenir entre 2 et 120 caractères.")
    values = tuple((value or "").strip() or None for value in (phone, email, address, notes))
    phone, email, address, notes = values
    if phone and len(phone) > 40:
        raise ValidationError("Le téléphone du fournisseur ne peut pas dépasser 40 caractères.")
    if email and (
        len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        raise ValidationError("Saisissez une adresse e-mail fournisseur valide.")
    if address and len(address) > 250:
        raise ValidationError("L'adresse du fournisseur ne peut pas dépasser 250 caractères.")
    if notes and len(notes) > 1000:
        raise ValidationError("Les notes ne peuvent pas dépasser 1 000 caractères.")
    return name, phone, email, address, notes


def create_supplier(conn, actor_user_id: int | None, name: str, *,
                    phone: str | None = None, email: str | None = None,
                    address: str | None = None, notes: str | None = None,
                    at: str | None = None) -> int:
    """Crée une fiche fournisseur ; les doublons de nom sont refusés."""
    require_permission(conn, actor_user_id, "purchases")
    name, phone, email, address, notes = _validated_supplier_fields(
        name, phone, email, address, notes
    )
    at = at or now_iso()
    with transaction(conn):
        try:
            cursor = conn.execute(
                "INSERT INTO suppliers "
                "(name, phone, email, address, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, phone, email, address, notes, at, at),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Un fournisseur portant ce nom existe déjà.") from exc
        supplier_id = cursor.lastrowid
        log_audit(conn, "fournisseur_cree", "suppliers", supplier_id, name, at, actor_user_id)
    return supplier_id


def update_supplier(conn, actor_user_id: int | None, supplier_id: int, name: str, *,
                    phone: str | None = None, email: str | None = None,
                    address: str | None = None, notes: str | None = None,
                    at: str | None = None) -> None:
    """Met à jour les coordonnées d'une fiche sans réécrire les anciennes réceptions."""
    require_permission(conn, actor_user_id, "purchases")
    name, phone, email, address, notes = _validated_supplier_fields(
        name, phone, email, address, notes
    )
    at = at or now_iso()
    with transaction(conn):
        target = conn.execute(
            "SELECT id, name FROM suppliers WHERE id = ?", (supplier_id,)
        ).fetchone()
        if target is None:
            raise ValidationError("Fournisseur introuvable.")
        try:
            conn.execute(
                "UPDATE suppliers SET name = ?, phone = ?, email = ?, address = ?, "
                "notes = ?, updated_at = ? WHERE id = ?",
                (name, phone, email, address, notes, at, supplier_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Un fournisseur portant ce nom existe déjà.") from exc
        log_audit(
            conn, "fournisseur_modifie", "suppliers", supplier_id,
            f"{target['name']} -> {name}", at, actor_user_id,
        )


def set_supplier_active(conn, actor_user_id: int | None, supplier_id: int,
                        active: bool, *, at: str | None = None) -> None:
    """Archive ou réactive une fiche ; son historique et ses dettes restent consultables."""
    require_permission(conn, actor_user_id, "purchases")
    at = at or now_iso()
    with transaction(conn):
        supplier = conn.execute(
            "SELECT name, active FROM suppliers WHERE id = ?", (supplier_id,)
        ).fetchone()
        if supplier is None:
            raise ValidationError("Fournisseur introuvable.")
        new_active = int(bool(active))
        if supplier["active"] == new_active:
            return
        conn.execute(
            "UPDATE suppliers SET active = ?, updated_at = ? WHERE id = ?",
            (new_active, at, supplier_id),
        )
        log_audit(
            conn,
            "fournisseur_reactive" if active else "fournisseur_archive",
            "suppliers",
            supplier_id,
            supplier["name"],
            at,
            actor_user_id,
        )


def record_supplier_payment(conn, receipt_id: int, amount_centimes: int, method: str, *,
                            note: str | None = None, at: str | None = None,
                            actor_user_id: int | None = None) -> int:
    """Enregistre un règlement total ou partiel d'une réception fournisseur."""
    require_permission(conn, actor_user_id, "purchases")
    if method not in PAYMENT_METHODS:
        raise ValidationError("Choisissez espèces, virement bancaire ou chèque.")
    if amount_centimes <= 0:
        raise ValidationError("Le montant du règlement doit être supérieur à zéro.")
    at = at or now_iso()
    with transaction(conn):
        receipt = conn.execute(
            "SELECT id, number, supplier, total_centimes, status "
            "FROM purchase_receipts WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if receipt is None:
            raise ValidationError("Réception fournisseur introuvable.")
        if receipt["status"] != "validee":
            raise ValidationError("Une réception annulée ne peut pas recevoir de règlement.")
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_centimes), 0) FROM supplier_payments "
            "WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()[0]
        due = receipt["total_centimes"] - paid
        if amount_centimes > due:
            raise ValidationError("Le règlement dépasse le solde restant dû.")

        session = current_session(conn) if method == "especes" else None
        if method == "especes" and session is None:
            raise ValidationError(
                "Ouvrez la caisse avant d'enregistrer un règlement fournisseur en espèces."
            )
        cursor = conn.execute(
            "INSERT INTO supplier_payments "
            "(receipt_id, method, amount_centimes, cash_session_id, note, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (receipt_id, method, amount_centimes, session["id"] if session else None,
             (note or "").strip() or None, at),
        )
        payment_id = cursor.lastrowid
        if session is not None:
            add_cash_movement(
                conn,
                session["id"],
                "depense",
                -amount_centimes,
                ref_type="SupplierPayment",
                ref_id=payment_id,
                ref_number=receipt["number"],
                description=f"Règlement fournisseur {receipt['supplier']} — {receipt['number']}",
                at=at,
            )
        log_audit(
            conn,
            "reglement_fournisseur",
            "supplier_payments",
            payment_id,
            f"{receipt['number']} — {method} — {amount_centimes}",
            at,
            actor_user_id,
        )
        return payment_id
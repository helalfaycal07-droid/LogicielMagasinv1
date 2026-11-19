"""Retours de vente, remboursements et remise en stock contrôlée."""

from dataclasses import dataclass

from .cash import require_open_session, add_cash_movement
from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .loyalty import reverse_sale_points_for_return
from .sequences import next_number
from . import serials
from .stock import add_movement
from .vouchers import record_voucher_issue
from .users import require_permission

REFUND_METHODS = ("especes", "avoir")


@dataclass
class ReturnLine:
    sale_item_id: int
    quantity: int
    resalable: bool = False
    imeis: list[str] | None = None


def _sale_items(conn, sale_id: int) -> list:
    return conn.execute(
        """
        SELECT i.*, p.name AS product_name, p.reference, p.track_serial,
               COALESCE((
                   SELECT SUM(ri.quantity)
                   FROM sale_return_items ri
                   JOIN sale_returns r ON r.id = ri.return_id
                   WHERE ri.sale_item_id = i.id
               ), 0) AS returned_quantity
        FROM sale_items i
        JOIN products p ON p.id = i.product_id
        WHERE i.sale_id = ?
        ORDER BY i.id
        """,
        (sale_id,),
    ).fetchall()


def get_sale_for_return(conn, sale_number: str) -> dict | None:
    """Recherche une vente et ses quantités encore retournables par numéro."""
    number = (sale_number or "").strip()
    if not number:
        raise ValidationError("Saisissez le numéro de la vente.")
    sale = conn.execute(
        """
        SELECT s.*, c.name AS customer_name
        FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
        WHERE s.number = ? AND s.status IN ('validee', 'retournee')
        """,
        (number,),
    ).fetchone()
    if sale is None:
        return None
    return {"sale": dict(sale), "items": [dict(row) for row in _sale_items(conn, sale["id"])]}


def list_sales_for_return(
    conn, limit: int = 50, search: str = "", offset: int = 0
) -> list[dict]:
    """Liste une page de ventes retournables récentes, filtrées par numéro ou client."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValidationError("Le nombre de ventes à afficher doit être positif.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValidationError("Le décalage des ventes à afficher ne peut pas être négatif.")
    search = search.strip()
    escaped_search = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = f"%{escaped_search}%"
    rows = conn.execute(
        """
        SELECT s.id, s.number, s.created_at, s.total_centimes,
               c.name AS customer_name
        FROM sales s
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE s.status IN ('validee', 'retournee')
          AND (? = '' OR s.number LIKE ? ESCAPE '!' COLLATE NOCASE
               OR COALESCE(c.name, '') LIKE ? ESCAPE '!' COLLATE NOCASE)
          AND EXISTS (
              SELECT 1
              FROM sale_items i
              WHERE i.sale_id = s.id
                AND i.quantity > COALESCE((
                    SELECT SUM(ri.quantity)
                    FROM sale_return_items ri
                    JOIN sale_returns r ON r.id = ri.return_id
                    WHERE ri.sale_item_id = i.id
                ), 0)
          )
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT ?
        OFFSET ?
        """,
        (search, pattern, pattern, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def _net_item_totals(items: list, sale_total: int) -> dict[int, int]:
    subtotal = sum(item["total_centimes"] for item in items)
    if subtotal == 0:
        if sale_total:
            raise ValidationError("Le total de la vente ne correspond pas à ses lignes.")
        return {item["id"]: 0 for item in items}

    shares = {
        item["id"]: (item["total_centimes"] * sale_total) // subtotal
        for item in items
    }
    remaining = sale_total - sum(shares.values())
    priority = sorted(
        items,
        key=lambda item: (
            -(item["total_centimes"] * sale_total % subtotal),
            item["id"],
        ),
    )
    for item in priority[:remaining]:
        shares[item["id"]] += 1
    return shares


def _calculate_return(conn, sale_id: int, lines: list[ReturnLine],
                      *, validate_imeis: bool) -> dict:
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if sale is None or sale["status"] not in ("validee", "retournee"):
        raise ValidationError("Cette vente n'existe pas ou ne peut plus être retournée.")
    if not lines:
        raise ValidationError("Sélectionnez au moins un article à retourner.")

    items = _sale_items(conn, sale_id)
    by_id = {item["id"]: item for item in items}
    shares = _net_item_totals(items, sale["total_centimes"])
    seen_items: set[int] = set()
    seen_serials: set[int] = set()
    calculated = []
    total_centimes = 0

    for request in lines:
        if request.sale_item_id in seen_items:
            raise ValidationError("Une ligne de vente ne peut apparaître qu'une fois dans un retour.")
        seen_items.add(request.sale_item_id)
        item = by_id.get(request.sale_item_id)
        if item is None:
            raise ValidationError("Une ligne sélectionnée n'appartient pas à cette vente.")
        if request.quantity <= 0:
            raise ValidationError("La quantité à retourner doit être supérieure à zéro.")
        available = item["quantity"] - item["returned_quantity"]
        if request.quantity > available:
            raise ValidationError(
                f"« {item['product_name']} » : {available} article(s) encore retournable(s), "
                f"{request.quantity} demandé(s)."
            )

        imeis: list[str] = []
        serial_ids: list[int] = []
        if item["track_serial"]:
            raw_imeis = request.imeis or []
            if validate_imeis:
                if len(raw_imeis) != request.quantity:
                    raise ValidationError(
                        f"« {item['product_name']} » : saisissez un IMEI pour chaque appareil retourné."
                    )
                for raw_imei in raw_imeis:
                    imei = serials.validate_imei(raw_imei, "IMEI de l'appareil retourné")
                    unit = serials.get_unit(conn, imei)
                    if (unit is None or unit["product_id"] != item["product_id"]
                            or unit["sale_id"] != sale_id or unit["status"] != "vendu"):
                        raise ValidationError(
                            f"L'IMEI {imei} n'est pas un appareil vendu dans cette vente."
                        )
                    if unit["id"] in seen_serials:
                        raise ValidationError(f"L'IMEI {imei} est saisi deux fois.")
                    seen_serials.add(unit["id"])
                    imeis.append(imei)
                    serial_ids.append(unit["id"])
        elif request.imeis:
            raise ValidationError(f"« {item['product_name']} » n'est pas suivi par IMEI.")

        previous_quantity = item["returned_quantity"]
        new_quantity = previous_quantity + request.quantity
        amount = (
            shares[item["id"]] * new_quantity // item["quantity"]
            - shares[item["id"]] * previous_quantity // item["quantity"]
        )
        total_centimes += amount
        calculated.append({
            "item": item,
            "quantity": request.quantity,
            "amount_centimes": amount,
            "resalable": bool(request.resalable),
            "serial_ids": serial_ids,
            "imeis": imeis,
        })

    already_returned = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) FROM sale_return_items "
        "WHERE sale_item_id IN (SELECT id FROM sale_items WHERE sale_id = ?)",
        (sale_id,),
    ).fetchone()[0]
    paid_at_sale = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) FROM sale_payments WHERE sale_id = ?",
        (sale_id,),
    ).fetchone()[0]
    paid_since = conn.execute(
        "SELECT COALESCE(SUM(amount_centimes), 0) FROM payment_allocations WHERE sale_id = ?",
        (sale_id,),
    ).fetchone()[0]
    due = max(0, sale["total_centimes"] - already_returned - paid_at_sale - paid_since)
    debt_reduction = min(total_centimes, due)
    return {
        "sale": sale,
        "lines": calculated,
        "total_centimes": total_centimes,
        "debt_reduction_centimes": debt_reduction,
        "refund_centimes": total_centimes - debt_reduction,
    }


def preview_return(conn, sale_id: int, lines: list[ReturnLine]) -> dict:
    """Calcule le montant d'un retour sans l'enregistrer."""
    result = _calculate_return(conn, sale_id, lines, validate_imeis=False)
    return {key: result[key] for key in (
        "total_centimes", "debt_reduction_centimes", "refund_centimes"
    )}


def _reduce_installments(conn, sale_id: int, amount_centimes: int, at: str) -> None:
    if amount_centimes <= 0:
        return
    plan = conn.execute(
        "SELECT id FROM payment_plans WHERE sale_id = ?", (sale_id,)
    ).fetchone()
    if plan is None:
        return

    remaining = amount_centimes
    installments = conn.execute(
        "SELECT * FROM installments WHERE plan_id = ? ORDER BY due_date DESC, number DESC",
        (plan["id"],),
    ).fetchall()
    for installment in installments:
        outstanding = (
            installment["amount_centimes"] - installment["paid_centimes"]
            - installment["cancelled_centimes"]
        )
        cancelled = min(remaining, outstanding)
        if cancelled:
            conn.execute(
                "UPDATE installments SET cancelled_centimes = cancelled_centimes + ? WHERE id = ?",
                (cancelled, installment["id"]),
            )
            remaining -= cancelled
            if cancelled == outstanding:
                from .credit import _release_cheques
                _release_cheques(conn, installment["id"], at)
        if remaining == 0:
            break
    if remaining:
        raise ValidationError("Le montant du retour dépasse le solde des échéances restantes.")


def create_return(conn, sale_id: int, lines: list[ReturnLine], refund_method: str, reason: str,
                  *, register_id: int = 1, at: str | None = None,
                  actor_user_id: int | None = None) -> dict:
    """Enregistre le retour, le stock, la dette et le remboursement dans une transaction."""
    require_permission(conn, actor_user_id, "return")
    if refund_method not in REFUND_METHODS:
        raise ValidationError("Choisissez un remboursement en espèces ou en avoir.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Une raison est obligatoire pour enregistrer le retour.")
    at = at or now_iso()

    with transaction(conn):
        result = _calculate_return(conn, sale_id, lines, validate_imeis=True)
        sale = result["sale"]
        refund = result["refund_centimes"]
        if refund_method == "avoir" and refund and sale["customer_id"] is None:
            raise ValidationError("Un avoir exige une vente liée à une fiche client.")
        session = (
            require_open_session(conn, register_id)
            if refund and refund_method == "especes"
            else None
        )
        number = next_number(conn, "RET")
        cursor = conn.execute(
            "INSERT INTO sale_returns (number, sale_id, customer_id, total_centimes, "
            "debt_reduction_centimes, refund_centimes, refund_method, session_id, reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (number, sale_id, sale["customer_id"], result["total_centimes"],
             result["debt_reduction_centimes"], refund, refund_method,
             session["id"] if session else None, reason, at),
        )
        return_id = cursor.lastrowid

        for returned in result["lines"]:
            item = returned["item"]
            cursor = conn.execute(
                "INSERT INTO sale_return_items "
                "(return_id, sale_item_id, quantity, amount_centimes, restocked) "
                "VALUES (?,?,?,?,?)",
                (return_id, item["id"], returned["quantity"],
                 returned["amount_centimes"], int(returned["resalable"])),
            )
            return_item_id = cursor.lastrowid
            if item["track_serial"]:
                for serial_id in returned["serial_ids"]:
                    conn.execute(
                        "INSERT INTO sale_return_serials (return_item_id, serial_id) VALUES (?,?)",
                        (return_item_id, serial_id),
                    )
                    new_status = "en_stock" if returned["resalable"] else "retourne"
                    conn.execute(
                        "UPDATE product_serials SET status = ? WHERE id = ?",
                        (new_status, serial_id),
                    )
                if returned["resalable"]:
                    add_movement(
                        conn, item["product_id"], returned["quantity"], "retour_client",
                        ref_type="SaleReturn", ref_id=return_id, ref_number=number,
                        note=f"Retour de {sale['number']} — {item['product_name']}", at=at,
                    )
            elif returned["resalable"]:
                add_movement(
                    conn, item["product_id"], returned["quantity"], "retour_client",
                    ref_type="SaleReturn", ref_id=return_id, ref_number=number,
                    note=f"Retour de {sale['number']} — {item['product_name']}", at=at,
                )

        _reduce_installments(
            conn, sale_id, result["debt_reduction_centimes"], at
        )
        if refund:
            if refund_method == "especes":
                add_cash_movement(
                    conn, session["id"], "sortie", -refund, ref_type="SaleReturn",
                    ref_id=return_id, ref_number=number,
                    description=f"Remboursement du retour {number}", at=at,
                )
            else:
                record_voucher_issue(conn, sale["customer_id"], refund, return_id, at)

        remaining_quantity = conn.execute(
            """
            SELECT COUNT(*) FROM sale_items i
            WHERE i.sale_id = ? AND i.quantity > COALESCE((
                SELECT SUM(ri.quantity)
                FROM sale_return_items ri
                WHERE ri.sale_item_id = i.id
            ), 0)
            """,
            (sale_id,),
        ).fetchone()[0]
        if remaining_quantity == 0:
            conn.execute("UPDATE sales SET status = 'retournee' WHERE id = ?", (sale_id,))

        cumulative_returned = conn.execute(
            "SELECT COALESCE(SUM(ri.amount_centimes), 0) "
            "FROM sale_return_items ri "
            "JOIN sale_items si ON si.id = ri.sale_item_id "
            "WHERE si.sale_id = ?",
            (sale_id,),
        ).fetchone()[0]
        reverse_sale_points_for_return(
            conn, sale_id, return_id, cumulative_returned,
            fully_returned=remaining_quantity == 0, at=at,
            actor_user_id=actor_user_id,
        )

        log_audit(
            conn, "retour_vente", "sale_returns", return_id,
            f"{number} — vente {sale['number']} — montant {result['total_centimes']} — {reason}",
            at, actor_user_id,
        )
        return {
            "return_id": return_id,
            "number": number,
            "sale_id": sale_id,
            "total_centimes": result["total_centimes"],
            "debt_reduction_centimes": result["debt_reduction_centimes"],
            "refund_centimes": refund,
            "refund_method": refund_method,
        }

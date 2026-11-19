"""Effacement des historiques commerciaux sans modifier les données de référence."""

from .clock import now_iso
from .db import log_audit, transaction
from .errors import AuthenticationError, PermissionDenied
from .users import authenticate, require_permission


def reinitialiser_historique(conn, actor_user_id: int, mot_de_passe: str,
                             *, at: str | None = None) -> None:
    """Efface les opérations commerciales et conserve les produits, le stock et les comptes."""
    require_permission(conn, actor_user_id, "manage_users")
    at = at or now_iso()

    with transaction(conn):
        admin = conn.execute(
            "SELECT username, role, active FROM users WHERE id = ?", (actor_user_id,)
        ).fetchone()
        if admin is None or not admin["active"] or admin["role"] != "administrateur":
            raise PermissionDenied("Seul un administrateur actif peut réinitialiser l'historique.")
        authenticated = authenticate(conn, admin["username"], mot_de_passe)
        if authenticated["id"] != actor_user_id:
            raise AuthenticationError("Le mot de passe administrateur est incorrect.")

        stock = conn.execute(
            "SELECT product_id, warehouse_id, SUM(quantity) AS quantity "
            "FROM stock_movements GROUP BY product_id, warehouse_id "
            "HAVING SUM(quantity) <> 0 ORDER BY product_id, warehouse_id"
        ).fetchall()

        conn.execute(
            "UPDATE product_serials SET entry_movement_id = NULL, sale_id = NULL, sold_at = NULL, "
            "status = CASE WHEN status IN ('vendu', 'retourne') THEN 'sorti' ELSE status END"
        )
        conn.execute("UPDATE sales SET quote_id = NULL")
        conn.execute("UPDATE quotes SET converted_sale_id = NULL")

        for table in (
            "sale_return_serials",
            "sale_return_items",
            "customer_credit_movements",
            "loyalty_movements",
            "payment_allocations",
            "cheques",
            "installments",
            "payment_plans",
            "sale_payments",
            "customer_payments",
            "sale_returns",
            "sale_items",
            "quote_items",
            "quotes",
            "sales",
            "supplier_payments",
            "supplier_adjustments",
            "purchase_receipt_items",
            "purchase_receipts",
            "customer_adjustments",
            "cash_movements",
            "cash_sessions",
            "stock_movements",
        ):
            conn.execute(f"DELETE FROM {table}")

        conn.execute("UPDATE customers SET status = 'actif' WHERE status = 'en_retard'")
        for movement in stock:
            quantity = movement["quantity"]
            conn.execute(
                "INSERT INTO stock_movements "
                "(product_id, warehouse_id, movement_type, quantity, qty_before, qty_after, "
                "note, created_at) VALUES (?, ?, 'inventaire', ?, 0, ?, ?, ?)",
                (
                    movement["product_id"],
                    movement["warehouse_id"],
                    quantity,
                    quantity,
                    "Stock conservé après réinitialisation de l'historique",
                    at,
                ),
            )

        conn.execute("DELETE FROM audit_log")
        conn.execute(
            "UPDATE document_sequences SET last_number = 0 "
            "WHERE code IN ('VTE', 'RET', 'REC', 'DEV')"
        )
        log_audit(
            conn,
            "historique_ventes_reinitialise",
            details="Historique commercial effacé; produits, stock, clients, fournisseurs et utilisateurs conservés.",
            at=at,
            actor_user_id=actor_user_id,
        )

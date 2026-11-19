"""Rapports de ventes, retours, paiements, stock actuel et clôtures de caisse."""

from datetime import date

from .errors import ValidationError
from .products import stock_report


def _validate_period(start_date: str, end_date: str) -> None:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Les dates du rapport doivent être au format AAAA-MM-JJ.") from exc
    if start.isoformat() != start_date or end.isoformat() != end_date:
        raise ValidationError("Les dates du rapport doivent être au format AAAA-MM-JJ.")
    if start > end:
        raise ValidationError("La date de début doit précéder ou égaler la date de fin.")


def report_data(conn, start_date: str, end_date: str) -> dict:
    """Retourne les ventes et encaissements de la période et l'état actuel du stock."""
    _validate_period(start_date, end_date)
    period = (start_date, end_date)

    sales = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_centimes), 0) AS total "
        "FROM sales WHERE status IN ('validee', 'retournee') "
        "AND substr(created_at, 1, 10) BETWEEN ? AND ?",
        period,
    ).fetchone()
    returns = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_centimes), 0) AS total "
        "FROM sale_returns WHERE substr(created_at, 1, 10) BETWEEN ? AND ?",
        period,
    ).fetchone()
    products_sold = conn.execute(
        """
        SELECT reference, name, SUM(quantity) AS quantity,
               SUM(total_centimes) AS total_centimes
        FROM (
            SELECT p.reference, p.name, i.quantity, i.total_centimes
            FROM sale_items i
            JOIN sales s ON s.id = i.sale_id
            JOIN products p ON p.id = i.product_id
            WHERE s.status IN ('validee', 'retournee')
              AND substr(s.created_at, 1, 10) BETWEEN ? AND ?
            UNION ALL
            SELECT p.reference, p.name, -ri.quantity, -ri.amount_centimes
            FROM sale_return_items ri
            JOIN sale_returns r ON r.id = ri.return_id
            JOIN sale_items i ON i.id = ri.sale_item_id
            JOIN products p ON p.id = i.product_id
            WHERE substr(r.created_at, 1, 10) BETWEEN ? AND ?
        )
        GROUP BY reference, name
        ORDER BY total_centimes DESC, name
        LIMIT 20
        """,
        (*period, *period),
    ).fetchall()
    return_rows = conn.execute(
        """
        SELECT r.number, r.created_at, s.number AS sale_number, p.reference,
               p.name AS product_name, ri.quantity, ri.amount_centimes,
               r.refund_method, r.reason
        FROM sale_returns r
        JOIN sales s ON s.id = r.sale_id
        JOIN sale_return_items ri ON ri.return_id = r.id
        JOIN sale_items i ON i.id = ri.sale_item_id
        JOIN products p ON p.id = i.product_id
        WHERE substr(r.created_at, 1, 10) BETWEEN ? AND ?
        ORDER BY r.created_at DESC, r.id DESC, ri.id
        """,
        period,
    ).fetchall()
    payments = conn.execute(
        """
        SELECT method, SUM(amount_centimes) AS total_centimes
        FROM (
            SELECT sp.method, sp.amount_centimes
            FROM sale_payments sp
            JOIN sales s ON s.id = sp.sale_id
            WHERE s.status IN ('validee', 'retournee')
              AND substr(s.created_at, 1, 10) BETWEEN ? AND ?
            UNION ALL
            SELECT method, amount_centimes
            FROM customer_payments
            WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
            UNION ALL
            SELECT refund_method AS method, -refund_centimes AS amount_centimes
            FROM sale_returns
            WHERE refund_centimes > 0 AND substr(created_at, 1, 10) BETWEEN ? AND ?
        )
        GROUP BY method
        ORDER BY total_centimes DESC, method
        """,
        (*period, *period, *period),
    ).fetchall()
    cash_closures = conn.execute(
        """
        SELECT c.closed_at, r.name AS register_name, c.expected_centimes,
               c.counted_centimes, c.difference_centimes, c.difference_reason
        FROM cash_sessions c
        JOIN cash_registers r ON r.id = c.register_id
        WHERE c.status = 'closed' AND substr(c.closed_at, 1, 10) BETWEEN ? AND ?
        ORDER BY c.closed_at DESC, c.id DESC
        """,
        period,
    ).fetchall()
    inventory = sorted(
        stock_report(conn),
        key=lambda product: product["valeur_cout_centimes"],
        reverse=True,
    )

    return {
        "sales_count": sales["count"],
        "sales_total_centimes": sales["total"],
        "returns_count": returns["count"],
        "returns_total_centimes": returns["total"],
        "net_sales_total_centimes": sales["total"] - returns["total"],
        "products_sold": [dict(row) for row in products_sold],
        "returns": [dict(row) for row in return_rows],
        "payments": [dict(row) for row in payments],
        "cash_closures": [dict(row) for row in cash_closures],
        "inventory": inventory,
        "inventory_value_centimes": sum(p["valeur_cout_centimes"] for p in inventory),
        "low_stock_count": sum(1 for p in inventory if p["bas"]),
        "negative_stock_count": sum(1 for p in inventory if p["negatif"]),
    }


def profit_report_data(conn, start_date: str, end_date: str,
                       grouping: str = "month") -> dict:
    """Calcule la marge brute par mois ou année à partir du coût capturé à la vente.

    Les retours réduisent le chiffre d'affaires. Le coût n'est reversé que pour
    les articles effectivement remis en stock. Une période avec des coûts historiques
    manquants est signalée comme incomplète plutôt que présentée comme un bénéfice exact.
    """
    _validate_period(start_date, end_date)
    if grouping not in ("month", "year"):
        raise ValidationError("Choisissez un regroupement mensuel ou annuel.")

    period_sql = "substr(created_at, 1, 7)" if grouping == "month" else "substr(created_at, 1, 4)"
    rows = conn.execute(
        f"""
        WITH movements AS (
            SELECT {period_sql} AS period,
                   s.total_centimes AS revenue_centimes,
                   0 AS return_centimes,
                   COALESCE(SUM(i.quantity * i.unit_cost_centimes), 0) AS cost_centimes,
                   COALESCE(SUM(CASE WHEN i.unit_cost_centimes IS NULL
                                     THEN i.quantity ELSE 0 END), 0) AS unknown_cost_quantity
            FROM sales s
            JOIN sale_items i ON i.sale_id = s.id
            WHERE s.status IN ('validee', 'retournee')
              AND substr(s.created_at, 1, 10) BETWEEN ? AND ?
            GROUP BY s.id
            UNION ALL
            SELECT {period_sql.replace("created_at", "r.created_at")} AS period,
                   0 AS revenue_centimes,
                   r.total_centimes AS return_centimes,
                   -COALESCE(SUM(CASE WHEN ri.restocked = 1
                                      THEN ri.quantity * i.unit_cost_centimes ELSE 0 END), 0)
                       AS cost_centimes,
                   COALESCE(SUM(CASE WHEN ri.restocked = 1
                                         AND i.unit_cost_centimes IS NULL
                                     THEN ri.quantity ELSE 0 END), 0) AS unknown_cost_quantity
            FROM sale_returns r
            JOIN sale_return_items ri ON ri.return_id = r.id
            JOIN sale_items i ON i.id = ri.sale_item_id
            WHERE substr(r.created_at, 1, 10) BETWEEN ? AND ?
            GROUP BY r.id
        )
        SELECT period, SUM(revenue_centimes) AS revenue_centimes,
               SUM(return_centimes) AS return_centimes,
               SUM(cost_centimes) AS cost_centimes,
               SUM(unknown_cost_quantity) AS unknown_cost_quantity
        FROM movements
        GROUP BY period
        ORDER BY period
        """,
        (start_date, end_date, start_date, end_date),
    ).fetchall()

    periods = []
    for row in rows:
        revenue = row["revenue_centimes"]
        returned = row["return_centimes"]
        cost = row["cost_centimes"]
        unknown = row["unknown_cost_quantity"]
        net_revenue = revenue - returned
        net_cost = cost
        periods.append({
            "period": row["period"],
            "revenue_centimes": revenue,
            "returns_centimes": returned,
            "net_revenue_centimes": net_revenue,
            "cost_centimes": net_cost,
            "unknown_cost_quantity": unknown,
            "profit_centimes": net_revenue - net_cost if unknown == 0 else None,
            "complete": unknown == 0,
        })

    return {
        "grouping": grouping,
        "periods": periods,
        "profit_complete": all(row["complete"] for row in periods),
        "unknown_cost_quantity": sum(row["unknown_cost_quantity"] for row in periods),
        "net_revenue_centimes": sum(row["net_revenue_centimes"] for row in periods),
        "cost_centimes": sum(row["cost_centimes"] for row in periods),
        "profit_centimes": (
            sum(row["profit_centimes"] for row in periods)
            if all(row["complete"] for row in periods) else None
        ),
    }

"""Produits : fiche, prix, indicateurs. Le stock vient des mouvements."""

import sqlite3

from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .money import margin_percent, markup_percent
from .stock import add_movement, stock_of
from .users import require_permission


def create_product(conn, reference: str, name: str, price_centimes: int, *,
                   cost_centimes: int = 0, barcode: str | None = None,
                   category: str | None = None, brand: str | None = None,
                   price_includes_tax: bool = True, tax_rate_bp: int = 1900,
                   min_stock: int = 0, track_serial: bool = False,
                   initial_stock: int = 0, at: str | None = None,
                   actor_user_id: int | None = None) -> int:
    require_permission(conn, actor_user_id, "manage_products")
    reference = (reference or "").strip()
    name = (name or "").strip()
    if not reference or not name:
        raise ValidationError("Le produit a besoin d'une référence et d'un nom.")
    if price_centimes < 0 or cost_centimes < 0:
        raise ValidationError("Un prix ne peut pas être négatif.")
    at = at or now_iso()
    with transaction(conn):
        try:
            cursor = conn.execute(
                "INSERT INTO products (reference, name, barcode, category, brand, cost_centimes, "
                "price_centimes, price_includes_tax, tax_rate_bp, min_stock, track_serial, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (reference, name, barcode, category, brand, cost_centimes, price_centimes,
                 int(price_includes_tax), tax_rate_bp, min_stock, int(track_serial), at),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(_duplicate_message(conn, reference, barcode)) from exc
        product_id = cursor.lastrowid
        if initial_stock:
            add_movement(conn, product_id, initial_stock, "inventaire", note="Stock initial", at=at)
        log_audit(conn, "produit_cree", "products", product_id, reference, at, actor_user_id)
    return product_id


def _duplicate_message(conn, reference: str, barcode: str | None) -> str:
    row = conn.execute("SELECT name, archived FROM products WHERE reference = ? OR (barcode IS NOT NULL AND barcode = ?)",
                       (reference, barcode)).fetchone()
    if row and row["archived"]:
        return (f"Cette référence ou ce code-barres est utilisé par un produit archivé (« {row['name']} »). "
                "Restaurez-le (« Voir les archivés ») ou choisissez une autre référence.")
    return "Cette référence ou ce code-barres existe déjà pour un autre produit."


def get_product(conn, product_id: int):
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def find_product(conn, text: str):
    """Recherche par code-barres, référence exacte, puis par nom."""
    text = text.strip()
    rows = conn.execute(
        "SELECT * FROM products WHERE archived = 0 "
        "AND (barcode = ? OR reference = ? COLLATE NOCASE) ORDER BY reference, id",
        (text, text),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        "SELECT * FROM products WHERE archived = 0 AND name LIKE ? ORDER BY name", (f"%{text}%",)
    ).fetchall()


def archive_product(conn, product_id: int, at: str | None = None,
                    actor_user_id: int | None = None) -> None:
    """Retire un produit de la liste sans rien effacer : son historique est conservé."""
    require_permission(conn, actor_user_id, "manage_products")
    with transaction(conn):
        product = get_product(conn, product_id)
        if product is None:
            raise ValidationError("Produit introuvable.")
        conn.execute("UPDATE products SET archived = 1 WHERE id = ?", (product_id,))
        log_audit(conn, "produit_archive", "products", product_id,
                  f"{product['reference']} — {product['name']}", at, actor_user_id)


def restore_product(conn, product_id: int, at: str | None = None,
                    actor_user_id: int | None = None) -> None:
    """Remet un produit archivé dans la liste."""
    require_permission(conn, actor_user_id, "manage_products")
    with transaction(conn):
        product = get_product(conn, product_id)
        if product is None:
            raise ValidationError("Produit introuvable.")
        conn.execute("UPDATE products SET archived = 0 WHERE id = ?", (product_id,))
        log_audit(conn, "produit_restaure", "products", product_id,
                  f"{product['reference']} — {product['name']}", at, actor_user_id)


def product_usage(conn, product_id: int) -> dict:
    """Ce qui empêche de supprimer définitivement un produit (historique à conserver)."""
    sale_lines = conn.execute("SELECT COUNT(*) AS n FROM sale_items WHERE product_id = ?", (product_id,)).fetchone()["n"]
    units = conn.execute("SELECT COUNT(*) AS n FROM product_serials WHERE product_id = ?", (product_id,)).fetchone()["n"]
    other_moves = conn.execute("SELECT COUNT(*) AS n FROM stock_movements WHERE product_id = ? "
                               "AND movement_type <> 'inventaire'", (product_id,)).fetchone()["n"]
    reasons = []
    if sale_lines:
        reasons.append(f"{sale_lines} ligne(s) de vente")
    if units:
        reasons.append(f"{units} appareil(s) enregistré(s) avec IMEI")
    if other_moves:
        reasons.append(f"{other_moves} mouvement(s) de stock (ventes, réceptions, ajustements)")
    return {"sale_lines": sale_lines, "units": units, "other_movements": other_moves,
            "reasons": reasons, "deletable": not reasons}


def delete_product(conn, product_id: int, at: str | None = None,
                   actor_user_id: int | None = None) -> None:
    """Supprime pour de bon un produit SANS historique (créé par erreur, essai...).

    Refusé dès qu'il y a une vente, un appareil IMEI ou un mouvement autre qu'un inventaire :
    il faut alors l'archiver. La suppression est écrite au journal.
    """
    require_permission(conn, actor_user_id, "delete_product")
    with transaction(conn):
        product = get_product(conn, product_id)
        if product is None:
            raise ValidationError("Produit introuvable.")
        usage = product_usage(conn, product_id)
        if not usage["deletable"]:
            raise ValidationError(
                f"« {product['name']} » ne peut pas être supprimé définitivement : il a déjà "
                + ", ".join(usage["reasons"]) + ". Archivez-le pour le retirer de la liste.")
        stock = stock_of(conn, product_id)
        conn.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        log_audit(conn, "produit_supprime", "products", product_id,
                  f"{product['reference']} — {product['name']} (stock au moment de la suppression : {stock})",
                  at, actor_user_id)


def price_indicators(conn, product_id: int) -> dict:
    """Majoration et marge, nommées clairement (elles ne sont pas la même chose)."""
    p = get_product(conn, product_id)
    return {
        "majoration_pct": markup_percent(p["cost_centimes"], p["price_centimes"]),
        "marge_pct": margin_percent(p["cost_centimes"], p["price_centimes"]),
    }


def stock_report(conn, archived: bool = False) -> list[dict]:
    """Une ligne par produit : stock réel (négatif possible), alerte stock bas, prix et indicateurs."""
    rows = conn.execute("SELECT * FROM products WHERE archived = ? ORDER BY name", (int(archived),)).fetchall()
    report = []
    for p in rows:
        qty = stock_of(conn, p["id"])
        report.append({
            "id": p["id"], "reference": p["reference"], "name": p["name"],
            "barcode": p["barcode"] or "", "category": p["category"] or "", "brand": p["brand"] or "",
            "price_centimes": p["price_centimes"], "cost_centimes": p["cost_centimes"],
            "tax_rate_bp": p["tax_rate_bp"], "price_includes_tax": bool(p["price_includes_tax"]),
            "track_serial": bool(p["track_serial"]),
            "majoration_pct": markup_percent(p["cost_centimes"], p["price_centimes"]),
            "marge_pct": margin_percent(p["cost_centimes"], p["price_centimes"]),
            "stock": qty, "min_stock": p["min_stock"],
            "negatif": qty < 0, "bas": qty <= p["min_stock"],
            "valeur_cout_centimes": max(qty, 0) * p["cost_centimes"],
        })
    return report


def stock_report_page(conn, *, page: int = 0, page_size: int = 50,
                      archived: bool = False, search: str = "",
                      low_stock_only: bool = False,
                      select_product_id: int | None = None) -> dict:
    """Fetch one stock-report page and catalog-wide totals with bounded product rows."""
    if page < 0:
        raise ValueError("page must be non-negative")
    if page_size < 1:
        raise ValueError("page_size must be positive")

    cte = """
        WITH product_stock AS (
            SELECT p.*,
                   COALESCE(SUM(m.quantity), 0) AS stock
            FROM products p
            LEFT JOIN stock_movements m
                ON m.product_id = p.id AND m.warehouse_id = 1
            WHERE p.archived = ?
            GROUP BY p.id
        )
    """
    escaped_search = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = f"%{escaped_search}%"
    filter_sql = """
        (? = '' OR name LIKE ? ESCAPE '!' COLLATE NOCASE
                OR reference LIKE ? ESCAPE '!' COLLATE NOCASE
                OR COALESCE(barcode, '') LIKE ? ESCAPE '!' COLLATE NOCASE)
        AND (? = 0 OR stock <= min_stock)
    """
    filter_args = (search, pattern, pattern, pattern, int(low_stock_only))
    archived_value = int(archived)

    summary = conn.execute(
        cte + """
        SELECT COUNT(*) AS product_count,
               COALESCE(SUM(CASE WHEN stock < 0 THEN 1 ELSE 0 END), 0) AS negative_count,
               COALESCE(SUM(CASE WHEN stock <= min_stock AND stock >= 0
                                 THEN 1 ELSE 0 END), 0) AS low_count,
               COALESCE(SUM(MAX(stock, 0) * cost_centimes), 0) AS stock_value
        FROM product_stock
        """,
        (archived_value,),
    ).fetchone()
    total_count = conn.execute(
        cte + f"SELECT COUNT(*) FROM product_stock WHERE {filter_sql}",
        (archived_value, *filter_args),
    ).fetchone()[0]

    if select_product_id is not None:
        selected = conn.execute(
            cte + "SELECT name, id FROM product_stock WHERE id = ?",
            (archived_value, select_product_id),
        ).fetchone()
        if selected is not None:
            preceding = conn.execute(
                cte + f"""
                SELECT COUNT(*) FROM product_stock
                WHERE {filter_sql} AND (name COLLATE NOCASE < ? COLLATE NOCASE
                    OR (name COLLATE NOCASE = ? COLLATE NOCASE AND id < ?))
                """,
                (archived_value, *filter_args, selected["name"], selected["name"],
                 selected["id"]),
            ).fetchone()[0]
            if preceding < total_count:
                page = preceding // page_size

    page_count = (total_count + page_size - 1) // page_size
    page = min(page, max(page_count - 1, 0))
    rows = conn.execute(
        cte + f"""
        SELECT product_stock.*,
               (SELECT GROUP_CONCAT(s.name, ', ')
                FROM product_suppliers ps
                JOIN suppliers s ON s.id = ps.supplier_id
                WHERE ps.product_id = product_stock.id) AS supplier_names
        FROM product_stock
        WHERE {filter_sql}
        ORDER BY name COLLATE NOCASE, id
        LIMIT ? OFFSET ?
        """,
        (archived_value, *filter_args, page_size, page * page_size),
    ).fetchall()
    report = []
    for product in rows:
        report.append({
            "id": product["id"],
            "reference": product["reference"],
            "name": product["name"],
            "barcode": product["barcode"] or "",
            "category": product["category"] or "",
            "brand": product["brand"] or "",
            "price_centimes": product["price_centimes"],
            "cost_centimes": product["cost_centimes"],
            "tax_rate_bp": product["tax_rate_bp"],
            "price_includes_tax": bool(product["price_includes_tax"]),
            "track_serial": bool(product["track_serial"]),
            "majoration_pct": markup_percent(
                product["cost_centimes"], product["price_centimes"]
            ),
            "marge_pct": margin_percent(
                product["cost_centimes"], product["price_centimes"]
            ),
            "stock": product["stock"],
            "min_stock": product["min_stock"],
            "negatif": product["stock"] < 0,
            "bas": product["stock"] <= product["min_stock"],
            "valeur_cout_centimes": max(product["stock"], 0) * product["cost_centimes"],
            "supplier_names": product["supplier_names"] or "",
        })
    return {
        "rows": report,
        "page": page,
        "page_count": page_count,
        "total_count": total_count,
        "summary": dict(summary),
    }


EDITABLE_FIELDS = ("reference", "name", "barcode", "category", "brand", "cost_centimes", "price_centimes",
                   "price_includes_tax", "tax_rate_bp", "min_stock", "track_serial")


def update_product(conn, product_id: int, at: str | None = None,
                   actor_user_id: int | None = None, **changes) -> list[str]:
    """Corrige la fiche d'un produit. Le stock ne se modifie pas ici (utiliser l'inventaire).

    Chaque changement est écrit au journal (ancienne valeur -> nouvelle valeur).
    Retourne la liste des changements effectués (vide si rien n'a changé).
    """
    require_permission(conn, actor_user_id, "manage_products")
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Champ non modifiable : {', '.join(sorted(unknown))}")
    at = at or now_iso()
    with transaction(conn):
        old = get_product(conn, product_id)
        if old is None:
            raise ValidationError("Produit introuvable.")
        done = []
        for field, value in changes.items():
            if field in ("price_includes_tax", "track_serial"):
                value = int(bool(value))
            if field == "track_serial" and old["track_serial"] != value:
                has_units = conn.execute("SELECT 1 FROM product_serials WHERE product_id = ?", (product_id,)).fetchone()
                if stock_of(conn, product_id) != 0 or has_units:
                    raise ValidationError(
                        "Le suivi par IMEI ne peut changer que lorsque le stock est à 0 et sans appareil enregistré.")
            if field in ("reference", "name"):
                value = (value or "").strip()
                if not value:
                    raise ValidationError("La référence et le nom ne peuvent pas être vides.")
            if field in ("barcode", "category", "brand"):
                value = (value or "").strip() or None
            if field in ("cost_centimes", "price_centimes", "tax_rate_bp", "min_stock") and value < 0:
                raise ValidationError("Cette valeur ne peut pas être négative.")
            if old[field] != value:
                done.append(f"{field}: {old[field]} -> {value}")
                try:
                    conn.execute(f"UPDATE products SET {field} = ? WHERE id = ?", (value, product_id))
                except sqlite3.IntegrityError as exc:
                    raise ValidationError(
                        "Cette référence ou ce code-barres existe déjà pour un autre produit.") from exc
        if done:
            log_audit(conn, "produit_modifie", "products", product_id,
                      " | ".join(done), at, actor_user_id)
    return done

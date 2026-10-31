"""Produits : fiche, prix, indicateurs. Le stock vient des mouvements."""

import sqlite3

from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .money import margin_percent, markup_percent
from .stock import add_movement, stock_of


def create_product(conn, reference: str, name: str, price_centimes: int, *,
                   cost_centimes: int = 0, barcode: str | None = None,
                   category: str | None = None, brand: str | None = None,
                   price_includes_tax: bool = True, tax_rate_bp: int = 1900,
                   min_stock: int = 0, track_serial: bool = False,
                   initial_stock: int = 0, at: str | None = None) -> int:
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
        log_audit(conn, "produit_cree", "products", product_id, reference, at)
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
    row = conn.execute("SELECT * FROM products WHERE barcode = ? OR reference = ?", (text, text)).fetchone()
    if row:
        return [row]
    return conn.execute(
        "SELECT * FROM products WHERE archived = 0 AND name LIKE ? ORDER BY name", (f"%{text}%",)
    ).fetchall()


def archive_product(conn, product_id: int, at: str | None = None) -> None:
    """Retire un produit de la liste sans rien effacer : son historique est conservé."""
    with transaction(conn):
        product = get_product(conn, product_id)
        if product is None:
            raise ValidationError("Produit introuvable.")
        conn.execute("UPDATE products SET archived = 1 WHERE id = ?", (product_id,))
        log_audit(conn, "produit_archive", "products", product_id, f"{product['reference']} — {product['name']}", at)


def restore_product(conn, product_id: int, at: str | None = None) -> None:
    """Remet un produit archivé dans la liste."""
    with transaction(conn):
        product = get_product(conn, product_id)
        if product is None:
            raise ValidationError("Produit introuvable.")
        conn.execute("UPDATE products SET archived = 0 WHERE id = ?", (product_id,))
        log_audit(conn, "produit_restaure", "products", product_id, f"{product['reference']} — {product['name']}", at)


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


def delete_product(conn, product_id: int, at: str | None = None) -> None:
    """Supprime pour de bon un produit SANS historique (créé par erreur, essai...).

    Refusé dès qu'il y a une vente, un appareil IMEI ou un mouvement autre qu'un inventaire :
    il faut alors l'archiver. La suppression est écrite au journal.
    """
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
                  f"{product['reference']} — {product['name']} (stock au moment de la suppression : {stock})", at)


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


EDITABLE_FIELDS = ("reference", "name", "barcode", "category", "brand", "cost_centimes", "price_centimes",
                   "price_includes_tax", "tax_rate_bp", "min_stock", "track_serial")


def update_product(conn, product_id: int, at: str | None = None, **changes) -> list[str]:
    """Corrige la fiche d'un produit. Le stock ne se modifie pas ici (utiliser l'inventaire).

    Chaque changement est écrit au journal (ancienne valeur -> nouvelle valeur).
    Retourne la liste des changements effectués (vide si rien n'a changé).
    """
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
            log_audit(conn, "produit_modifie", "products", product_id, " | ".join(done), at)
    return done

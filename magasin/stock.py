"""Stock = somme des mouvements. Jamais un nombre modifié à la main."""

from .clock import now_iso
from .db import transaction
from .errors import ValidationError

MOVEMENT_TYPES = (
    "reception_achat", "vente", "retour_client", "retour_fournisseur",
    "ajustement", "inventaire", "transfert",
)

DEFAULT_WAREHOUSE = 1


def stock_of(conn, product_id: int, warehouse_id: int = DEFAULT_WAREHOUSE) -> int:
    """Stock actuel (peut être négatif : il est alors affiché tel quel)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS total FROM stock_movements "
        "WHERE product_id = ? AND warehouse_id = ?",
        (product_id, warehouse_id),
    ).fetchone()
    return row["total"]


def add_movement(conn, product_id: int, quantity: int, movement_type: str, *,
                 warehouse_id: int = DEFAULT_WAREHOUSE, lot: str | None = None,
                 ref_type: str | None = None, ref_id: int | None = None,
                 ref_number: str | None = None, counterparty: str | None = None,
                 note: str | None = None, at: str | None = None) -> int:
    """Ajoute un mouvement avec quantité avant / après. Retourne son identifiant."""
    if quantity == 0:
        raise ValidationError("Un mouvement de stock ne peut pas avoir une quantité de 0.")
    if movement_type not in MOVEMENT_TYPES:
        raise ValidationError(f"Type de mouvement inconnu : {movement_type}")
    with transaction(conn):
        before = stock_of(conn, product_id, warehouse_id)
        cursor = conn.execute(
            "INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, "
            "qty_before, qty_after, lot, ref_type, ref_id, ref_number, counterparty, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (product_id, warehouse_id, movement_type, quantity, before, before + quantity,
             lot, ref_type, ref_id, ref_number, counterparty, note, at or now_iso()),
        )
        return cursor.lastrowid


def list_movements(conn, product_id: int | None = None):
    sql = ("SELECT m.*, p.name AS product_name FROM stock_movements m "
           "JOIN products p ON p.id = m.product_id")
    args: tuple = ()
    if product_id is not None:
        sql += " WHERE m.product_id = ?"
        args = (product_id,)
    return conn.execute(sql + " ORDER BY m.id", args).fetchall()


def inventory_count(conn, product_id: int, counted: int, note: str | None = None,
                    warehouse_id: int = DEFAULT_WAREHOUSE, at: str | None = None):
    """Inventaire : on saisit la quantité comptée, le logiciel crée le mouvement de l'écart.

    Retourne l'identifiant du mouvement, ou None si le stock était déjà exact.
    """
    if counted < 0:
        raise ValidationError("La quantité comptée ne peut pas être négative.")
    tracked = conn.execute("SELECT track_serial, name FROM products WHERE id = ?", (product_id,)).fetchone()
    if tracked and tracked["track_serial"]:
        raise ValidationError(
            f"« {tracked['name']} » est suivi par IMEI : ajoutez ou sortez les appareils un par un "
            "(bouton « Appareils (IMEI) »), le stock se met à jour tout seul.")
    difference = counted - stock_of(conn, product_id, warehouse_id)
    if difference == 0:
        return None
    return add_movement(conn, product_id, difference, "inventaire", warehouse_id=warehouse_id,
                        note=(note or "").strip() or "Inventaire", at=at)

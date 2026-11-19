"""Suivi par IMEI : chaque smartphone a un IMEI1 et, s'il a deux SIM, un IMEI2.

Règles :
- un IMEI a 15 chiffres et un chiffre de contrôle (algorithme de Luhn) : une faute de frappe est détectée ;
- un IMEI n'existe qu'une seule fois dans la base (en IMEI1 comme en IMEI2) ;
- un produit suivi par IMEI n'augmente et ne diminue que appareil par appareil ;
- le stock d'un tel produit est toujours égal au nombre d'appareils « en_stock ».
"""

import re

from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .users import require_permission
from .stock import add_movement

STATUSES = ("en_stock", "vendu", "retourne", "sorti")
STATUS_LABELS = {"en_stock": "En stock", "vendu": "Vendu", "retourne": "Retourné", "sorti": "Sorti du stock"}


# ---------------------------------------------------------------- contrôle du numéro
def luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        n = int(char)
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def complete_imei(first_14_digits: str) -> str:
    """Ajoute le chiffre de contrôle à 14 chiffres (sert aux données de démonstration et aux tests)."""
    if not re.fullmatch(r"\d{14}", first_14_digits):
        raise ValidationError("Il faut exactement 14 chiffres.")
    for check in "0123456789":
        if luhn_ok(first_14_digits + check):
            return first_14_digits + check
    raise AssertionError("inatteignable")


def clean_imei(text: str | None) -> str:
    """Enlève espaces, tirets et points (un scanner ou un copier-coller en ajoute parfois)."""
    return re.sub(r"[\s.\-/]", "", text or "")


def validate_imei(text: str | None, label: str = "IMEI") -> str:
    imei = clean_imei(text)
    if not imei:
        raise ValidationError(f"{label} : saisie vide.")
    if not imei.isdigit():
        raise ValidationError(f"{label} : seuls les chiffres sont acceptés ({text!r}).")
    if len(imei) != 15:
        raise ValidationError(f"{label} : un IMEI a 15 chiffres, celui-ci en a {len(imei)} ({imei}).")
    if not luhn_ok(imei):
        raise ValidationError(f"{label} : le chiffre de contrôle ne correspond pas ({imei}). "
                              "Vérifiez la saisie ou rescannez l'étiquette.")
    return imei


# -------------------------------------------------------------------------- lecture
def get_unit(conn, imei: str):
    """Retrouve un appareil par son IMEI1 ou son IMEI2."""
    imei = clean_imei(imei)
    return conn.execute("SELECT * FROM product_serials WHERE imei1 = ? OR imei2 = ?", (imei, imei)).fetchone()


def find_units(conn, text: str, limit: int = 50) -> list:
    """Recherche partielle (au moins 4 chiffres) dans les IMEI1 et IMEI2."""
    text = clean_imei(text)
    if len(text) < 4:
        raise ValidationError("Saisissez au moins 4 chiffres pour chercher un IMEI.")
    return conn.execute(
        "SELECT s.*, p.name AS product_name FROM product_serials s JOIN products p ON p.id = s.product_id "
        "WHERE s.imei1 LIKE ? OR s.imei2 LIKE ? ORDER BY s.id DESC LIMIT ?",
        (f"%{text}%", f"%{text}%", limit)).fetchall()


def units_of(conn, product_id: int, status: str | None = None) -> list:
    sql = "SELECT * FROM product_serials WHERE product_id = ?"
    args: list = [product_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def count_in_stock(conn, product_id: int) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM product_serials WHERE product_id = ? AND status = 'en_stock'",
                        (product_id,)).fetchone()["n"]


def trace(conn, imei: str) -> dict | None:
    """Historique d'un appareil : produit, état, entrée, vente et client (utile pour le service après-vente)."""
    unit = get_unit(conn, imei)
    if unit is None:
        return None
    product = conn.execute("SELECT name, reference FROM products WHERE id = ?", (unit["product_id"],)).fetchone()
    result = {"imei1": unit["imei1"], "imei2": unit["imei2"], "status": unit["status"],
              "product_name": product["name"], "product_reference": product["reference"],
              "entered_at": unit["created_at"], "sold_at": unit["sold_at"], "sale_number": None,
              "customer_name": None, "customer_phone": None, "note": unit["note"]}
    if unit["sale_id"]:
        sale = conn.execute(
            "SELECT s.number, c.name, c.phone FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
            "WHERE s.id = ?", (unit["sale_id"],)).fetchone()
        result.update(sale_number=sale["number"], customer_name=sale["name"], customer_phone=sale["phone"])
    return result


# ------------------------------------------------------------------------ écriture
def _require_tracked(conn, product_id: int):
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None or product["archived"]:
        raise ValidationError("Produit introuvable ou archivé.")
    if not product["track_serial"]:
        raise ValidationError(f"« {product['name']} » n'est pas suivi par IMEI. "
                              "Activez « Suivi par IMEI » dans la fiche du produit.")
    return product


def add_units(conn, product_id: int, units: list[tuple[str, str | None]], *, movement_type: str = "inventaire",
              ref_type: str | None = None, ref_id: int | None = None, ref_number: str | None = None,
              lot: str | None = None, counterparty: str | None = None,
              note: str | None = None, warehouse_id: int = 1,
              at: str | None = None, actor_user_id: int | None = None) -> list[int]:
    """Entre des smartphones en stock : (imei1, imei2 ou None) par appareil. Tout ou rien."""
    require_permission(conn, actor_user_id, "inventory")
    if not units:
        raise ValidationError("Aucun appareil à enregistrer.")
    at = at or now_iso()
    with transaction(conn):
        product = _require_tracked(conn, product_id)
        seen: set[str] = set()
        cleaned = []
        for position, (raw1, raw2) in enumerate(units, start=1):
            imei1 = validate_imei(raw1, f"Appareil {position} — IMEI1")
            imei2 = validate_imei(raw2, f"Appareil {position} — IMEI2") if clean_imei(raw2) else None
            if imei2 == imei1:
                raise ValidationError(f"Appareil {position} : l'IMEI2 ne peut pas être identique à l'IMEI1.")
            for imei in filter(None, (imei1, imei2)):
                if imei in seen:
                    raise ValidationError(f"L'IMEI {imei} est saisi deux fois dans cette liste.")
                seen.add(imei)
                existing = get_unit(conn, imei)
                if existing:
                    raise ValidationError(
                        f"L'IMEI {imei} existe déjà dans le logiciel "
                        f"(état : {STATUS_LABELS[existing['status']].lower()}).")
            cleaned.append((imei1, imei2))
        ids = []
        for imei1, imei2 in cleaned:
            movement_id = add_movement(conn, product_id, 1, movement_type, lot=lot, ref_type=ref_type,
                                       ref_id=ref_id, ref_number=ref_number, counterparty=counterparty,
                                       note=note or f"IMEI {imei1}",
                                       warehouse_id=warehouse_id, at=at)
            cursor = conn.execute(
                "INSERT INTO product_serials (product_id, imei1, imei2, status, entry_movement_id, note, created_at) "
                "VALUES (?,?,?, 'en_stock', ?,?,?)", (product_id, imei1, imei2, movement_id, note, at))
            ids.append(cursor.lastrowid)
        log_audit(conn, "imei_entree", "products", product_id,
                  f"{len(ids)} appareil(s) — {product['name']}", at, actor_user_id)
    return ids


def remove_unit(conn, serial_id: int, reason: str, at: str | None = None,
                actor_user_id: int | None = None) -> None:
    """Sort un appareil du stock (perdu, défectueux, erreur de saisie...). Une raison est obligatoire."""
    require_permission(conn, actor_user_id, "inventory")
    if not (reason or "").strip():
        raise ValidationError("Une raison est obligatoire pour sortir un appareil du stock.")
    at = at or now_iso()
    with transaction(conn):
        unit = conn.execute("SELECT * FROM product_serials WHERE id = ?", (serial_id,)).fetchone()
        if unit is None:
            raise ValidationError("Appareil introuvable.")
        if unit["status"] != "en_stock":
            raise ValidationError("Seul un appareil en stock peut être sorti du stock.")
        add_movement(conn, unit["product_id"], -1, "ajustement", note=f"IMEI {unit['imei1']} : {reason.strip()}", at=at)
        conn.execute("UPDATE product_serials SET status = 'sorti', note = ? WHERE id = ?", (reason.strip(), serial_id))
        log_audit(conn, "imei_sortie", "product_serials", serial_id,
                  reason.strip(), at, actor_user_id)


def mark_sold(conn, serial_ids: list[int], sale_id: int, at: str) -> None:
    """Appelé par la vente : les appareils scannés passent à « vendu » et sont reliés à la vente."""
    for serial_id in serial_ids:
        conn.execute("UPDATE product_serials SET status = 'vendu', sale_id = ?, sold_at = ? WHERE id = ?",
                     (sale_id, at, serial_id))


def resolve_for_sale(conn, product_id: int, imeis: list[str], quantity: int) -> list[int]:
    """Vérifie les IMEI scannés pour une ligne de vente et renvoie les identifiants des appareils."""
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if len(imeis) != quantity:
        raise ValidationError(
            f"« {product['name']} » : {quantity} appareil(s) à vendre, {len(imeis)} IMEI scanné(s). "
            "Scannez l'IMEI de chaque téléphone vendu.")
    ids = []
    for raw in imeis:
        imei = validate_imei(raw, "IMEI de l'appareil vendu")
        unit = get_unit(conn, imei)
        if unit is None:
            raise ValidationError(f"L'IMEI {imei} n'est pas dans le stock du logiciel.")
        if unit["product_id"] != product_id:
            other = conn.execute("SELECT name FROM products WHERE id = ?", (unit["product_id"],)).fetchone()["name"]
            raise ValidationError(f"L'IMEI {imei} appartient à « {other} », pas à « {product['name']} ».")
        if unit["status"] != "en_stock":
            raise ValidationError(f"L'IMEI {imei} n'est pas disponible (état : {STATUS_LABELS[unit['status']].lower()}).")
        if unit["id"] in ids:
            raise ValidationError(f"L'IMEI {imei} est scanné deux fois.")
        ids.append(unit["id"])
    return ids


_DETAIL_SQL = (
    "SELECT s.*, p.name AS product_name, sa.number AS sale_number, c.name AS customer_name, "
    "c.phone AS customer_phone FROM product_serials s JOIN products p ON p.id = s.product_id "
    "LEFT JOIN sales sa ON sa.id = s.sale_id LEFT JOIN customers c ON c.id = sa.customer_id "
)


def list_units_detailed(conn, product_id: int, status: str | None = None) -> list[dict]:
    """Appareils d'un produit avec le numéro de vente et le client (si vendu)."""
    sql = _DETAIL_SQL + "WHERE s.product_id = ?"
    args: list = [product_id]
    if status:
        sql += " AND s.status = ?"
        args.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY s.id DESC", args)]


def search_detailed(conn, text: str, limit: int = 50) -> list[dict]:
    """Recherche par IMEI (complet ou au moins 4 chiffres), avec produit, vente et client."""
    text = clean_imei(text)
    if len(text) < 4:
        raise ValidationError("Saisissez au moins 4 chiffres pour chercher un IMEI.")
    rows = conn.execute(_DETAIL_SQL + "WHERE s.imei1 LIKE ? OR s.imei2 LIKE ? ORDER BY s.id DESC LIMIT ?",
                        (f"%{text}%", f"%{text}%", limit))
    return [dict(r) for r in rows]


def parse_imei_list(text: str) -> list[tuple[str, str | None]]:
    """Lit une liste collée (depuis Excel, un message...) : une ligne par téléphone, IMEI1 puis IMEI2.

    Les numéros peuvent être séparés par un espace, une tabulation, une virgule ou un point-virgule.
    Tout ou rien : la première ligne invalide arrête la lecture avec son numéro de ligne.
    """
    units = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        tokens = [t for t in re.split(r"[\s,;]+", line.strip()) if t]
        if len(tokens) > 2:
            raise ValidationError(f"Ligne {number} : {len(tokens)} numéros trouvés, il en faut au plus 2 "
                                  "(IMEI1 puis IMEI2, chacun sans espace).")
        imei1 = validate_imei(tokens[0], f"Ligne {number} — IMEI1")
        imei2 = validate_imei(tokens[1], f"Ligne {number} — IMEI2") if len(tokens) == 2 else None
        units.append((imei1, imei2))
    if not units:
        raise ValidationError("Aucune ligne à ajouter : collez un IMEI1 (et un IMEI2) par ligne.")
    return units

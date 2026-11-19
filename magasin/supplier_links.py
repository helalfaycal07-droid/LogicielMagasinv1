"""Association non destructive des fournisseurs aux produits depuis SUIVI_STOCK."""

from dataclasses import dataclass
from pathlib import Path

from .clock import now_iso
from .db import log_audit, transaction
from .errors import ValidationError
from .import_excel import SHEET_NAME, _normalize_header, _open_workbook, _text
from .users import require_permission

REFERENCE_HEADER = "code produit"
SUPPLIER_HEADER = "fournisseur"


@dataclass(frozen=True)
class SupplierLinkRow:
    source_row: int
    reference: str
    supplier: str
    status: str
    issue: str = ""
    product_id: int | None = None
    supplier_id: int | None = None

    @property
    def eligible(self) -> bool:
        return self.status == "À associer"


@dataclass(frozen=True)
class SupplierLinkPreview:
    source_path: Path
    rows: tuple[SupplierLinkRow, ...]

    @property
    def linkable_rows(self) -> tuple[SupplierLinkRow, ...]:
        return tuple(row for row in self.rows if row.eligible)

    def count(self, status: str) -> int:
        return sum(row.status == status for row in self.rows)


def preview_supplier_links(conn, workbook_path: str | Path) -> SupplierLinkPreview:
    """Inspect supplier/reference pairs without writing to the database."""
    path = Path(workbook_path)
    if not path.is_file():
        raise ValidationError("Le fichier Excel sélectionné est introuvable.")

    workbook = _open_workbook(path)
    try:
        if SHEET_NAME not in workbook.sheetnames:
            raise ValidationError(f"La feuille « {SHEET_NAME} » est absente du classeur.")

        sheet = workbook[SHEET_NAME]
        header_limit = min(max(sheet.max_row or 1, 1), 30)
        header_row = None
        columns: dict[str, int] = {}
        for row_number, values in enumerate(
            sheet.iter_rows(min_row=1, max_row=header_limit, values_only=True), start=1
        ):
            normalized = {
                _normalize_header(value): index for index, value in enumerate(values)
            }
            if REFERENCE_HEADER in normalized and SUPPLIER_HEADER in normalized:
                header_row = row_number
                columns = {
                    "reference": normalized[REFERENCE_HEADER],
                    "supplier": normalized[SUPPLIER_HEADER],
                }
                break
        if header_row is None:
            raise ValidationError(
                "Les colonnes « CODE PRODUIT » et « FOURNISSEUR » sont nécessaires "
                "dans les 30 premières lignes."
            )

        products = {
            row["reference"]: (row["id"], row["archived"])
            for row in conn.execute("SELECT id, reference, archived FROM products")
        }
        associations = {
            (row["product_id"], row["supplier_id"])
            for row in conn.execute(
                "SELECT product_id, supplier_id FROM product_suppliers"
            )
        }

        rows: list[SupplierLinkRow] = []
        encountered: set[tuple[str, str]] = set()
        for source_row, values in enumerate(
            sheet.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            reference = _text(values[columns["reference"]]) \
                if columns["reference"] < len(values) else ""
            supplier_name = _text(values[columns["supplier"]]) \
                if columns["supplier"] < len(values) else ""
            if not reference and not supplier_name:
                continue
            if not reference or not supplier_name:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "À vérifier",
                    "Référence ou fournisseur manquant.",
                ))
                continue

            supplier_key = supplier_name.casefold()
            pair = (reference, supplier_key)
            if pair in encountered:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Doublon du fichier",
                    "Cette association apparaît déjà plus haut dans la feuille.",
                ))
                continue
            encountered.add(pair)

            product = products.get(reference)
            if product is None:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Référence inconnue",
                    "Aucun produit ne possède exactement cette référence.",
                ))
                continue
            product_id, archived = product
            if archived:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Produit archivé",
                    "Un produit archivé ne peut pas recevoir de nouvelle association.",
                    product_id=product_id,
                ))
                continue
            supplier = conn.execute(
                "SELECT id, active FROM suppliers WHERE name = ? COLLATE NOCASE",
                (supplier_name,),
            ).fetchone()
            if supplier is None:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Fournisseur inconnu",
                    "Créez d’abord sa fiche dans l’écran Fournisseurs.",
                    product_id=product_id,
                ))
                continue
            supplier_id = supplier["id"]
            if not supplier["active"]:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Fournisseur archivé",
                    "Réactivez sa fiche dans l’écran Fournisseurs avant l’association.",
                    product_id=product_id,
                    supplier_id=supplier_id,
                ))
                continue
            if (product_id, supplier_id) in associations:
                rows.append(SupplierLinkRow(
                    source_row, reference, supplier_name, "Déjà associé",
                    "Cette association existe déjà et ne sera pas modifiée.",
                    product_id=product_id,
                    supplier_id=supplier_id,
                ))
                continue
            rows.append(SupplierLinkRow(
                source_row, reference, supplier_name, "À associer",
                product_id=product_id,
                supplier_id=supplier_id,
            ))

        return SupplierLinkPreview(path, tuple(rows))
    finally:
        workbook.close()


def apply_supplier_links(conn, preview: SupplierLinkPreview, *,
                         actor_user_id: int | None = None,
                         at: str | None = None) -> int:
    """Apply only product/supplier links; reject stale previews atomically."""
    require_permission(conn, actor_user_id, "purchases")
    candidates = preview.linkable_rows
    if not candidates:
        raise ValidationError("Aucune association nouvelle et valide à enregistrer.")

    at = at or now_iso()
    with transaction(conn):
        resolved: list[tuple[int, int]] = []
        for row in candidates:
            product = conn.execute(
                "SELECT id, archived FROM products WHERE reference = ?",
                (row.reference,),
            ).fetchone()
            supplier = conn.execute(
                "SELECT id, active FROM suppliers WHERE name = ? COLLATE NOCASE",
                (row.supplier,),
            ).fetchone()
            if (
                product is None or product["archived"] or supplier is None
                or not supplier["active"]
                or product["id"] != row.product_id
                or supplier["id"] != row.supplier_id
                or conn.execute(
                    "SELECT 1 FROM product_suppliers "
                    "WHERE product_id = ? AND supplier_id = ?",
                    (row.product_id, row.supplier_id),
                ).fetchone() is not None
            ):
                raise ValidationError(
                    "Le catalogue ou les fiches fournisseurs ont changé depuis l’aperçu. "
                    "Recommencez pour vérifier les associations."
                )
            resolved.append((product["id"], supplier["id"]))

        conn.executemany(
            "INSERT INTO product_suppliers (product_id, supplier_id, created_at) "
            "VALUES (?, ?, ?)",
            ((product_id, supplier_id, at) for product_id, supplier_id in resolved),
        )
        log_audit(
            conn,
            "fournisseurs_associes_produits",
            "product_suppliers",
            details=f"{preview.source_path.name} — {len(resolved)} association(s)",
            at=at,
            actor_user_id=actor_user_id,
        )
    return len(resolved)


def product_supplier_names(conn) -> dict[int, list[str]]:
    """Return associated supplier names grouped by product for catalogue display."""
    grouped: dict[int, list[str]] = {}
    for row in conn.execute(
        "SELECT ps.product_id, s.name FROM product_suppliers ps "
        "JOIN suppliers s ON s.id = ps.supplier_id "
        "ORDER BY s.name COLLATE NOCASE"
    ):
        grouped.setdefault(row["product_id"], []).append(row["name"])
    return grouped


def product_suppliers(conn, product_id: int) -> list[dict]:
    """Return all supplier choices currently associated with one product."""
    return [dict(row) for row in conn.execute(
        "SELECT s.id, s.name, s.active FROM product_suppliers ps "
        "JOIN suppliers s ON s.id = ps.supplier_id "
        "WHERE ps.product_id = ? ORDER BY s.name COLLATE NOCASE",
        (product_id,),
    )]


def set_product_supplier(conn, product_id: int, supplier_id: int | None, *,
                         actor_user_id: int | None = None,
                         at: str | None = None) -> None:
    """Replace links with one supplier, clear with ID 0, or preserve when ID is None."""
    if supplier_id is None:
        return
    require_permission(conn, actor_user_id, "purchases")
    if supplier_id < 0:
        raise ValidationError("Choisissez une fiche fournisseur valide.")
    at = at or now_iso()
    with transaction(conn):
        product = conn.execute(
            "SELECT reference, archived FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None or product["archived"]:
            raise ValidationError("Produit introuvable ou archivé.")

        current_ids = {
            row["supplier_id"] for row in conn.execute(
                "SELECT supplier_id FROM product_suppliers WHERE product_id = ?",
                (product_id,),
            )
        }
        selected_ids = set()
        supplier_name = None
        if supplier_id:
            supplier = conn.execute(
                "SELECT id, name, active FROM suppliers WHERE id = ?", (supplier_id,)
            ).fetchone()
            if supplier is None:
                raise ValidationError("Fournisseur introuvable.")
            if not supplier["active"] and current_ids != {supplier_id}:
                raise ValidationError(
                    "Un fournisseur archivé ne peut pas être associé à un produit."
                )
            selected_ids.add(supplier_id)
            supplier_name = supplier["name"]

        if current_ids == selected_ids:
            return

        conn.execute("DELETE FROM product_suppliers WHERE product_id = ?", (product_id,))
        if supplier_id:
            conn.execute(
                "INSERT INTO product_suppliers (product_id, supplier_id, created_at) "
                "VALUES (?, ?, ?)",
                (product_id, supplier_id, at),
            )
        log_audit(
            conn,
            "fournisseur_produit_modifie",
            "products",
            product_id,
            f"{product['reference']} — fournisseur : {supplier_name or 'aucun'}",
            at,
            actor_user_id,
        )

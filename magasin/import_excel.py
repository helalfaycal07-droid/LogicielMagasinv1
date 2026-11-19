"""Import products and opening stock from the SUIVI_STOCK Excel worksheet."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zipfile import BadZipFile

from .db import log_audit, transaction
from .errors import ValidationError
from .products import create_product

SHEET_NAME = "SUIVI_STOCK"
REQUIRED_HEADERS = {
    "reference": "code produit",
    "name": "designation",
    "category": "categorie",
    "cost": "prix achat da",
    "price": "prix vente da",
    "stock": "stock restant",
    "min_stock": "seuil alerte",
}


@dataclass(frozen=True)
class StockImportRow:
    source_row: int
    reference: str
    name: str
    category: str | None
    cost_centimes: int | None
    price_centimes: int | None
    stock: int | None
    min_stock: int | None
    status: str
    issue: str = ""

    @property
    def eligible(self) -> bool:
        return self.status == "À importer"


@dataclass(frozen=True)
class StockImportPreview:
    source_path: Path
    rows: tuple[StockImportRow, ...]
    tax_rate_bp: int

    @property
    def importable_rows(self) -> tuple[StockImportRow, ...]:
        return tuple(row for row in self.rows if row.eligible)

    def count(self, status: str) -> int:
        return sum(row.status == status for row in self.rows)


def _normalize_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _decimal(value, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{label} manquant ou invalide.")
    text = str(value).strip()
    for blank in (" ", "\u00a0", "\u202f"):
        text = text.replace(blank, "")
    text = text.replace("DA", "").replace("da", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"{label} invalide : {value!r}.") from exc
    if not result.is_finite():
        raise ValidationError(f"{label} invalide : {value!r}.")
    return result


def _money_centimes(value, label: str) -> int:
    amount = _decimal(value, label)
    if amount < 0:
        raise ValidationError(f"{label} ne peut pas être négatif.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _quantity(value, label: str, *, blank_is_zero: bool = False) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        if blank_is_zero:
            return 0
        raise ValidationError(f"{label} manquant.")
    amount = _decimal(value, label)
    if amount != amount.to_integral_value():
        raise ValidationError(f"{label} doit être un nombre entier.")
    return int(amount)


def _open_workbook(path: Path):
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:
        raise ValidationError(
            "La lecture Excel nécessite openpyxl. Installez les dépendances du logiciel "
            "avec « python -m pip install -r requirements.txt »."
        ) from exc

    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValidationError("Choisissez un fichier Excel .xlsx ou .xlsm.")
    try:
        return load_workbook(
            path, read_only=True, data_only=True, keep_links=False, keep_vba=False
        )
    except (OSError, BadZipFile, InvalidFileException) as exc:
        raise ValidationError(f"Le fichier Excel ne peut pas être lu : {exc}") from exc


def _setting_tax_rate(conn) -> int:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'default_tax_rate_bp'"
    ).fetchone()
    try:
        rate = int(row["value"]) if row else -1
    except (TypeError, ValueError) as exc:
        raise ValidationError("Le taux de TVA par défaut du logiciel est invalide.") from exc
    if rate < 0:
        raise ValidationError("Le taux de TVA par défaut du logiciel est absent ou invalide.")
    return rate


def preview_stock_workbook(conn, workbook_path: str | Path) -> StockImportPreview:
    """Read the selected worksheet without executing macros or saving the workbook."""
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
        column_indexes = {}
        for row_number, values in enumerate(
            sheet.iter_rows(min_row=1, max_row=header_limit, values_only=True), start=1
        ):
            normalized = {_normalize_header(value): index for index, value in enumerate(values)}
            if all(header in normalized for header in REQUIRED_HEADERS.values()):
                header_row = row_number
                column_indexes = {
                    key: normalized[header] for key, header in REQUIRED_HEADERS.items()
                }
                break
        if header_row is None:
            expected = ", ".join(REQUIRED_HEADERS.values())
            raise ValidationError(
                f"Les colonnes nécessaires sont introuvables dans les 30 premières lignes : {expected}."
            )

        known_references = {
            str(row["reference"]).casefold()
            for row in conn.execute("SELECT reference FROM products").fetchall()
        }
        encountered: set[str] = set()
        rows: list[StockImportRow] = []
        for source_row, values in enumerate(
            sheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1
        ):
            if not any(value is not None and str(value).strip() for value in values):
                continue

            def value_for(key: str):
                index = column_indexes[key]
                return values[index] if index < len(values) else None

            reference = _text(value_for("reference"))
            name = _text(value_for("name"))
            category = _text(value_for("category")) or None
            if not reference:
                rows.append(StockImportRow(
                    source_row, "", name, category, None, None, None, None,
                    "À vérifier", "Référence manquante.",
                ))
                continue

            normalized_reference = reference.casefold()
            if normalized_reference in encountered:
                rows.append(StockImportRow(
                    source_row, reference, name, category, None, None, None, None,
                    "Doublon du fichier", "Une première ligne porte déjà cette référence.",
                ))
                continue
            encountered.add(normalized_reference)
            if normalized_reference in known_references:
                rows.append(StockImportRow(
                    source_row, reference, name, category, None, None, None, None,
                    "Déjà dans le catalogue",
                    "La fiche existante ne sera ni modifiée ni réapprovisionnée.",
                ))
                continue

            try:
                if not name:
                    raise ValidationError("Désignation manquante.")
                cost = _money_centimes(value_for("cost"), "Prix d'achat")
                price = _money_centimes(value_for("price"), "Prix de vente")
                stock = _quantity(value_for("stock"), "Stock restant")
                min_stock = _quantity(
                    value_for("min_stock"), "Seuil d'alerte", blank_is_zero=True
                )
                if min_stock < 0:
                    raise ValidationError("Le seuil d'alerte ne peut pas être négatif.")
            except ValidationError as exc:
                rows.append(StockImportRow(
                    source_row, reference, name, category, None, None, None, None,
                    "À vérifier", str(exc),
                ))
                continue
            rows.append(StockImportRow(
                source_row, reference, name, category, cost, price, stock, min_stock,
                "À importer",
            ))
        return StockImportPreview(path, tuple(rows), _setting_tax_rate(conn))
    finally:
        workbook.close()


def import_stock_workbook(conn, preview: StockImportPreview, *,
                          actor_user_id: int | None = None) -> int:
    """Create all eligible products and opening-stock movements atomically."""
    candidates = preview.importable_rows
    if not candidates:
        raise ValidationError("Aucune ligne valide et nouvelle n'est prête à être importée.")

    with transaction(conn):
        current_references = {
            str(row["reference"]).casefold()
            for row in conn.execute("SELECT reference FROM products").fetchall()
        }
        if any(row.reference.casefold() in current_references for row in candidates):
            raise ValidationError(
                "Le catalogue a changé depuis l'aperçu. Recommencez l'import pour vérifier les références."
            )
        tax_rate_bp = _setting_tax_rate(conn)
        for row in candidates:
            create_product(
                conn,
                row.reference,
                row.name,
                row.price_centimes,
                cost_centimes=row.cost_centimes,
                category=row.category,
                price_includes_tax=True,
                tax_rate_bp=tax_rate_bp,
                min_stock=row.min_stock,
                initial_stock=row.stock,
                actor_user_id=actor_user_id,
            )
        log_audit(
            conn,
            "import_excel_stock",
            "products",
            details=(
                f"{preview.source_path.name} — feuille {SHEET_NAME} — "
                f"{len(candidates)} produit(s), stock restant utilisé comme stock de départ"
            ),
            actor_user_id=actor_user_id,
        )
    return len(candidates)

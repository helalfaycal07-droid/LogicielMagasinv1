"""Lecture des formulaires : transforme des textes saisis en valeurs exactes, avec des messages clairs."""

from decimal import Decimal, InvalidOperation

from .errors import ValidationError
from .money import da_to_centimes


def parse_int(text: str, label: str, minimum: int = 0) -> int:
    text = (text or "").strip().replace(" ", "")
    if text == "":
        return minimum if minimum >= 0 else 0
    try:
        value = int(text)
    except ValueError as exc:
        raise ValidationError(f"{label} : saisissez un nombre entier (par exemple 3).") from exc
    if value < minimum:
        raise ValidationError(f"{label} ne peut pas être inférieur à {minimum}.")
    return value


def parse_product_form(*, reference: str, name: str, price_text: str, cost_text: str = "",
                       barcode: str = "", category: str = "", brand: str = "",
                       tax_percent_text: str = "19", min_stock_text: str = "0",
                       initial_stock_text: str = "0", price_includes_tax: bool = True,
                       track_serial: bool = False) -> dict:
    """Retourne les arguments de products.create_product, ou lève une ValidationError lisible."""
    if not reference.strip():
        raise ValidationError("La référence est obligatoire (par exemple 101).")
    if not name.strip():
        raise ValidationError("Le nom du produit est obligatoire.")
    if not price_text.strip():
        raise ValidationError("Le prix de vente est obligatoire.")
    price = da_to_centimes(price_text)
    cost = da_to_centimes(cost_text) if cost_text.strip() else 0
    if price < 0 or cost < 0:
        raise ValidationError("Un prix ne peut pas être négatif.")
    try:
        rate_bp = int((Decimal((tax_percent_text or "0").replace(",", ".").strip()) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("TVA : saisissez un pourcentage (par exemple 19).") from exc
    if rate_bp < 0:
        raise ValidationError("La TVA ne peut pas être négative.")
    initial_stock = parse_int(initial_stock_text, "Stock initial")
    if track_serial and initial_stock:
        raise ValidationError("Un produit suivi par IMEI n'a pas de stock initial : ajoutez ensuite les "
                              "appareils avec le bouton « Appareils (IMEI) ».")
    return {
        "reference": reference.strip(), "name": name.strip(), "price_centimes": price,
        "cost_centimes": cost, "barcode": barcode.strip() or None,
        "category": category.strip() or None, "brand": brand.strip() or None,
        "tax_rate_bp": rate_bp, "price_includes_tax": price_includes_tax,
        "min_stock": parse_int(min_stock_text, "Stock minimum"),
        "initial_stock": initial_stock, "track_serial": track_serial,
    }

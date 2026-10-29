"""Montants exacts : tout est en centimes entiers (1 DA = 100 centimes)."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import ValidationError


def da_to_centimes(value) -> int:
    """'38 000,00' ou 38000 ou '38000.5' -> centimes (arrondi au centime)."""
    text = str(value).strip()
    for blank in (" ", "\u00a0", "\u202f"):
        text = text.replace(blank, "")
    text = text.replace("DA", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"Montant invalide : {value!r}") from exc
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_da(centimes: int) -> str:
    """3900000 -> '39 000,00 DA' (espace pour les milliers, virgule décimale)."""
    sign = "-" if centimes < 0 else ""
    units, cents = divmod(abs(int(centimes)), 100)
    return f"{sign}{units:,}".replace(",", " ") + f",{cents:02d} DA"


def split_evenly(total: int, parts: int) -> list[int]:
    """Répartit `total` en `parts` mensualités.

    Chaque mensualité vaut le total/parts arrondi au centime ; la dernière
    absorbe la différence d'arrondi. La somme est toujours égale au total.
    """
    if parts < 1:
        raise ValidationError("Il faut au moins une mensualité.")
    if total < 0:
        raise ValidationError("Le total à répartir ne peut pas être négatif.")
    base = (2 * total + parts) // (2 * parts)  # arrondi au plus proche, demi vers le haut
    amounts = [base] * (parts - 1)
    amounts.append(total - base * (parts - 1))
    return amounts


def split_ttc(ttc: int, rate_bp: int) -> tuple[int, int]:
    """Prix TTC -> (hors taxe, TVA). `rate_bp` en centièmes de % (1900 = 19 %)."""
    ht = int((Decimal(ttc) * 10000 / (10000 + rate_bp)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return ht, ttc - ht


def add_tax(ht: int, rate_bp: int) -> tuple[int, int]:
    """Prix HT -> (TVA, TTC)."""
    tva = int((Decimal(ht) * rate_bp / 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return tva, ht + tva


def markup_percent(cost: int, price: int):
    """Majoration = (prix - coût) / coût. None si le coût est nul."""
    if cost <= 0:
        return None
    return (Decimal(price - cost) * 100 / Decimal(cost)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def margin_percent(cost: int, price: int):
    """Marge = (prix - coût) / prix. None si le prix est nul."""
    if price <= 0:
        return None
    return (Decimal(price - cost) * 100 / Decimal(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def centimes_to_text(centimes: int) -> str:
    """Pour pré-remplir un champ de saisie : 3900000 -> '39000', 3850050 -> '38500,50'."""
    units, cents = divmod(abs(int(centimes)), 100)
    sign = "-" if centimes < 0 else ""
    return f"{sign}{units}" if cents == 0 else f"{sign}{units},{cents:02d}"

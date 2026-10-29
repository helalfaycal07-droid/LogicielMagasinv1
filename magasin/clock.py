"""Une seule horloge : l'heure d'Algérie (UTC+1, sans changement d'heure).

On part de l'heure universelle de l'ordinateur, donc un mauvais fuseau
horaire Windows ne décale pas les documents du logiciel.
"""

from datetime import date, datetime, timedelta, timezone

ALGIERS = timezone(timedelta(hours=1), "Africa/Algiers")


def now() -> datetime:
    return datetime.now(ALGIERS)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def today() -> date:
    return now().date()


def format_dt(iso: str) -> str:
    """'2026-10-22T09:41:41+01:00' -> '22/10/2026 09:41' (24 h)."""
    d = datetime.fromisoformat(iso)
    return d.strftime("%d/%m/%Y %H:%M")


def format_date(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d/%m/%Y")

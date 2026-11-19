"""Points de fidélité clients, enregistrés dans un journal append-only."""

from calendar import monthrange
from datetime import date

from .clock import today
from .db import log_audit
from .errors import ValidationError

CENTIMES_PER_POINT_EARNED = 10_000
CENTIMES_PER_POINT_REDEEMED = 100
POINT_EXPIRATION_MONTHS = 18


def points_discount_centimes(points: int) -> int:
    if not isinstance(points, int) or isinstance(points, bool) or points < 0:
        raise ValidationError("Le nombre de points utilisé doit être un entier positif ou nul.")
    return points * CENTIMES_PER_POINT_REDEEMED


def points_earned_for_sale(total_centimes: int) -> int:
    if total_centimes < 0:
        raise ValidationError("Le total de vente ne peut pas être négatif.")
    return total_centimes // CENTIMES_PER_POINT_EARNED


def _expiration_date(at: str) -> str:
    try:
        issued = date.fromisoformat(at[:10])
    except (TypeError, ValueError, IndexError) as exc:
        raise ValidationError("La date d'attribution des points est invalide.") from exc
    month_index = issued.year * 12 + issued.month - 1 + POINT_EXPIRATION_MONTHS
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(issued.day, monthrange(year, month)[1])).isoformat()


def _effective_date(at: str | None = None) -> str:
    if at is None:
        return today().isoformat()
    try:
        return date.fromisoformat(at[:10]).isoformat()
    except (TypeError, ValueError, IndexError) as exc:
        raise ValidationError("La date de consultation des points est invalide.") from exc


def _eligible_lots(conn, customer_id: int, on_date: str) -> list[dict]:
    result = []
    earned_rows = conn.execute(
        "SELECT id, points, expires_on, created_at FROM loyalty_movements "
        "WHERE customer_id = ? AND kind = 'earned' AND expires_on > ? "
        "ORDER BY expires_on, created_at, id",
        (customer_id, on_date),
    ).fetchall()
    for earned in earned_rows:
        adjustments = conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM loyalty_movements "
            "WHERE source_movement_id = ?",
            (earned["id"],),
        ).fetchone()[0]
        result.append({
            "id": earned["id"],
            "points": earned["points"] + adjustments,
            "expires_on": earned["expires_on"],
        })
    return result


def available_points(conn, customer_id: int, *, at: str | None = None) -> int:
    """Solde non expiré ; les retours peuvent rendre ce solde temporairement négatif."""
    on_date = _effective_date(at)
    return max(0, sum(lot["points"] for lot in _eligible_lots(conn, customer_id, on_date)))


def record_sale_points(conn, customer_id: int | None, sale_id: int,
                       total_centimes: int, at: str,
                       actor_user_id: int | None = None) -> int:
    points = points_earned_for_sale(total_centimes)
    if customer_id is None or points == 0:
        return 0
    expires_on = _expiration_date(at)
    conn.execute(
        "INSERT INTO loyalty_movements "
        "(customer_id, points, kind, sale_id, expires_on, created_at) "
        "VALUES (?, ?, 'earned', ?, ?, ?)",
        (customer_id, points, sale_id, expires_on, at),
    )
    log_audit(
        conn, "points_fidelite_gagnes", "sales", sale_id,
        f"points={points} expiration={expires_on}", at, actor_user_id,
    )
    return points


def redeem_points(conn, customer_id: int, points: int, sale_id: int, at: str,
                  actor_user_id: int | None = None) -> None:
    if points == 0:
        return
    if not isinstance(points, int) or isinstance(points, bool) or points < 0:
        raise ValidationError("Le nombre de points utilisé doit être un entier positif.")
    on_date = _effective_date(at)
    lots = _eligible_lots(conn, customer_id, on_date)
    available = max(0, sum(lot["points"] for lot in lots))
    if points > available:
        raise ValidationError("Le client ne possède pas assez de points disponibles.")

    remaining = points
    for lot in lots:
        if remaining == 0:
            break
        debit = min(remaining, max(0, lot["points"]))
        if debit == 0:
            continue
        conn.execute(
            "INSERT INTO loyalty_movements "
            "(customer_id, points, kind, sale_id, source_movement_id, expires_on, created_at) "
            "VALUES (?, ?, 'redeemed', ?, ?, ?, ?)",
            (customer_id, -debit, sale_id, lot["id"], lot["expires_on"], at),
        )
        remaining -= debit
    if remaining:
        raise ValidationError("Le solde de points a changé ; la vente n'a pas été enregistrée.")
    log_audit(
        conn, "points_fidelite_utilises", "sales", sale_id, f"points={points}", at,
        actor_user_id,
    )


def reverse_sale_points_for_return(conn, sale_id: int, return_id: int,
                                   cumulative_returned_centimes: int,
                                   *, fully_returned: bool, at: str,
                                   actor_user_id: int | None = None) -> None:
    """Annule proportionnellement les points gagnés et restitue ceux consommés."""
    sale = conn.execute(
        "SELECT customer_id, total_centimes FROM sales WHERE id = ?", (sale_id,)
    ).fetchone()
    if sale is None or sale["customer_id"] is None:
        return
    customer_id = sale["customer_id"]
    sale_total = sale["total_centimes"]
    on_date = _effective_date(at)
    changed = False

    earned_rows = conn.execute(
        "SELECT id, points, expires_on FROM loyalty_movements "
        "WHERE sale_id = ? AND kind = 'earned'",
        (sale_id,),
    ).fetchall()
    for earned in earned_rows:
        previous = conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM loyalty_movements "
            "WHERE source_movement_id = ? AND kind = 'return_reversal'",
            (earned["id"],),
        ).fetchone()[0]
        target = (
            earned["points"] if fully_returned
            else earned["points"] * cumulative_returned_centimes // sale_total
            if sale_total else 0
        )
        reversal = target + previous
        if reversal <= 0:
            continue
        conn.execute(
            "INSERT INTO loyalty_movements "
            "(customer_id, points, kind, sale_id, return_id, source_movement_id, "
            "expires_on, created_at) VALUES (?, ?, 'return_reversal', ?, ?, ?, ?, ?)",
            (customer_id, -reversal, sale_id, return_id, earned["id"],
             earned["expires_on"], at),
        )
        changed = True

    redeemed_rows = conn.execute(
        "SELECT source_movement_id, -points AS points, expires_on "
        "FROM loyalty_movements WHERE sale_id = ? AND kind = 'redeemed'",
        (sale_id,),
    ).fetchall()
    for redeemed in redeemed_rows:
        if redeemed["expires_on"] <= on_date:
            continue
        restored = conn.execute(
            """
            SELECT COALESCE(SUM(lm.points), 0)
            FROM loyalty_movements lm
            JOIN sale_returns sr ON sr.id = lm.return_id
            WHERE lm.source_movement_id = ? AND lm.kind = 'return_restore'
              AND sr.sale_id = ?
            """,
            (redeemed["source_movement_id"], sale_id),
        ).fetchone()[0]
        target = (
            redeemed["points"] if fully_returned
            else redeemed["points"] * cumulative_returned_centimes // sale_total
            if sale_total else 0
        )
        restoration = target - restored
        if restoration <= 0:
            continue
        conn.execute(
            "INSERT INTO loyalty_movements "
            "(customer_id, points, kind, sale_id, return_id, source_movement_id, "
            "expires_on, created_at) VALUES (?, ?, 'return_restore', ?, ?, ?, ?, ?)",
            (customer_id, restoration, sale_id, return_id,
             redeemed["source_movement_id"], redeemed["expires_on"], at),
        )
        changed = True

    if changed:
        log_audit(
            conn, "points_fidelite_ajustes_retour", "sale_returns", return_id,
            f"vente_id={sale_id}", at, actor_user_id,
        )

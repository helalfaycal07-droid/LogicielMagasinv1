"""Contrôles d'intégrité : le stock affiché doit toujours égaler la somme des mouvements."""


def check_stock(conn) -> list[str]:
    """Renvoie la liste des anomalies (liste vide = tout est cohérent)."""
    problems = []
    running: dict[tuple[int, int], int] = {}
    for m in conn.execute("SELECT * FROM stock_movements ORDER BY id"):
        key = (m["product_id"], m["warehouse_id"])
        before = running.get(key, 0)
        if m["qty_before"] != before:
            problems.append(f"Mouvement {m['id']} : quantité avant {m['qty_before']} au lieu de {before}.")
        if m["qty_after"] != before + m["quantity"]:
            problems.append(f"Mouvement {m['id']} : quantité après incohérente.")
        running[key] = before + m["quantity"]
    for (product_id, warehouse_id), total in running.items():
        real = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) AS s FROM stock_movements WHERE product_id=? AND warehouse_id=?",
            (product_id, warehouse_id)).fetchone()["s"]
        if real != total:
            problems.append(f"Produit {product_id} : stock {real} différent de la somme {total}.")
    return problems


def check_cash(conn) -> list[str]:
    """Chaque ligne de caisse doit repartir du solde « après » de la précédente."""
    problems = []
    for session in conn.execute("SELECT id FROM cash_sessions"):
        previous = None
        for m in conn.execute("SELECT * FROM cash_movements WHERE session_id=? ORDER BY id", (session["id"],)):
            if previous is not None and m["kind"] != "fermeture" and m["balance_before"] != previous:
                problems.append(f"Caisse, ligne {m['id']} : solde avant {m['balance_before']} au lieu de {previous}.")
            previous = m["balance_after"]
    return problems


def check_serials(conn) -> list[str]:
    """Pour un produit suivi par IMEI, le stock doit égaler le nombre d'appareils en stock."""
    problems = []
    for p in conn.execute("SELECT id, name FROM products WHERE track_serial = 1"):
        stock = conn.execute("SELECT COALESCE(SUM(quantity),0) AS s FROM stock_movements WHERE product_id = ?",
                             (p["id"],)).fetchone()["s"]
        units = conn.execute("SELECT COUNT(*) AS n FROM product_serials WHERE product_id = ? AND status = 'en_stock'",
                             (p["id"],)).fetchone()["n"]
        if stock != units:
            problems.append(f"« {p['name']} » : stock {stock} mais {units} appareil(s) en stock avec IMEI.")
    return problems


def check_all(conn) -> list[str]:
    return check_stock(conn) + check_cash(conn) + check_serials(conn)

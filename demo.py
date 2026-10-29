"""Démonstration du noyau : rejoue les cas de l'essai MekaStore avec des données fictives.

Lancer :  python demo.py
Aucune donnée réelle n'est utilisée. La base est créée en mémoire et disparaît à la fin.
"""

from datetime import date

from magasin.cash import close_session, expected_balance, open_session, record_expense, session_summary
from magasin.credit import add_cheques_for_plan, create_plan, list_installments, record_payment, set_cheque_status
from magasin.customers import create_customer, customer_debt, receivables
from magasin.db import connect
from magasin.integrity import check_all
from magasin.money import format_da, split_ttc
from magasin.products import create_product, price_indicators
from magasin.sales import Line, PaymentIn, create_sale
from magasin.stock import list_movements, stock_of


def titre(texte):
    print("\n" + "=" * 60 + f"\n{texte}\n" + "=" * 60)


def main():
    conn = connect(":memory:")

    titre("1. Produit et prix")
    tv = create_product(conn, "101", "TV BRANDT 40 GOOGLE TV", 3_900_000, cost_centimes=3_000_000, initial_stock=3)
    ind = price_indicators(conn, tv)
    print(f"Prix de vente {format_da(3_900_000)} | coût {format_da(3_000_000)}")
    print(f"Majoration : {ind['majoration_pct']} %   Marge : {ind['marge_pct']} %   (deux choses différentes)")
    ht, tva = split_ttc(3_900_000, 1900)
    print(f"39 000 DA TTC = {format_da(ht)} HT + {format_da(tva)} de TVA (si TVA 19 %)")

    titre("2. Ouverture de caisse et vente au comptant")
    session = open_session(conn, opening_centimes=1_500_000)
    print("Solde d'ouverture :", format_da(1_500_000))
    r = create_sale(conn, [Line(tv)], [PaymentIn("especes", 4_000_000)])
    print(f"Vente {r.number} : total {format_da(r.total_centimes)}, reçu {format_da(r.tendered_centimes)}, "
          f"monnaie à rendre {format_da(r.change_centimes)}")
    print("Stock du téléviseur :", stock_of(conn, tv), "| solde attendu de la caisse :",
          format_da(expected_balance(conn, session)))

    titre("3. Vente à crédit : acompte 10 000 DA sur 38 000 DA, 6 mensualités, 6 chèques")
    client = create_customer(conn, "Client Test", "0555000000", credit_allowed=True)
    v = create_sale(conn, [Line(tv, 1, unit_price_centimes=3_800_000)], [PaymentIn("especes", 1_000_000)],
                    customer_id=client)
    print(f"Vente {v.number} : reste dû {format_da(v.due_centimes)}")
    plan = create_plan(conn, v.sale_id, 6, date(2026, 11, 22))
    add_cheques_for_plan(conn, plan, first_number=1001, bank="Banque test")
    for i in list_installments(conn, plan_id=plan, today=date(2026, 10, 22)):
        print(f"  Échéance {i['number']} : {i['due_date']}  {format_da(i['amount_centimes'])}  [{i['status']}]")

    titre("4. Versement et chèque")
    record_payment(conn, client, 500_000)
    print("Après un versement de 5 000 DA, dette :", format_da(customer_debt(conn, client)))
    print("Qui me doit de l'argent ?", [(c["name"], format_da(c["debt_centimes"])) for c in receivables(conn)])
    statuts = [r["status"] for r in conn.execute("SELECT status FROM cheques ORDER BY id")]
    print("Statut des chèques :", statuts[:2], "... (l'échéance 1 est payée : son chèque est à rendre au client)")
    cheque = conn.execute("SELECT id FROM cheques ORDER BY id LIMIT 1 OFFSET 1").fetchone()["id"]
    set_cheque_status(conn, cheque, "remis_banque")
    set_cheque_status(conn, cheque, "encaisse", today=date(2026, 11, 25))
    print("Chèque n°2 encaissé, dette :", format_da(customer_debt(conn, client)))

    titre("5. Dépense, fermeture de caisse et contrôle")
    record_expense(conn, session, 100_000, "Achat de sacs")
    summary = session_summary(conn, session)
    print("Par nature :", {k: format_da(v) for k, v in summary["by_kind"].items()})
    attendu = summary["expected"]
    res = close_session(conn, session, attendu)
    print(f"Attendu {format_da(res['expected'])}, compté {format_da(res['counted'])}, écart {format_da(res['difference'])}")
    problemes = check_all(conn)
    print("Contrôle d'intégrité :", "tout est cohérent" if not problemes else problemes)

    titre("6. Journal des mouvements de stock du téléviseur")
    for m in list_movements(conn, tv):
        print(f"  {m['movement_type']:<16} {m['qty_before']:>3} -> {m['qty_after']:>3}  ({m['ref_number'] or 'stock initial'})")


if __name__ == "__main__":
    main()

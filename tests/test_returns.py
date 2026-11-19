"""Tests des retours, remboursements et avoirs."""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from magasin.cash import expected_balance, open_session
from magasin.credit import create_plan, list_installments
from magasin.customers import create_customer, customer_debt
from magasin.db import connect
from magasin.errors import NoOpenSession, ValidationError
from magasin.integrity import check_all
from magasin.products import create_product
from magasin.returns import (ReturnLine, create_return, get_sale_for_return,
                             list_sales_for_return, preview_return)
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import add_units, complete_imei, get_unit
from magasin.stock import stock_of
from magasin.vouchers import voucher_balance
from tests.helpers import new_db


class ReturnsTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "RET-001", "Produit retournable", 10_000,
            cost_centimes=5_000, initial_stock=5,
        )

    def test_retour_partiel_applique_la_remise_et_la_remise_en_stock_est_optionnelle(self):
        session_id = open_session(self.conn, opening_centimes=50_000)
        sale = create_sale(
            self.conn, [Line(self.product_id, quantity=2)],
            [PaymentIn("especes", 19_000)], discount_centimes=1_000,
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]

        estimate = preview_return(self.conn, sale.sale_id, [ReturnLine(item_id, 1, True)])
        self.assertEqual(estimate, {
            "total_centimes": 9_500,
            "debt_reduction_centimes": 0,
            "refund_centimes": 9_500,
        })
        first = create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, True)], "especes", "Produit intact"
        )
        self.assertEqual(first["refund_centimes"], 9_500)
        self.assertEqual(stock_of(self.conn, self.product_id), 4)
        self.assertEqual(expected_balance(self.conn, session_id), 59_500)

        second = create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, False)], "especes", "Produit abîmé"
        )
        self.assertEqual(second["total_centimes"], 9_500)
        self.assertEqual(stock_of(self.conn, self.product_id), 4)
        self.assertEqual(expected_balance(self.conn, session_id), 50_000)
        self.assertEqual(
            self.conn.execute("SELECT status FROM sales WHERE id = ?", (sale.sale_id,)).fetchone()[0],
            "retournee",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT SUM(amount_centimes) FROM sale_return_items WHERE sale_item_id = ?",
                (item_id,),
            ).fetchone()[0],
            19_000,
        )

    def test_liste_ventes_recentes_retournables_exclut_ventes_non_valides_et_completes(self):
        open_session(self.conn, opening_centimes=50_000)
        customer_id = create_customer(self.conn, "Client récent", "0555000001")
        recent_sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            customer_id=customer_id,
        )
        completed_sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
        )
        archived_sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
        )
        self.conn.execute(
            "UPDATE sales SET status = 'archivee' WHERE id = ?", (archived_sale.sale_id,)
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (completed_sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, completed_sale.sale_id, [ReturnLine(item_id, 1)],
            "especes", "Retour complet",
        )

        sales = list_sales_for_return(self.conn)

        self.assertEqual([sale["number"] for sale in sales], [recent_sale.number])
        self.assertEqual(sales[0]["customer_name"], "Client récent")
        self.assertEqual(sales[0]["total_centimes"], 10_000)

    def test_liste_ventes_recentes_refuse_limite_non_positive(self):
        with self.assertRaises(ValidationError):
            list_sales_for_return(self.conn, 0)
        with self.assertRaises(ValidationError):
            list_sales_for_return(self.conn, offset=-1)

    def test_recherche_retrouve_vente_ancienne_par_nom_et_numero_partiel(self):
        create_product(
            self.conn, "RET-SEARCH-001", "Produit recherche retour", 10_000,
            cost_centimes=5_000, initial_stock=100,
        )
        self.product_id = self.conn.execute(
            "SELECT id FROM products WHERE reference = 'RET-SEARCH-001'"
        ).fetchone()["id"]
        open_session(self.conn, opening_centimes=50_000)
        old_customer = create_customer(self.conn, "Client_% ancien", "0555000002")
        old_sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            customer_id=old_customer,
        )
        for index in range(50):
            create_sale(
                self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)]
            )

        recent_sales = list_sales_for_return(self.conn)
        found_by_customer = list_sales_for_return(self.conn, search="client_% ancien")
        found_by_number = list_sales_for_return(
            self.conn, search=old_sale.number[-3:]
        )

        self.assertEqual(len(recent_sales), 50)
        self.assertNotIn(old_sale.number, [sale["number"] for sale in recent_sales])
        self.assertEqual(
            [sale["number"] for sale in found_by_customer], [old_sale.number]
        )
        self.assertIn(old_sale.number, [sale["number"] for sale in found_by_number])

    def test_cycle_retour_partiel_puis_complet_sur_base_sqlite_temporaire(self):
        with TemporaryDirectory() as temporary_directory:
            conn = connect(str(Path(temporary_directory) / "retours-test.db"))
            try:
                product_id = create_product(
                    conn, "RET-FLOW-001", "Produit parcours retour", 10_000,
                    cost_centimes=5_000, initial_stock=5,
                )
                session_id = open_session(conn, opening_centimes=50_000)
                sale = create_sale(
                    conn, [Line(product_id, quantity=2)],
                    [PaymentIn("especes", 20_000)],
                )
                item_id = conn.execute(
                    "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
                ).fetchone()["id"]

                self.assertEqual(stock_of(conn, product_id), 3)
                self.assertEqual(expected_balance(conn, session_id), 70_000)

                partial = create_return(
                    conn, sale.sale_id, [ReturnLine(item_id, 1, True)],
                    "especes", "Retour partiel de contrôle",
                )
                available_sale = get_sale_for_return(conn, sale.number)
                self.assertEqual(partial["refund_centimes"], 10_000)
                self.assertEqual(available_sale["items"][0]["returned_quantity"], 1)
                self.assertEqual(stock_of(conn, product_id), 4)
                self.assertEqual(expected_balance(conn, session_id), 60_000)

                complete = create_return(
                    conn, sale.sale_id, [ReturnLine(item_id, 1, False)],
                    "especes", "Retour complet de contrôle",
                )
                self.assertEqual(complete["refund_centimes"], 10_000)
                completed_sale = get_sale_for_return(conn, sale.number)
                self.assertEqual(completed_sale["items"][0]["returned_quantity"], 2)
                self.assertEqual(stock_of(conn, product_id), 4)
                self.assertEqual(expected_balance(conn, session_id), 50_000)
                self.assertEqual(
                    conn.execute(
                        "SELECT status FROM sales WHERE id = ?", (sale.sale_id,)
                    ).fetchone()["status"],
                    "retournee",
                )
                self.assertNotIn(
                    sale.number,
                    [row["number"] for row in list_sales_for_return(conn)],
                )
                self.assertEqual(check_all(conn), [])
            finally:
                conn.close()

    def test_retour_sur_credit_reduit_la_dette_et_les_echeances(self):
        customer_id = create_customer(
            self.conn, "Client retour", "0555000000", credit_allowed=True
        )
        sale = create_sale(
            self.conn, [Line(self.product_id, quantity=2)], [], customer_id=customer_id
        )
        plan_id = create_plan(self.conn, sale.sale_id, 4, date(2026, 12, 1))
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]

        first = create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, True)], "especes", "Réduction de dette"
        )
        self.assertEqual(first["debt_reduction_centimes"], 10_000)
        self.assertEqual(first["refund_centimes"], 0)
        self.assertEqual(customer_debt(self.conn, customer_id), 10_000)
        installments = list_installments(self.conn, plan_id=plan_id)
        self.assertEqual(sum(row["reste_centimes"] for row in installments), 10_000)
        self.assertEqual(sum(row["cancelled_centimes"] for row in installments), 10_000)

        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, False)], "especes", "Annulation du solde"
        )
        self.assertEqual(customer_debt(self.conn, customer_id), 0)
        self.assertTrue(all(row["status"] == "annulee" for row in list_installments(
            self.conn, plan_id=plan_id
        )))

    def test_avoir_de_retour_est_utilisable_et_son_solde_baisse(self):
        customer_id = create_customer(self.conn, "Client avoir", "0555000001")
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            customer_id=customer_id,
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1)], "avoir", "Retour client"
        )
        self.assertEqual(voucher_balance(self.conn, customer_id), 10_000)

        second_sale = create_sale(
            self.conn, [Line(self.product_id)],
            [PaymentIn("avoir", 10_000)], customer_id=customer_id,
        )

        self.assertEqual(second_sale.due_centimes, 0)
        self.assertEqual(voucher_balance(self.conn, customer_id), 0)

    def test_especes_exigem_uma_caisse_ouverte_e_o_retorno_e_atomico(self):
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)]
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]

        with self.assertRaises(NoOpenSession):
            create_return(
                self.conn, sale.sale_id, [ReturnLine(item_id, 1, True)],
                "especes", "Sem caixa",
            )

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sale_returns").fetchone()[0], 0)
        self.assertEqual(stock_of(self.conn, self.product_id), 4)

    def test_avoir_exige_un_client_e_imei_retornado_e_rastreavel(self):
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)]
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        with self.assertRaises(ValidationError):
            create_return(
                self.conn, sale.sale_id, [ReturnLine(item_id, 1)],
                "avoir", "Aucun client",
            )

        phone = create_product(
            self.conn, "RET-IMEI", "Téléphone retour", 20_000, track_serial=True
        )
        imei = complete_imei("35678901234567")
        add_units(self.conn, phone, [(imei, None)])
        phone_sale = create_sale(
            self.conn, [Line(phone, imeis=[imei])], [PaymentIn("ccp", 20_000)]
        )
        phone_item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (phone_sale.sale_id,)
        ).fetchone()["id"]
        open_session(self.conn, opening_centimes=30_000)
        create_return(
            self.conn, phone_sale.sale_id,
            [ReturnLine(phone_item_id, 1, True, [imei])],
            "especes", "Appareil contrôlé",
        )

        self.assertEqual(get_unit(self.conn, imei)["status"], "en_stock")
        self.assertEqual(stock_of(self.conn, phone), 1)
        self.assertEqual(check_all(self.conn), [])


if __name__ == "__main__":
    unittest.main()

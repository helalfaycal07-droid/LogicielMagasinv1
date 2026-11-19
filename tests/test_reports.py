"""Tests des rapports essentiels."""

import unittest

from magasin.cash import close_session, open_session
from magasin.customers import create_customer
from magasin.errors import ValidationError
from magasin.products import create_product, update_product
from magasin.reports import report_data
from magasin.reports import profit_report_data
from magasin.returns import ReturnLine, create_return
from magasin.sales import Line, PaymentIn, create_sale
from tests.helpers import new_db


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "R-001", "Produit rapport", 10_000, cost_centimes=4_000,
            min_stock=2, initial_stock=3,
        )

    def test_rapport_filtre_les_ventes_et_calcule_le_stock_actuel(self):
        create_sale(
            self.conn, [Line(self.product_id, quantity=1)],
            [PaymentIn("carte", 10_000)], at="2026-11-02T10:00:00+01:00",
        )
        create_sale(
            self.conn, [Line(self.product_id, quantity=1)],
            [PaymentIn("virement", 10_000)], at="2026-10-31T10:00:00+01:00",
        )

        data = report_data(self.conn, "2026-11-01", "2026-11-30")

        self.assertEqual(data["sales_count"], 1)
        self.assertEqual(data["sales_total_centimes"], 10_000)
        self.assertEqual(data["products_sold"], [{
            "reference": "R-001", "name": "Produit rapport",
            "quantity": 1, "total_centimes": 10_000,
        }])
        self.assertEqual(data["payments"], [{"method": "carte", "total_centimes": 10_000}])
        self.assertEqual(data["inventory_value_centimes"], 4_000)
        self.assertEqual(data["low_stock_count"], 1)
        self.assertEqual(data["negative_stock_count"], 0)

    def test_rapport_affiche_les_clotures_et_leurs_ecarts(self):
        session_id = open_session(self.conn, opening_centimes=1_000,
                                  at="2026-11-02T08:00:00+01:00")
        close_session(self.conn, session_id, 900, reason="Écart de test",
                      at="2026-11-02T18:00:00+01:00")

        data = report_data(self.conn, "2026-11-02", "2026-11-02")

        self.assertEqual(len(data["cash_closures"]), 1)
        self.assertEqual(data["cash_closures"][0]["expected_centimes"], 1_000)
        self.assertEqual(data["cash_closures"][0]["counted_centimes"], 900)
        self.assertEqual(data["cash_closures"][0]["difference_centimes"], -100)

    def test_rapport_distingue_les_retours_de_la_periode_et_le_chiffre_affaires_net(self):
        customer_id = create_customer(self.conn, "Client rapport", "0555000002")
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            customer_id=customer_id, at="2026-11-01T10:00:00+01:00",
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1)],
            "avoir", "Retour rapport", at="2026-11-02T10:00:00+01:00",
        )

        data = report_data(self.conn, "2026-11-02", "2026-11-02")

        self.assertEqual(data["sales_count"], 0)
        self.assertEqual(data["returns_count"], 1)
        self.assertEqual(data["returns_total_centimes"], 10_000)
        self.assertEqual(data["net_sales_total_centimes"], -10_000)
        self.assertEqual(data["products_sold"][0]["quantity"], -1)
        self.assertEqual(data["payments"], [{"method": "avoir", "total_centimes": -10_000}])
        self.assertEqual(data["returns"][0]["reason"], "Retour rapport")

    def test_rapport_refuse_une_periode_invalide(self):
        for start, end in (
            ("02/11/2026", "2026-11-30"),
            ("2026-12-01", "2026-11-30"),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                report_data(self.conn, start, end)

    def test_benefice_mensuel_utilise_le_cout_capture_a_la_vente_et_la_remise(self):
        create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 9_000)],
            discount_centimes=1_000, at="2026-11-02T10:00:00+01:00",
        )
        update_product(self.conn, self.product_id, cost_centimes=8_000)

        data = profit_report_data(self.conn, "2026-11-01", "2026-11-30")

        self.assertTrue(data["profit_complete"])
        self.assertEqual(data["periods"], [{
            "period": "2026-11",
            "revenue_centimes": 9_000,
            "returns_centimes": 0,
            "net_revenue_centimes": 9_000,
            "cost_centimes": 4_000,
            "unknown_cost_quantity": 0,
            "profit_centimes": 5_000,
            "complete": True,
        }])

    def test_benefice_annuel_tient_compte_des_retours_revendables(self):
        customer_id = create_customer(self.conn, "Client bénéfice", "0555000003")
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            customer_id=customer_id, at="2026-01-03T10:00:00+01:00",
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, True)],
            "avoir", "Retour revendable", at="2026-02-05T10:00:00+01:00",
        )

        data = profit_report_data(self.conn, "2026-01-01", "2026-12-31", "year")

        self.assertEqual(len(data["periods"]), 1)
        self.assertEqual(data["periods"][0]["period"], "2026")
        self.assertEqual(data["periods"][0]["net_revenue_centimes"], 0)
        self.assertEqual(data["periods"][0]["cost_centimes"], 0)
        self.assertEqual(data["periods"][0]["profit_centimes"], 0)

    def test_benefice_signale_les_couts_historiques_indisponibles(self):
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 10_000)],
            at="2026-11-02T10:00:00+01:00",
        )
        self.conn.execute(
            "UPDATE sale_items SET unit_cost_centimes = NULL WHERE sale_id = ?",
            (sale.sale_id,),
        )

        data = profit_report_data(self.conn, "2026-11-01", "2026-11-30")

        self.assertFalse(data["profit_complete"])
        self.assertIsNone(data["profit_centimes"])
        self.assertEqual(data["periods"][0]["unknown_cost_quantity"], 1)
        self.assertIsNone(data["periods"][0]["profit_centimes"])

    def test_benefice_refuse_un_regroupement_invalide(self):
        with self.assertRaises(ValidationError):
            profit_report_data(self.conn, "2026-11-01", "2026-11-30", "jour")


if __name__ == "__main__":
    unittest.main()

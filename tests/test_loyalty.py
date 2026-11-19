import sqlite3
import tempfile
import unittest
from pathlib import Path

from magasin.customers import create_customer
from magasin.db import MIGRATIONS, _seed, connect
from magasin.errors import ValidationError
from magasin.loyalty import available_points
from magasin.products import create_product
from magasin.returns import ReturnLine, create_return
from magasin.sales import Line, create_sale
from magasin.stock import stock_of
from tests.helpers import new_db


class LoyaltyTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.customer_id = create_customer(
            self.conn, "Client fidélité", "0555000012", credit_allowed=True
        )
        self.product_id = create_product(
            self.conn, "FID-001", "Produit fidélité", 10_000, initial_stock=10
        )

    def test_points_gagnes_sur_le_total_net_et_expirent_apres_18_mois(self):
        sale = create_sale(
            self.conn,
            [Line(self.product_id, quantity=2)],
            [],
            customer_id=self.customer_id,
            at="2026-11-02T10:00:00+01:00",
        )

        self.assertEqual(sale.loyalty_points_earned, 2)
        self.assertEqual(
            available_points(self.conn, self.customer_id, at="2028-05-01"),
            2,
        )
        self.assertEqual(
            available_points(self.conn, self.customer_id, at="2028-05-02"),
            0,
        )

    def test_utilisation_des_points_est_atomique_et_gagne_des_points_sur_le_net(self):
        create_sale(
            self.conn,
            [Line(self.product_id)],
            [],
            customer_id=self.customer_id,
            at="2026-11-02T10:00:00+01:00",
        )
        second_sale = create_sale(
            self.conn,
            [Line(self.product_id, quantity=2)],
            [],
            customer_id=self.customer_id,
            loyalty_points=1,
            at="2026-11-03T10:00:00+01:00",
        )

        self.assertEqual(second_sale.total_centimes, 19_900)
        self.assertEqual(second_sale.loyalty_points_redeemed, 1)
        self.assertEqual(second_sale.loyalty_points_earned, 1)
        self.assertEqual(available_points(self.conn, self.customer_id, at="2026-11-03"), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT loyalty_points_redeemed FROM sales WHERE id = ?",
                (second_sale.sale_id,),
            ).fetchone()[0],
            1,
        )

    def test_retour_total_annule_les_points_gagnes_et_restitue_les_points_utilises(self):
        create_sale(
            self.conn,
            [Line(self.product_id)],
            [],
            customer_id=self.customer_id,
            at="2026-11-02T10:00:00+01:00",
        )
        sale = create_sale(
            self.conn,
            [Line(self.product_id, quantity=2)],
            [],
            customer_id=self.customer_id,
            loyalty_points=1,
            at="2026-11-03T10:00:00+01:00",
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()[0]
        stock_before_return = stock_of(self.conn, self.product_id)

        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1)], "avoir", "Retour partiel"
        )
        self.assertEqual(available_points(self.conn, self.customer_id, at="2026-11-04"), 1)

        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1)], "avoir", "Retour du solde"
        )
        self.assertEqual(available_points(self.conn, self.customer_id, at="2026-11-04"), 1)
        self.assertEqual(stock_of(self.conn, self.product_id), stock_before_return)

    def test_points_insuffisants_n_enregistrent_ni_vente_ni_mouvement_de_stock(self):
        sales_before = self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        stock_before = stock_of(self.conn, self.product_id)

        with self.assertRaises(ValidationError):
            create_sale(
                self.conn,
                [Line(self.product_id)],
                [],
                customer_id=self.customer_id,
                loyalty_points=1,
            )

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], sales_before)
        self.assertEqual(stock_of(self.conn, self.product_id), stock_before)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM loyalty_movements").fetchone()[0],
            0,
        )

    def test_migration_depuis_schema_9_est_additive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema9.db"
            legacy = sqlite3.connect(":memory:")
            legacy.row_factory = sqlite3.Row
            legacy.execute("PRAGMA foreign_keys = ON")
            for version, migration in enumerate(MIGRATIONS[:9], start=1):
                legacy.executescript(
                    "BEGIN;\n" + migration
                    + f"\nPRAGMA user_version = {version};\nCOMMIT;"
                )
                if version == 1:
                    _seed(legacy)
            backup = sqlite3.connect(path)
            legacy.backup(backup)
            backup.close()
            legacy.close()

            migrated = connect(str(path))
            self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 14)
            sales_columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(sales)")
            }
            self.assertIn("loyalty_points_redeemed", sales_columns)
            self.assertEqual(
                migrated.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'loyalty_movements'"
                ).fetchone()[0],
                1,
            )
            migrated.close()


if __name__ == "__main__":
    unittest.main()

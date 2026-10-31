import unittest

from magasin.errors import ValidationError
from magasin.integrity import check_all
from magasin.products import (archive_product, create_product, delete_product, get_product, product_usage,
                              restore_product, stock_report)
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import add_units, complete_imei
from magasin.stock import add_movement, inventory_count
from tests.helpers import new_db, open_cash


def actions(conn):
    return [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]


class SuppressionTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()

    def test_un_produit_cree_par_erreur_se_supprime_pour_de_bon(self):
        p = create_product(self.conn, "X1", "Produit d'essai", 10_000, initial_stock=3)
        inventory_count(self.conn, p, 5, "recomptage")           # un inventaire ne bloque pas
        self.assertTrue(product_usage(self.conn, p)["deletable"])
        delete_product(self.conn, p)
        self.assertIsNone(get_product(self.conn, p))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0], 0)
        self.assertEqual(stock_report(self.conn), [])
        derniere = self.conn.execute("SELECT * FROM audit_log WHERE action = 'produit_supprime'").fetchone()
        self.assertIn("X1", derniere["details"])
        self.assertIn("stock au moment de la suppression : 5", derniere["details"])
        self.assertEqual(check_all(self.conn), [])

    def test_un_produit_vendu_ne_se_supprime_pas(self):
        p = create_product(self.conn, "X1", "Produit vendu", 10_000, initial_stock=3)
        open_cash(self.conn)
        create_sale(self.conn, [Line(p)], [PaymentIn("especes", 10_000)])
        usage = product_usage(self.conn, p)
        self.assertFalse(usage["deletable"])
        with self.assertRaises(ValidationError) as ctx:
            delete_product(self.conn, p)
        self.assertIn("vente", str(ctx.exception))
        self.assertIsNotNone(get_product(self.conn, p))          # rien n'a été effacé

    def test_un_produit_avec_des_imei_ne_se_supprime_pas(self):
        p = create_product(self.conn, "T1", "Téléphone", 3_000_000, track_serial=True)
        add_units(self.conn, p, [(complete_imei("35123400000001"), None)])
        with self.assertRaises(ValidationError) as ctx:
            delete_product(self.conn, p)
        self.assertIn("IMEI", str(ctx.exception))

    def test_un_ajustement_ou_une_reception_bloque_la_suppression(self):
        p = create_product(self.conn, "X1", "Produit", 10_000)
        add_movement(self.conn, p, 2, "reception_achat")
        with self.assertRaises(ValidationError):
            delete_product(self.conn, p)

    def test_archiver_puis_restaurer(self):
        p = create_product(self.conn, "X1", "Produit", 10_000, initial_stock=2)
        archive_product(self.conn, p)
        self.assertEqual(stock_report(self.conn), [])
        self.assertEqual([r["reference"] for r in stock_report(self.conn, archived=True)], ["X1"])
        restore_product(self.conn, p)
        self.assertEqual([r["reference"] for r in stock_report(self.conn)], ["X1"])
        self.assertEqual(actions(self.conn)[-2:], ["produit_archive", "produit_restaure"])

    def test_reference_d_un_produit_archive_est_expliquee(self):
        p = create_product(self.conn, "X1", "Ancien produit", 10_000)
        archive_product(self.conn, p)
        with self.assertRaises(ValidationError) as ctx:
            create_product(self.conn, "X1", "Nouveau", 20_000)
        self.assertIn("archivé", str(ctx.exception))

    def test_la_reference_d_un_produit_supprime_redevient_libre(self):
        p = create_product(self.conn, "X1", "Essai", 10_000)
        delete_product(self.conn, p)
        create_product(self.conn, "X1", "Vrai produit", 20_000)    # pas d'erreur


if __name__ == "__main__":
    unittest.main()

import unittest

from magasin.errors import ValidationError
from magasin.forms import parse_product_form
from magasin.integrity import check_all
from magasin.products import create_product, find_product, stock_report
from magasin.stock import inventory_count, list_movements, stock_of
from tests.helpers import new_db


class InventaireTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.p = create_product(self.conn, "A1", "Produit A", 100_000, cost_centimes=60_000, initial_stock=5)

    def test_inventaire_cree_le_mouvement_de_l_ecart(self):
        inventory_count(self.conn, self.p, 3, "Comptage du samedi")
        self.assertEqual(stock_of(self.conn, self.p), 3)
        dernier = list_movements(self.conn, self.p)[-1]
        self.assertEqual((dernier["movement_type"], dernier["quantity"], dernier["qty_before"], dernier["qty_after"]),
                         ("inventaire", -2, 5, 3))
        self.assertEqual(check_all(self.conn), [])

    def test_inventaire_sans_ecart_ne_cree_rien(self):
        self.assertIsNone(inventory_count(self.conn, self.p, 5))
        self.assertEqual(len(list_movements(self.conn, self.p)), 1)

    def test_inventaire_negatif_refuse(self):
        with self.assertRaises(ValidationError):
            inventory_count(self.conn, self.p, -1)


class RapportEtFormulaireTests(unittest.TestCase):
    def test_rapport_de_stock(self):
        conn = new_db()
        create_product(conn, "A1", "Produit A", 100_000, cost_centimes=60_000, initial_stock=2, min_stock=2)
        create_product(conn, "B1", "Produit B", 50_000, initial_stock=10)
        lignes = {r["reference"]: r for r in stock_report(conn)}
        self.assertTrue(lignes["A1"]["bas"])
        self.assertFalse(lignes["B1"]["bas"])
        self.assertEqual(str(lignes["A1"]["majoration_pct"]), "66.67")
        self.assertEqual(str(lignes["A1"]["marge_pct"]), "40.00")

    def test_recherche_par_nom_reference_et_code_barres(self):
        conn = new_db()
        create_product(conn, "A1", "Réfrigérateur 350 L", 100_000, barcode="6130000000011")
        self.assertEqual(len(find_product(conn, "frigo")), 0)
        self.assertEqual(len(find_product(conn, "frigérateur")), 1)
        self.assertEqual(len(find_product(conn, "A1")), 1)
        self.assertEqual(len(find_product(conn, "6130000000011")), 1)

    def test_lecture_du_formulaire(self):
        v = parse_product_form(reference=" 101 ", name="TV", price_text="39 000,00", cost_text="30000",
                               tax_percent_text="19", min_stock_text="2", initial_stock_text="3")
        self.assertEqual((v["price_centimes"], v["cost_centimes"], v["tax_rate_bp"], v["initial_stock"]),
                         (3_900_000, 3_000_000, 1900, 3))

    def test_formulaire_invalide(self):
        for kwargs in (
            dict(reference="", name="TV", price_text="10"),
            dict(reference="1", name="", price_text="10"),
            dict(reference="1", name="TV", price_text=""),
            dict(reference="1", name="TV", price_text="abc"),
            dict(reference="1", name="TV", price_text="10", initial_stock_text="trois"),
            dict(reference="1", name="TV", price_text="10", tax_percent_text="x"),
        ):
            with self.assertRaises(ValidationError):
                parse_product_form(**kwargs)


if __name__ == "__main__":
    unittest.main()

import unittest

from magasin.errors import ValidationError
from magasin.money import centimes_to_text
from magasin.products import create_product, get_product, update_product
from magasin.stock import stock_of
from tests.helpers import new_db


class ModificationProduitTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.a = create_product(self.conn, "A1", "Produit A", 3_900_000, cost_centimes=3_000_000,
                                barcode="111", initial_stock=3)
        self.b = create_product(self.conn, "B1", "Produit B", 100_000, barcode="222")

    def journal(self):
        return [r["details"] for r in self.conn.execute(
            "SELECT details FROM audit_log WHERE action = 'produit_modifie'")]

    def test_corriger_une_erreur_de_prix_laisse_le_stock_intact(self):
        faits = update_product(self.conn, self.a, price_centimes=3_800_000)
        self.assertEqual(faits, ["price_centimes: 3900000 -> 3800000"])
        self.assertEqual(get_product(self.conn, self.a)["price_centimes"], 3_800_000)
        self.assertEqual(stock_of(self.conn, self.a), 3)
        self.assertEqual(self.journal(), ["price_centimes: 3900000 -> 3800000"])

    def test_aucun_changement_n_ecrit_rien(self):
        self.assertEqual(update_product(self.conn, self.a, price_centimes=3_900_000), [])
        self.assertEqual(self.journal(), [])

    def test_reference_ou_code_barres_deja_utilises(self):
        with self.assertRaises(ValidationError):
            update_product(self.conn, self.a, reference="B1")
        with self.assertRaises(ValidationError):
            update_product(self.conn, self.a, barcode="222")
        self.assertEqual(get_product(self.conn, self.a)["reference"], "A1")  # rien n'a changé

    def test_valeurs_refusees(self):
        for changes in ({"name": "  "}, {"price_centimes": -1}, {"stock": 5}):
            with self.assertRaises(ValidationError):
                update_product(self.conn, self.a, **changes)

    def test_un_echec_n_applique_aucun_changement(self):
        with self.assertRaises(ValidationError):
            update_product(self.conn, self.a, price_centimes=1, reference="B1")
        self.assertEqual(get_product(self.conn, self.a)["price_centimes"], 3_900_000)

    def test_texte_de_saisie(self):
        self.assertEqual(centimes_to_text(3_900_000), "39000")
        self.assertEqual(centimes_to_text(3_850_050), "38500,50")


if __name__ == "__main__":
    unittest.main()

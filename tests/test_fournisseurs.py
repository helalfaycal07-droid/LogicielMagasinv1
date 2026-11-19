import unittest

from magasin.errors import PermissionDenied, ValidationError
from magasin.products import create_product
from magasin.purchases import (ReceiptLine, create_supplier, list_suppliers,
                               receive_stock, set_supplier_active, update_supplier)
from magasin.users import create_first_admin, create_user
from tests.helpers import new_db


class FournisseursTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "FOUR-001", "Produit fournisseur", 10_000,
            cost_centimes=5_000,
        )

    def test_creation_et_modification_des_coordonnees(self):
        supplier_id = create_supplier(
            self.conn, None, "Fournisseur Test", phone="0555000001",
            email="contact@example.com", address="Alger", notes="Livraison rapide",
        )

        update_supplier(
            self.conn, None, supplier_id, "Fournisseur Test 2",
            phone="0555000002", email="achat@example.com",
            address="Alger", notes="Livraison rapide",
        )

        supplier, = list_suppliers(self.conn)
        self.assertEqual(supplier["name"], "Fournisseur Test 2")
        self.assertEqual(supplier["phone"], "0555000002")
        self.assertEqual(supplier["email"], "achat@example.com")
        self.assertEqual(supplier["address"], "Alger")
        self.assertEqual(supplier["notes"], "Livraison rapide")

    def test_doublons_et_coordonnees_invalides_sont_refuses(self):
        create_supplier(self.conn, None, "Fournisseur Test")
        for name, email in (
            (" fournisseur test ", None),
            ("Fournisseur Email", "pas-un-email"),
            (" ", None),
        ):
            with self.subTest(name=name, email=email), self.assertRaises(ValidationError):
                create_supplier(self.conn, None, name, email=email)

    def test_reception_est_liee_a_la_fiche_et_alimente_son_solde(self):
        supplier_id = create_supplier(self.conn, None, "Fournisseur Test")
        receive_stock(
            self.conn, [ReceiptLine(self.product_id, 2)], " fournisseur test "
        )

        receipt = self.conn.execute(
            "SELECT supplier_id FROM purchase_receipts WHERE number = 'REC-000001'"
        ).fetchone()
        supplier, = list_suppliers(self.conn)
        self.assertEqual(receipt["supplier_id"], supplier_id)
        self.assertEqual(supplier["receipt_count"], 1)
        self.assertEqual(supplier["total_centimes"], 10_000)
        self.assertEqual(supplier["due_centimes"], 10_000)

    def test_nom_saisi_lors_dune_reception_cree_une_fiche_compatible(self):
        receive_stock(self.conn, [ReceiptLine(self.product_id, 1)], "Nouveau fournisseur")

        supplier, = list_suppliers(self.conn)
        self.assertEqual(supplier["name"], "Nouveau fournisseur")
        self.assertEqual(supplier["receipt_count"], 1)

    def test_archivage_conserve_lhistorique_et_bloque_les_nouvelles_receptions(self):
        supplier_id = create_supplier(self.conn, None, "Fournisseur Test")
        receive_stock(self.conn, [ReceiptLine(self.product_id, 1)], "Fournisseur Test")

        set_supplier_active(self.conn, None, supplier_id, False)

        supplier, = list_suppliers(self.conn)
        self.assertFalse(supplier["active"])
        self.assertEqual(supplier["receipt_count"], 1)
        self.assertEqual(supplier["due_centimes"], 5_000)
        with self.assertRaises(ValidationError):
            receive_stock(self.conn, [ReceiptLine(self.product_id, 1)], "Fournisseur Test")

        set_supplier_active(self.conn, None, supplier_id, True)
        self.assertTrue(list_suppliers(self.conn)[0]["active"])

    def test_les_permissions_achats_sappliquent_aux_fiches_fournisseur(self):
        admin_id = create_first_admin(self.conn, "admin-four", "MotDePasseAdmin")
        cashier_id = create_user(
            self.conn, admin_id, "caisse-four", "MotDePasseCaisse", "caissier"
        )

        with self.assertRaises(PermissionDenied):
            create_supplier(self.conn, cashier_id, "Fournisseur Test")


if __name__ == "__main__":
    unittest.main()

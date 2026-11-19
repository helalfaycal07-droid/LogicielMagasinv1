import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from PySide6.QtWidgets import QApplication, QDialog

from interface.dialogues import ModifierProduitDialogue
from interface.page_produits import PageProduits
from magasin.products import create_product, stock_report
from magasin.purchases import create_supplier, set_supplier_active
from tests.helpers import new_db


class SupplierLinksInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "fournisseurs.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SUIVI_STOCK"
        sheet["A3"] = "CODE PRODUIT"
        sheet["B3"] = "FOURNISSEUR"
        sheet["A4"] = "UI-SUP-1"
        sheet["B4"] = "Fournisseur UI"
        workbook.save(self.path)
        workbook.close()
        self.product_id = create_product(
            self.conn, "UI-SUP-1", "Produit UI", 15_000, initial_stock=4
        )
        self.supplier_id = create_supplier(self.conn, None, "Fournisseur UI")

    def test_dedicated_action_applies_after_preview_confirmation(self):
        page = PageProduits(self.conn)
        with (
            patch(
                "interface.page_produits.QFileDialog.getOpenFileName",
                return_value=(str(self.path), ""),
            ),
            patch(
                "interface.page_produits.ouvrir_dialogue",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch("interface.page_produits.QMessageBox.information"),
        ):
            page.rattacher_fournisseurs_excel()

        self.assertEqual(
            [
                tuple(row) for row in self.conn.execute(
                    "SELECT product_id, supplier_id FROM product_suppliers"
                )
            ],
            [(self.product_id, self.supplier_id)],
        )
        self.assertEqual(
            page.tableau.item(0, 8).text(),
            "Fournisseur UI",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],
            1,
        )

    def test_cancelled_preview_does_not_create_association(self):
        page = PageProduits(self.conn)
        with (
            patch(
                "interface.page_produits.QFileDialog.getOpenFileName",
                return_value=(str(self.path), ""),
            ),
            patch(
                "interface.page_produits.ouvrir_dialogue",
                return_value=QDialog.DialogCode.Rejected,
            ),
        ):
            page.rattacher_fournisseurs_excel()

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_suppliers").fetchone()[0],
            0,
        )

    def test_product_edit_dialog_selects_and_saves_supplier_in_catalog(self):
        second_supplier_id = create_supplier(
            self.conn, None, "Fournisseur UI secondaire"
        )
        page = PageProduits(self.conn)
        product = stock_report(self.conn)[0]
        dialogue = ModifierProduitDialogue(page, product, self.conn)

        self.assertEqual(
            dialogue.fournisseur.currentData(),
            0,
        )
        dialogue.fournisseur.setCurrentIndex(
            dialogue.fournisseur.findData(second_supplier_id)
        )
        dialogue._valider()
        self.assertIsNotNone(dialogue.valeurs)
        page.appliquer_modification(self.product_id, dialogue.valeurs)

        linked = self.conn.execute(
            "SELECT supplier_id FROM product_suppliers WHERE product_id = ?",
            (self.product_id,),
        ).fetchone()
        self.assertEqual(linked["supplier_id"], second_supplier_id)
        self.assertEqual(page.tableau.item(0, 8).text(), "Fournisseur UI secondaire")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE product_id = ?",
                (self.product_id,),
            ).fetchone()[0],
            1,
        )

    def test_multiple_existing_suppliers_are_preserved_unless_replaced(self):
        second_supplier_id = create_supplier(
            self.conn, None, "Fournisseur UI secondaire"
        )
        self.conn.execute(
            "INSERT INTO product_suppliers (product_id, supplier_id, created_at) "
            "VALUES (?, ?, ?)",
            (self.product_id, self.supplier_id, "2026-11-02T10:00:00+01:00"),
        )
        self.conn.execute(
            "INSERT INTO product_suppliers (product_id, supplier_id, created_at) "
            "VALUES (?, ?, ?)",
            (self.product_id, second_supplier_id, "2026-11-02T10:00:00+01:00"),
        )
        page = PageProduits(self.conn)
        dialogue = ModifierProduitDialogue(page, stock_report(self.conn)[0], self.conn)

        self.assertIsNone(dialogue.fournisseur.currentData())
        dialogue._valider()
        page.appliquer_modification(self.product_id, dialogue.valeurs)

        self.assertEqual(
            {
                row["supplier_id"] for row in self.conn.execute(
                    "SELECT supplier_id FROM product_suppliers WHERE product_id = ?",
                    (self.product_id,),
                )
            },
            {self.supplier_id, second_supplier_id},
        )

    def test_failed_supplier_change_rolls_back_product_edits(self):
        archived_id = create_supplier(
            self.conn, None, "Fournisseur UI archivé"
        )
        set_supplier_active(self.conn, None, archived_id, False)
        product = stock_report(self.conn)[0]
        page = PageProduits(self.conn)
        values = {
            "reference": product["reference"],
            "name": product["name"],
            "barcode": product["barcode"],
            "category": product["category"],
            "brand": product["brand"],
            "cost_centimes": product["cost_centimes"],
            "price_centimes": product["price_centimes"] + 100,
            "price_includes_tax": product["price_includes_tax"],
            "tax_rate_bp": product["tax_rate_bp"],
            "min_stock": product["min_stock"],
            "track_serial": product["track_serial"],
            "supplier_id": archived_id,
        }

        with patch("interface.page_produits.montrer_erreur"):
            page.appliquer_modification(self.product_id, values)

        stored = self.conn.execute(
            "SELECT price_centimes FROM products WHERE id = ?", (self.product_id,)
        ).fetchone()
        self.assertEqual(stored["price_centimes"], product["price_centimes"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product_suppliers WHERE product_id = ?",
                (self.product_id,),
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()

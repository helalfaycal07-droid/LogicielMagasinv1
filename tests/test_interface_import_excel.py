import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from PySide6.QtWidgets import QApplication, QDialog

from interface.page_produits import PageProduits
from magasin.products import find_product
from tests.helpers import new_db


class ImportExcelInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "stock.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SUIVI_STOCK"
        headers = (
            "CODE PRODUIT", "DÉSIGNATION", "CATÉGORIE", "PRIX ACHAT (DA)",
            "PRIX VENTE (DA)", "STOCK RESTANT", "SEUIL ALERTE",
        )
        for column, header in enumerate(headers, start=1):
            sheet.cell(row=7, column=column, value=header)
        values = ("UI-1", "Produit UI", "Accessoires", 100, 150, 2, 1)
        for column, value in enumerate(values, start=1):
            sheet.cell(row=8, column=column, value=value)
        workbook.save(self.path)
        workbook.close()

    def test_import_action_loads_preview_and_records_stock(self):
        page = PageProduits(self.conn)
        with (
            patch("interface.page_produits.QFileDialog.getOpenFileName",
                  return_value=(str(self.path), "")),
            patch("interface.page_produits.ouvrir_dialogue",
                  return_value=QDialog.DialogCode.Accepted),
            patch("interface.page_produits.QMessageBox.information"),
        ):
            page.importer_excel()

        products = find_product(self.conn, "UI-1")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "Produit UI")

    def test_cancelled_preview_does_not_import(self):
        page = PageProduits(self.conn)
        with (
            patch("interface.page_produits.QFileDialog.getOpenFileName",
                  return_value=(str(self.path), "")),
            patch("interface.page_produits.ouvrir_dialogue",
                  return_value=QDialog.DialogCode.Rejected),
        ):
            page.importer_excel()

        self.assertEqual(find_product(self.conn, "UI-1"), [])


if __name__ == "__main__":
    unittest.main()

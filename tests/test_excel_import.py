import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from magasin.errors import ValidationError
from magasin.import_excel import import_stock_workbook, preview_stock_workbook
from magasin.products import create_product, get_product
from magasin.stock import list_movements, stock_of
from tests.helpers import new_db


HEADERS = (
    "CODE PRODUIT", "DÉSIGNATION", "FOURNISSEUR", "CATÉGORIE", "DATE ENTRÉE",
    "DERN. VENTE", "PRIX ACHAT\n(DA)", "PRIX VENTE\n(DA)", "MARGE %",
    "QTÉ INITIALE", "QTÉ VENDUE", "STOCK RESTANT", "SEUIL ALERTE",
)


class ImportExcelTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def workbook_path(self, rows):
        path = Path(self.temp_dir.name) / "stock.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SUIVI_STOCK"
        for column, header in enumerate(HEADERS, start=1):
            sheet.cell(row=7, column=column, value=header)
        for row_number, values in enumerate(rows, start=8):
            for column, value in enumerate(values, start=1):
                sheet.cell(row=row_number, column=column, value=value)
        workbook.save(path)
        workbook.close()
        return path

    @staticmethod
    def product_row(reference, name="Produit test", cost=1000, price=1500, stock=4,
                    minimum=2, supplier="Fournisseur test", category="Accessoires"):
        row = [None] * len(HEADERS)
        row[0] = reference
        row[1] = name
        row[2] = supplier
        row[3] = category
        row[6] = cost
        row[7] = price
        row[11] = stock
        row[12] = minimum
        return row

    def test_preview_maps_stock_and_excludes_duplicates_existing_and_invalid_rows(self):
        create_product(self.conn, "EXISTING", "Produit déjà présent", 200_000, initial_stock=7)
        path = self.workbook_path([
            self.product_row("NEW-1"),
            self.product_row("NEW-1", name="Doublon"),
            self.product_row("existing"),
            self.product_row("NO-PRICE", price=None),
            self.product_row(None),
        ])

        preview = preview_stock_workbook(self.conn, path)

        self.assertEqual(preview.count("À importer"), 1)
        self.assertEqual(preview.count("Doublon du fichier"), 1)
        self.assertEqual(preview.count("Déjà dans le catalogue"), 1)
        self.assertEqual(preview.count("À vérifier"), 2)
        imported = preview.importable_rows[0]
        self.assertEqual(
            (imported.reference, imported.name, imported.category, imported.cost_centimes,
             imported.price_centimes, imported.stock, imported.min_stock),
            ("NEW-1", "Produit test", "Accessoires", 100_000, 150_000, 4, 2),
        )

    def test_import_creates_product_and_audited_opening_stock_without_history(self):
        path = self.workbook_path([self.product_row("NEW-2", stock=6)])
        preview = preview_stock_workbook(self.conn, path)

        count = import_stock_workbook(self.conn, preview)

        product = self.conn.execute(
            "SELECT * FROM products WHERE reference = 'NEW-2'"
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual((product["name"], product["category"], product["cost_centimes"],
                          product["price_centimes"], product["tax_rate_bp"],
                          product["price_includes_tax"], product["min_stock"]),
                         ("Produit test", "Accessoires", 100_000, 150_000, 1900, 1, 2))
        self.assertEqual(stock_of(self.conn, product["id"]), 6)
        movements = list_movements(self.conn, product["id"])
        self.assertEqual([(movement["movement_type"], movement["quantity"]) for movement in movements],
                         [("inventaire", 6)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchase_receipts").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'import_excel_stock'"
            ).fetchone()[0],
            1,
        )

    def test_import_refuses_a_catalogue_changed_after_preview(self):
        path = self.workbook_path([
            self.product_row("FIRST"),
            self.product_row("SECOND"),
        ])
        preview = preview_stock_workbook(self.conn, path)
        create_product(self.conn, "SECOND", "Créé entre-temps", 10_000)

        with self.assertRaises(ValidationError):
            import_stock_workbook(self.conn, preview)

        self.assertIsNone(self.conn.execute(
            "SELECT id FROM products WHERE reference = 'FIRST'"
        ).fetchone())
        self.assertEqual(get_product(
            self.conn,
            self.conn.execute("SELECT id FROM products WHERE reference = 'SECOND'").fetchone()["id"],
        )["name"], "Créé entre-temps")

    def test_decimal_amounts_and_blank_alert_threshold_are_read_correctly(self):
        path = self.workbook_path([
            self.product_row("DA-1", cost="1 234,50 DA", price="2.345,67", stock="3,0",
                             minimum=None),
        ])

        imported = preview_stock_workbook(self.conn, path).importable_rows[0]

        self.assertEqual(imported.cost_centimes, 123_450)
        self.assertEqual(imported.price_centimes, 234_567)
        self.assertEqual((imported.stock, imported.min_stock), (3, 0))

    def test_missing_sheet_or_headers_is_reported(self):
        path = Path(self.temp_dir.name) / "wrong.xlsx"
        workbook = Workbook()
        workbook.active.title = "Autre feuille"
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValidationError, "SUIVI_STOCK"):
            preview_stock_workbook(self.conn, path)

        path = self.workbook_path([])
        workbook = Workbook()
        workbook.active.title = "SUIVI_STOCK"
        workbook.active["A1"] = "Entêtes inconnues"
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValidationError, "colonnes nécessaires"):
            preview_stock_workbook(self.conn, path)


if __name__ == "__main__":
    unittest.main()

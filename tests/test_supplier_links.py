import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from magasin.errors import ValidationError
from magasin.db import connect
from magasin.products import create_product
from magasin.purchases import ReceiptLine, create_supplier, receive_stock, set_supplier_active
from magasin.sales import Line, PaymentIn, create_sale
from magasin.stock import stock_of
from magasin.cash import open_session
from magasin.supplier_links import (
    apply_supplier_links,
    preview_supplier_links,
    set_product_supplier,
)
from tests.helpers import new_db


class SupplierLinksTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def workbook_path(self, rows):
        path = Path(self.temp_dir.name) / "fournisseurs.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SUIVI_STOCK"
        sheet.cell(row=4, column=1, value="CODE PRODUIT")
        sheet.cell(row=4, column=2, value="FOURNISSEUR")
        for row_number, values in enumerate(rows, start=5):
            sheet.cell(row=row_number, column=1, value=values[0])
            sheet.cell(row=row_number, column=2, value=values[1])
        workbook.save(path)
        workbook.close()
        return path

    def test_preview_requires_exact_product_reference_and_existing_supplier(self):
        product_id = create_product(self.conn, "SKU-1", "Produit", 15_000)
        first_supplier = create_supplier(self.conn, None, "Fournisseur A")
        second_supplier = create_supplier(self.conn, None, "Fournisseur B")
        self.conn.execute(
            "INSERT INTO product_suppliers (product_id, supplier_id, created_at) "
            "VALUES (?, ?, ?)",
            (product_id, first_supplier, "2026-11-02T10:00:00+01:00"),
        )
        path = self.workbook_path([
            ("SKU-1", "Fournisseur A"),
            ("SKU-1", "Fournisseur A"),
            ("sku-1", "Fournisseur B"),
            ("SKU-1", "Fournisseur inconnu"),
            ("SKU-1", "Fournisseur B"),
        ])

        preview = preview_supplier_links(self.conn, path)

        self.assertEqual(preview.count("Déjà associé"), 1)
        self.assertEqual(preview.count("Doublon du fichier"), 1)
        self.assertEqual(preview.count("Référence inconnue"), 1)
        self.assertEqual(preview.count("Fournisseur inconnu"), 1)
        self.assertEqual(len(preview.linkable_rows), 1)
        self.assertEqual(preview.linkable_rows[0].supplier_id, second_supplier)

    def test_apply_adds_only_links_and_preserves_stock_and_all_history(self):
        product_id = create_product(
            self.conn, "SKU-HIST", "Produit avec historique", 15_000,
            cost_centimes=5_000, initial_stock=3,
        )
        receive_stock(self.conn, [ReceiptLine(product_id, 2)], "Fournisseur historique")
        open_session(self.conn, opening_centimes=100_000)
        create_sale(
            self.conn,
            [Line(product_id)],
            [PaymentIn("ccp", 15_000)],
        )
        supplier_id = create_supplier(self.conn, None, "Fournisseur Excel")
        path = self.workbook_path([("SKU-HIST", "Fournisseur Excel")])
        preview = preview_supplier_links(self.conn, path)
        tables = (
            "products", "stock_movements", "sales", "sale_items", "sale_payments",
            "purchase_receipts", "purchase_receipt_items", "supplier_payments",
            "cash_sessions", "cash_movements", "document_sequences",
        )
        before = {
            table: [tuple(row) for row in self.conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

        count = apply_supplier_links(
            self.conn, preview, at="2026-11-02T11:00:00+01:00"
        )

        self.assertEqual(count, 1)
        self.assertEqual(stock_of(self.conn, product_id), 4)
        self.assertEqual(
            [
                tuple(row) for row in self.conn.execute(
                    "SELECT product_id, supplier_id FROM product_suppliers"
                )
            ],
            [(product_id, supplier_id)],
        )
        for table in tables:
            with self.subTest(table=table):
                after = [
                    tuple(row) for row in self.conn.execute(f"SELECT * FROM {table}")
                ]
                self.assertEqual(after, before[table])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE action = 'fournisseurs_associes_produits'"
            ).fetchone()[0],
            1,
        )

    def test_stale_preview_fails_without_partial_associations(self):
        product_id = create_product(self.conn, "SKU-STALE", "Produit", 15_000)
        supplier_id = create_supplier(self.conn, None, "Fournisseur actif")
        path = self.workbook_path([("SKU-STALE", "Fournisseur actif")])
        preview = preview_supplier_links(self.conn, path)
        set_supplier_active(self.conn, None, supplier_id, False)

        with self.assertRaisesRegex(ValidationError, "ont changé depuis l’aperçu"):
            apply_supplier_links(self.conn, preview)

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product_suppliers WHERE product_id = ?",
                (product_id,),
            ).fetchone()[0],
            0,
        )

    def test_workbook_without_supplier_column_is_rejected(self):
        path = Path(self.temp_dir.name) / "sans-fournisseur.xlsx"
        workbook = Workbook()
        workbook.active.title = "SUIVI_STOCK"
        workbook.active["A1"] = "CODE PRODUIT"
        workbook.save(path)
        workbook.close()

        with self.assertRaisesRegex(ValidationError, "FOURNISSEUR"):
            preview_supplier_links(self.conn, path)

    def test_existing_version_8_database_migrates_without_losing_supplier_data(self):
        path = Path(self.temp_dir.name) / "existing.db"
        conn = connect(str(path))
        product_id = create_product(conn, "MIGRATION-1", "Produit", 15_000)
        supplier_id = create_supplier(conn, None, "Fournisseur conservé")
        conn.execute("DROP TABLE product_suppliers")
        conn.execute("DROP TABLE loyalty_movements")
        conn.execute("DROP TABLE supplier_adjustments")
        conn.execute("ALTER TABLE purchase_receipts DROP COLUMN status")
        conn.execute("ALTER TABLE purchase_receipt_items DROP COLUMN unit_price_centimes")
        conn.execute("ALTER TABLE sales DROP COLUMN loyalty_points_redeemed")
        conn.execute("PRAGMA user_version = 8")
        conn.close()

        migrated = connect(str(path))
        self.addCleanup(migrated.close)

        self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 14)
        self.assertEqual(
            migrated.execute(
                "SELECT name FROM suppliers WHERE id = ?", (supplier_id,)
            ).fetchone()["name"],
            "Fournisseur conservé",
        )
        self.assertEqual(
            migrated.execute(
                "SELECT reference FROM products WHERE id = ?", (product_id,)
            ).fetchone()["reference"],
            "MIGRATION-1",
        )
        self.assertEqual(
            migrated.execute("SELECT COUNT(*) FROM product_suppliers").fetchone()[0],
            0,
        )

    def test_direct_assignment_replaces_or_clears_links_without_touching_stock(self):
        product_id = create_product(
            self.conn, "DIRECT-1", "Produit", 15_000, initial_stock=3
        )
        first_id = create_supplier(self.conn, None, "Fournisseur direct A")
        second_id = create_supplier(self.conn, None, "Fournisseur direct B")
        set_product_supplier(self.conn, product_id, first_id)
        movements_before = [
            tuple(row) for row in self.conn.execute(
                "SELECT * FROM stock_movements ORDER BY id"
            )
        ]

        set_product_supplier(self.conn, product_id, second_id)

        links = [
            tuple(row) for row in self.conn.execute(
                "SELECT product_id, supplier_id FROM product_suppliers"
            )
        ]
        self.assertEqual(links, [(product_id, second_id)])
        self.assertEqual(stock_of(self.conn, product_id), 3)
        self.assertEqual(
            [
                tuple(row) for row in self.conn.execute(
                    "SELECT * FROM stock_movements ORDER BY id"
                )
            ],
            movements_before,
        )

        set_product_supplier(self.conn, product_id, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_suppliers").fetchone()[0],
            0,
        )
        self.assertEqual(stock_of(self.conn, product_id), 3)

    def test_archived_supplier_cannot_be_assigned_to_another_product(self):
        product_id = create_product(self.conn, "DIRECT-2", "Produit", 15_000)
        supplier_id = create_supplier(self.conn, None, "Fournisseur archivé")
        set_supplier_active(self.conn, None, supplier_id, False)

        with self.assertRaisesRegex(ValidationError, "archivé"):
            set_product_supplier(self.conn, product_id, supplier_id)

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_suppliers").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.cash import open_session, suggested_opening
from magasin.customers import create_customer, customer_debt
from magasin.errors import AuthenticationError, PermissionDenied
from magasin.loyalty import available_points
from magasin.products import create_product
from magasin.purchases import ReceiptLine, receive_stock, supplier_balances
from magasin.returns import ReturnLine, create_return
from magasin.reinitialisation import reinitialiser_historique
from magasin.sales import Line, PaymentIn, create_sale
from magasin.stock import stock_of
from magasin.users import create_first_admin, create_user
from tests.helpers import new_db


class ReinitialisationTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.password = "MotDePasseAdminTest"
        self.admin_id = create_first_admin(self.conn, "admin-reset", self.password)
        self.product_id = create_product(
            self.conn, "RST-001", "Produit conservé", 10_000,
            initial_stock=5, actor_user_id=self.admin_id,
        )
        self.customer_id = create_customer(
            self.conn, "Client conservé", "0555000001", credit_allowed=True
        )
        open_session(self.conn, opening_centimes=5_000, actor_user_id=self.admin_id)
        sale = create_sale(
            self.conn,
            [Line(self.product_id, quantity=2)],
            [PaymentIn("especes", 20_000)],
            customer_id=self.customer_id,
            actor_user_id=self.admin_id,
            at="2026-11-07T10:00:00+01:00",
        )
        session_id = self.conn.execute(
            "SELECT id FROM cash_sessions WHERE status = 'open'"
        ).fetchone()["id"]
        quote_id = self.conn.execute(
            "INSERT INTO quotes "
            "(number, customer_id, recipient_name, discount_centimes, total_centimes, "
            "status, created_at, valid_until, converted_sale_id) "
            "VALUES ('DEV-TEST-001', ?, 'Client conservé', 0, 20000, 'converted', "
            "'2026-11-07', '2026-12-07', ?)",
            (self.customer_id, sale.sale_id),
        ).lastrowid
        self.conn.execute("UPDATE sales SET quote_id = ? WHERE id = ?", (quote_id, sale.sale_id))
        self.conn.execute(
            "INSERT INTO quote_items "
            "(quote_id, product_id, reference, name, quantity, unit_price_centimes, "
            "tax_rate_bp, total_centimes) VALUES (?, ?, 'RST-001', 'Produit conservé', "
            "2, 10000, 1900, 20000)",
            (quote_id, self.product_id),
        )
        payment_id = self.conn.execute(
            "INSERT INTO customer_payments "
            "(number, customer_id, method, amount_centimes, session_id, created_at) "
            "VALUES ('REC-TEST-001', ?, 'virement', 1000, ?, '2026-11-07')",
            (self.customer_id, session_id),
        ).lastrowid
        plan_id = self.conn.execute(
            "INSERT INTO payment_plans "
            "(sale_id, customer_id, principal_centimes, installment_count, first_due_date, created_at) "
            "VALUES (?, ?, 10000, 1, '2026-12-07', '2026-11-07')",
            (sale.sale_id, self.customer_id),
        ).lastrowid
        installment_id = self.conn.execute(
            "INSERT INTO installments (plan_id, number, due_date, amount_centimes) "
            "VALUES (?, 1, '2026-12-07', 10000)",
            (plan_id,),
        ).lastrowid
        self.conn.execute(
            "INSERT INTO payment_allocations "
            "(payment_id, sale_id, installment_id, amount_centimes) VALUES (?, ?, ?, 1000)",
            (payment_id, sale.sale_id, installment_id),
        )
        self.conn.execute(
            "INSERT INTO cheques "
            "(customer_id, installment_id, number, amount_centimes, due_date, created_at) "
            "VALUES (?, ?, 'CHQ-TEST-001', 10000, '2026-12-07', '2026-11-07')",
            (self.customer_id, installment_id),
        )
        self.conn.execute(
            "INSERT INTO customer_credit_movements "
            "(customer_id, amount_centimes, sale_id, created_at) "
            "VALUES (?, 1000, ?, '2026-11-07')",
            (self.customer_id, sale.sale_id),
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1, resalable=True)],
            "especes", "Retour de test", actor_user_id=self.admin_id,
        )
        receive_stock(
            self.conn,
            [ReceiptLine(self.product_id, 2, unit_cost_centimes=5_000)],
            "Fournisseur conservé",
            actor_user_id=self.admin_id,
        )
        self.supplier_id = self.conn.execute(
            "SELECT id FROM suppliers WHERE name = 'Fournisseur conservé'"
        ).fetchone()["id"]
        receipt_id = self.conn.execute(
            "SELECT id FROM purchase_receipts ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO supplier_payments "
            "(receipt_id, method, amount_centimes, cash_session_id, created_at) "
            "VALUES (?, 'virement', 1000, ?, '2026-11-07')",
            (receipt_id, session_id),
        )
        self.conn.execute(
            "INSERT INTO supplier_adjustments "
            "(supplier_id, receipt_id, amount_centimes, reason, created_at) "
            "VALUES (?, ?, 1000, 'Dette de test', '2026-11-07')",
            (self.supplier_id, receipt_id),
        )
        self.conn.execute(
            "INSERT INTO customer_adjustments "
            "(customer_id, amount_centimes, reason, created_at) "
            "VALUES (?, 2000, 'Créance de test', '2026-11-07')",
            (self.customer_id,),
        )
        self.conn.execute(
            "UPDATE customers SET status = 'en_retard' WHERE id = ?",
            (self.customer_id,),
        )

    def test_efface_historique_et_conserve_references_et_stock(self):
        stock_avant = stock_of(self.conn, self.product_id)
        self.assertEqual(stock_avant, 6)
        self.assertGreater(available_points(self.conn, self.customer_id), 0)
        self.assertTrue(supplier_balances(self.conn))

        reinitialiser_historique(
            self.conn, self.admin_id, self.password, at="2026-11-08T10:00:00+01:00"
        )

        self.assertEqual(stock_of(self.conn, self.product_id), stock_avant)
        self.assertEqual(customer_debt(self.conn, self.customer_id), 0)
        self.assertEqual(available_points(self.conn, self.customer_id), 0)
        self.assertEqual(supplier_balances(self.conn), [])
        self.assertEqual(suggested_opening(self.conn), 0)
        self.assertEqual(
            self.conn.execute("SELECT status FROM customers WHERE id = ?", (self.customer_id,)).fetchone()[0],
            "actif",
        )
        for table in (
            "sales", "sale_items", "sale_payments", "sale_returns", "sale_return_items",
            "customer_payments", "customer_adjustments", "customer_credit_movements",
            "loyalty_movements", "cash_sessions", "cash_movements", "purchase_receipts",
            "purchase_receipt_items", "supplier_payments", "supplier_adjustments",
            "payment_allocations", "payment_plans", "installments", "cheques",
            "quotes", "quote_items",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                )

        for table, identity in (
            ("products", self.product_id),
            ("customers", self.customer_id),
            ("suppliers", self.supplier_id),
            ("users", self.admin_id),
        ):
            self.assertIsNotNone(
                self.conn.execute(f"SELECT id FROM {table} WHERE id = ?", (identity,)).fetchone()
            )
        movements = self.conn.execute(
            "SELECT movement_type, quantity, qty_before, qty_after FROM stock_movements "
            "WHERE product_id = ?",
            (self.product_id,),
        ).fetchall()
        self.assertEqual(len(movements), 1)
        self.assertEqual(tuple(movements[0]), ("inventaire", 6, 0, 6))
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(), []
        )
        self.assertEqual(
            self.conn.execute("SELECT action FROM audit_log").fetchall()[0][0],
            "historique_ventes_reinitialise",
        )

    def test_mot_de_passe_incorrect_ne_modifie_rien(self):
        with self.assertRaises(AuthenticationError):
            reinitialiser_historique(self.conn, self.admin_id, "mot de passe incorrect")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_un_compte_non_administrateur_ne_peut_pas_reinitialiser(self):
        responsable_id = create_user(
            self.conn, self.admin_id, "responsable-reset", "MotDePasseResponsable",
            "responsable",
        )
        with self.assertRaises(PermissionDenied):
            reinitialiser_historique(self.conn, responsable_id, "MotDePasseResponsable")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class ReinitialisationInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_sauvegarde import PageSauvegarde

        self.conn = new_db()
        self.addCleanup(self.conn.close)
        self.admin_id = create_first_admin(
            self.conn, "admin-ui-reset", "MotDePasseAdminUIReset"
        )
        self.after_reset = Mock()
        self.page = PageSauvegarde(
            self.conn, Mock(), actor_user_id=self.admin_id,
            after_reset=self.after_reset,
        )
        self.assertFalse(self.page.bouton_reinitialiser.isHidden())

    def test_confirmation_et_mot_de_passe_requis_avant_reinitialisation(self):
        with (
            patch(
                "interface.page_sauvegarde.QMessageBox.question",
                return_value=QMessageBox.No,
            ),
            patch("interface.page_sauvegarde.QInputDialog.getText") as demande_mot_de_passe,
        ):
            self.page.reinitialiser_historique()
        demande_mot_de_passe.assert_not_called()
        self.after_reset.assert_not_called()

    def test_mot_de_passe_administrateur_valide_l_action(self):
        with (
            patch(
                "interface.page_sauvegarde.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch(
                "interface.page_sauvegarde.QInputDialog.getText",
                return_value=("MotDePasseAdminUIReset", True),
            ),
            patch("interface.page_sauvegarde.QMessageBox.information"),
        ):
            self.page.reinitialiser_historique()
        self.after_reset.assert_called_once_with()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 1)

    def test_action_absente_sans_session_administrateur(self):
        from interface.page_sauvegarde import PageSauvegarde

        page = PageSauvegarde(self.conn, Mock())
        self.assertTrue(page.bouton_reinitialiser.isHidden())


if __name__ == "__main__":
    unittest.main()

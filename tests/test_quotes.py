import unittest
from datetime import timedelta

from magasin.clock import today
from magasin.customers import create_customer
from magasin.errors import PermissionDenied, ValidationError
from magasin.products import create_product, update_product
from magasin.quotes import QuoteLine, cancel_quote, create_quote, get_quote, list_quotes
from magasin.sales import Line, PaymentIn, create_sale
from magasin.stock import stock_of
from magasin.users import create_first_admin, create_user
from tests.helpers import new_db


class DevisTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "DEV-001", "Produit devis", 10_000,
            cost_centimes=5_000, initial_stock=3,
        )
        self.customer_id = create_customer(self.conn, "Client devis", "0555000004")

    def _creer_devis(self, *, at=None, discount=0):
        return create_quote(
            self.conn,
            [QuoteLine(self.product_id, 2)],
            recipient_name="Client devis",
            customer_id=self.customer_id,
            discount_centimes=discount,
            at=at,
        )

    def test_creation_du_devis_ne_change_ni_stock_ni_vente(self):
        stock = stock_of(self.conn, self.product_id)

        quote = self._creer_devis()

        self.assertEqual(quote["number"], "DEV-000001")
        self.assertEqual(
            quote["valid_until"],
            (today() + timedelta(days=30)).isoformat(),
        )
        self.assertEqual(quote["total_centimes"], 20_000)
        self.assertEqual(get_quote(self.conn, quote["id"])["lines"][0]["quantity"], 2)
        self.assertEqual(stock_of(self.conn, self.product_id), stock)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sale_payments").fetchone()[0], 0)

    def test_conversion_utilise_le_prix_du_devis_meme_si_le_prix_catalogue_change(self):
        quote = self._creer_devis()
        update_product(self.conn, self.product_id, price_centimes=12_000)

        sale = create_sale(
            self.conn, [Line(self.product_id, 2, 10_000)],
            [PaymentIn("ccp", 20_000)], quote_id=quote["id"],
        )

        self.assertEqual(sale.total_centimes, 20_000)
        saved = get_quote(self.conn, quote["id"])
        self.assertEqual(saved["status"], "converted")
        self.assertEqual(saved["converted_sale_id"], sale.sale_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT quote_id FROM sales WHERE id = ?", (sale.sale_id,)
            ).fetchone()["quote_id"],
            quote["id"],
        )

    def test_conversion_refuse_une_modification_des_lignes_et_reste_atomique(self):
        quote = self._creer_devis()

        with self.assertRaises(ValidationError):
            create_sale(
                self.conn, [Line(self.product_id, 1)],
                [PaymentIn("ccp", 10_000)], quote_id=quote["id"],
            )

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(get_quote(self.conn, quote["id"])["status"], "open")

    def test_conversion_refuse_un_devis_expire(self):
        quote = self._creer_devis(at="2026-01-01T10:00:00+01:00")

        with self.assertRaises(ValidationError):
            create_sale(
                self.conn, [Line(self.product_id, 2, 10_000)],
                [PaymentIn("ccp", 20_000)], quote_id=quote["id"],
            )

        self.assertEqual(get_quote(self.conn, quote["id"])["status"], "open")

    def test_annulation_conserve_le_devis_et_interdit_la_conversion(self):
        quote = self._creer_devis()
        cancel_quote(self.conn, quote["id"])

        self.assertEqual(get_quote(self.conn, quote["id"])["status"], "cancelled")
        self.assertEqual(list_quotes(self.conn, open_only=True), [])
        with self.assertRaises(ValidationError):
            create_sale(
                self.conn, [Line(self.product_id, 2, 10_000)],
                [PaymentIn("ccp", 20_000)], quote_id=quote["id"],
            )

    def test_remise_sur_devis_reste_reservee_aux_responsables(self):
        admin_id = create_first_admin(self.conn, "admin-dev", "MotDePasseAdmin")
        cashier_id = create_user(
            self.conn, admin_id, "caisse-dev", "MotDePasseCaisse", "caissier"
        )

        with self.assertRaises(PermissionDenied):
            create_quote(
                self.conn, [QuoteLine(self.product_id, 1)],
                recipient_name="Client devis", customer_id=self.customer_id,
                discount_centimes=100, actor_user_id=cashier_id,
            )


if __name__ == "__main__":
    unittest.main()

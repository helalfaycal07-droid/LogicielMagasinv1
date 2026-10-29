import unittest

from magasin.cash import expected_balance
from magasin.db import connect
from magasin.errors import (CreditNotAllowed, InsufficientStock, NoOpenSession, PaymentInsufficient,
                            ValidationError)
from magasin.integrity import check_all
from magasin.money import format_da
from magasin.sales import Line, PaymentIn, create_sale, sale_summary
from magasin.settings import set_setting
from magasin.stock import list_movements, stock_of
from tests.helpers import TV_PRICE, credit_customer, new_db, open_cash, tv


class VenteAuComptantTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product = tv(self.conn, stock=3)
        self.session = open_cash(self.conn, opening=1_500_000)  # 15 000 DA

    def test_vente_espèces_avec_rendu_de_monnaie(self):
        r = create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", 4_000_000)])
        self.assertEqual(r.number, "VTE-000001")
        self.assertEqual((r.total_centimes, r.paid_centimes, r.due_centimes), (TV_PRICE, TV_PRICE, 0))
        self.assertEqual(r.change_centimes, 100_000)  # 1 000,00 DA rendus
        # seule la valeur de la vente entre dans la caisse, pas le rendu
        self.assertEqual(expected_balance(self.conn, self.session), 1_500_000 + TV_PRICE)
        self.assertEqual(format_da(expected_balance(self.conn, self.session)), "54 000,00 DA")
        # le stock passe de 3 à 2, mouvement de type « vente » avec la référence
        self.assertEqual(stock_of(self.conn, self.product), 2)
        mouvement = list_movements(self.conn, self.product)[-1]
        self.assertEqual((mouvement["movement_type"], mouvement["qty_before"], mouvement["qty_after"]),
                         ("vente", 3, 2))
        self.assertEqual(mouvement["ref_number"], "VTE-000001")
        self.assertEqual(check_all(self.conn), [])

    def test_paiement_partiel_refuse_sans_client_a_credit(self):
        with self.assertRaises(PaymentInsufficient) as ctx:
            create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", 2_000_000)])
        self.assertIn("19 000,00 DA", str(ctx.exception))
        # rien n'a été écrit : ni vente, ni mouvement de stock
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(stock_of(self.conn, self.product), 3)

    def test_vente_en_especes_sans_session_ouverte(self):
        conn = new_db()
        produit = tv(conn)
        with self.assertRaises(NoOpenSession):
            create_sale(conn, [Line(produit)], [PaymentIn("especes", TV_PRICE)])

    def test_le_rendu_ne_vient_que_des_especes(self):
        with self.assertRaises(ValidationError):
            create_sale(self.conn, [Line(self.product)], [PaymentIn("ccp", 4_000_000)])

    def test_paiement_ccp_n_entre_pas_dans_le_tiroir_caisse(self):
        create_sale(self.conn, [Line(self.product)], [PaymentIn("ccp", TV_PRICE)])
        self.assertEqual(expected_balance(self.conn, self.session), 1_500_000)
        self.assertEqual(stock_of(self.conn, self.product), 2)


class StockInsuffisantTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product = tv(self.conn, stock=0)
        open_cash(self.conn)

    def test_par_defaut_on_avertit_et_le_stock_devient_negatif(self):
        r = create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", TV_PRICE)])
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(stock_of(self.conn, self.product), -1)
        mouvement = list_movements(self.conn, self.product)[-1]
        self.assertEqual(mouvement["movement_type"], "vente")  # jamais « ajustement »
        self.assertEqual(check_all(self.conn), [])

    def test_reglage_bloquer(self):
        set_setting(self.conn, "sale_when_insufficient", "block")
        with self.assertRaises(InsufficientStock):
            create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", TV_PRICE)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(stock_of(self.conn, self.product), 0)

    def test_reglage_autoriser(self):
        set_setting(self.conn, "sale_when_insufficient", "allow")
        r = create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", TV_PRICE)])
        self.assertEqual(r.warnings, [])
        self.assertEqual(stock_of(self.conn, self.product), -1)


class CreditAutoriseTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product = tv(self.conn, stock=3)
        open_cash(self.conn)

    def test_credit_refuse_si_client_non_autorise(self):
        from magasin.customers import create_customer
        client = create_customer(self.conn, "Client sans crédit", "0555000001")
        with self.assertRaises(CreditNotAllowed):
            create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", 1_000_000)], customer_id=client)

    def test_plafond_de_credit(self):
        client = credit_customer(self.conn, credit_limit_centimes=2_000_000)  # plafond 20 000 DA
        with self.assertRaises(CreditNotAllowed):
            create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", 1_000_000)], customer_id=client)

    def test_resume_a_quatre_montants(self):
        client = credit_customer(self.conn)
        r = create_sale(self.conn, [Line(self.product)], [PaymentIn("especes", 1_500_000)], customer_id=client)
        s = sale_summary(self.conn, r.sale_id)
        self.assertEqual((s["total"], s["paye_a_la_vente"], s["paye_depuis"], s["reste_du"]),
                         (TV_PRICE, 1_500_000, 0, 2_400_000))


if __name__ == "__main__":
    unittest.main()

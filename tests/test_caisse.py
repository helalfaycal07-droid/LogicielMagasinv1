import unittest

from magasin.cash import (close_session, current_session, expected_balance, open_session, record_expense,
                          session_summary, suggested_opening)
from magasin.errors import DifferenceNeedsReason, SessionAlreadyOpen, ValidationError
from magasin.integrity import check_all
from magasin.money import format_da
from magasin.sales import Line, PaymentIn, create_sale
from tests.helpers import credit_customer, new_db, tv


class CaisseTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()

    def test_une_seule_session_ouverte_a_la_fois(self):
        open_session(self.conn, opening_centimes=0)
        with self.assertRaises(SessionAlreadyOpen):
            open_session(self.conn, opening_centimes=0)

    def test_ecart_exige_une_raison_ecrite(self):
        session = open_session(self.conn, opening_centimes=0)
        produit = tv(self.conn)
        client = credit_customer(self.conn)
        create_sale(self.conn, [Line(produit)], [PaymentIn("especes", 1_000_000)], customer_id=client)  # acompte 10 000 DA
        self.assertEqual(expected_balance(self.conn, session), 1_000_000)
        with self.assertRaises(DifferenceNeedsReason):
            close_session(self.conn, session, counted_centimes=900_000)  # il manque 1 000 DA
        self.assertIsNotNone(current_session(self.conn))               # toujours ouverte
        resultat = close_session(self.conn, session, counted_centimes=900_000, reason="Erreur de rendu de monnaie")
        self.assertEqual(resultat["difference"], -100_000)
        self.assertIsNone(current_session(self.conn))

    def test_le_solde_de_cloture_devient_l_ouverture_proposee(self):
        session = open_session(self.conn, opening_centimes=0)
        close_session(self.conn, session, counted_centimes=0)
        self.assertEqual(suggested_opening(self.conn), 0)
        session = open_session(self.conn, opening_centimes=1_500_000)
        close_session(self.conn, session, counted_centimes=1_500_000)
        reouverture = open_session(self.conn)  # sans montant : reprend la clôture précédente
        self.assertEqual(expected_balance(self.conn, reouverture), 1_500_000)

    def test_depense_reduit_le_solde_attendu(self):
        session = open_session(self.conn, opening_centimes=5_400_000)  # 54 000 DA
        record_expense(self.conn, session, 100_000, "Achat de sacs")
        self.assertEqual(format_da(expected_balance(self.conn, session)), "53 000,00 DA")
        summary = session_summary(self.conn, session)
        self.assertEqual(summary["by_kind"]["depense"], -100_000)

    def test_la_caisse_ne_devient_pas_negative(self):
        session = open_session(self.conn, opening_centimes=50_000)
        with self.assertRaises(ValidationError):
            record_expense(self.conn, session, 100_000, "Trop grand")

    def test_piste_d_audit_coherente(self):
        session = open_session(self.conn, opening_centimes=100_000)
        record_expense(self.conn, session, 40_000, "Test")
        close_session(self.conn, session, counted_centimes=60_000)
        self.assertEqual(check_all(self.conn), [])


if __name__ == "__main__":
    unittest.main()

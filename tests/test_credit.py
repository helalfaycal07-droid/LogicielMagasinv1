import unittest
from datetime import date

from magasin.cash import expected_balance, session_summary
from magasin.credit import (add_cheques_for_plan, add_months, create_plan, list_installments,
                            record_payment, refresh_customer_status, set_cheque_status)
from magasin.customers import (add_opening_balance, create_customer, customer_debt, get_customer,
                               receivables, set_status)
from magasin.errors import InvalidTransition, OverPayment, ValidationError
from magasin.integrity import check_all
from magasin.money import format_da
from magasin.sales import Line, PaymentIn, create_sale, sale_summary
from tests.helpers import credit_customer, new_db, open_cash, tv

PREMIERE_ECHEANCE = date(2026, 11, 22)


class VenteACreditTests(unittest.TestCase):
    """Cas de l'essai : vente 38 000 DA, acompte 10 000 DA, versement 5 000 DA."""

    def setUp(self):
        self.conn = new_db()
        self.produit = tv(self.conn, stock=3)
        # prix de la vente de l'essai : 38 000 DA
        self.session = open_cash(self.conn, opening=0)
        self.client = credit_customer(self.conn)
        self.vente = create_sale(self.conn, [Line(self.produit, 1, unit_price_centimes=3_800_000)],
                                 [PaymentIn("especes", 1_000_000)], customer_id=self.client)

    def test_acompte_reste_du_et_versement(self):
        self.assertEqual(self.vente.due_centimes, 2_800_000)
        self.assertEqual(customer_debt(self.conn, self.client), 2_800_000)
        record_payment(self.conn, self.client, 500_000)
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "23 000,00 DA")
        # la caisse attend acompte + versement
        self.assertEqual(format_da(expected_balance(self.conn, self.session)), "15 000,00 DA")
        par_nature = session_summary(self.conn, self.session)["by_kind"]
        self.assertEqual(par_nature, {"acompte": 1_000_000, "paiement_client": 500_000})
        self.assertEqual(check_all(self.conn), [])

    def test_versement_superieur_a_la_dette_refuse(self):
        with self.assertRaises(OverPayment):
            record_payment(self.conn, self.client, 2_800_001)

    def test_qui_me_doit_de_l_argent(self):
        self.assertEqual(receivables(self.conn),
                         [{"customer_id": self.client, "name": "Client Test", "status": "actif",
                           "debt_centimes": 2_800_000}])

    def test_reprise_de_dette_ancienne(self):
        add_opening_balance(self.conn, self.client, 1_000_000, "Dette reprise de l'ancien cahier")
        self.assertEqual(customer_debt(self.conn, self.client), 3_800_000)
        record_payment(self.conn, self.client, 3_800_000)
        self.assertEqual(customer_debt(self.conn, self.client), 0)

    def test_statut_client_exige_une_raison(self):
        with self.assertRaises(ValidationError):
            set_status(self.conn, self.client, "bloque", "")
        set_status(self.conn, self.client, "bloque", "Chèques impayés")
        self.assertEqual(get_customer(self.conn, self.client)["status"], "bloque")


class EcheancierTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.produit = tv(self.conn, stock=3)
        self.session = open_cash(self.conn, opening=0)
        self.client = credit_customer(self.conn)
        self.vente = create_sale(self.conn, [Line(self.produit, 1, unit_price_centimes=3_800_000)],
                                 [PaymentIn("especes", 1_000_000)], customer_id=self.client)

    def plan(self, n):
        return create_plan(self.conn, self.vente.sale_id, n, PREMIERE_ECHEANCE)

    def test_dates_mensuelles_et_fin_de_mois(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        plan = self.plan(8)
        dates = [i["due_date"] for i in list_installments(self.conn, plan_id=plan, today=date(2026, 10, 22))]
        self.assertEqual(dates[0], "2026-11-22")
        self.assertEqual(dates[-1], "2027-06-22")

    def test_huit_mensualites_de_3500(self):
        plan = self.plan(8)
        montants = [i["amount_centimes"] for i in list_installments(self.conn, plan_id=plan)]
        self.assertEqual(montants, [350_000] * 8)

    def test_dix_mensualites_de_2800(self):
        plan = self.plan(10)
        montants = [i["amount_centimes"] for i in list_installments(self.conn, plan_id=plan)]
        self.assertEqual(montants, [280_000] * 10)

    def test_neuf_et_six_mensualites_avec_arrondi(self):
        plan = self.plan(9)
        montants = [i["amount_centimes"] for i in list_installments(self.conn, plan_id=plan)]
        self.assertEqual(montants, [311_111] * 8 + [311_112])
        self.assertEqual(sum(montants), 2_800_000)

    def test_versement_partiel_sur_huit_mensualites(self):
        plan = self.plan(8)
        record_payment(self.conn, self.client, 400_000, today=date(2026, 10, 22))  # 4 000 DA
        inst = list_installments(self.conn, plan_id=plan, today=date(2026, 10, 22))
        self.assertEqual(inst[0]["status"], "payee")
        self.assertEqual((inst[1]["status"], inst[1]["reste_centimes"]), ("partielle", 300_000))
        self.assertEqual(inst[2]["status"], "a_venir")
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "24 000,00 DA")

    def test_acompte_et_deux_mensualites_payees(self):
        self.plan(8)
        record_payment(self.conn, self.client, 700_000, today=date(2026, 10, 22))
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "21 000,00 DA")
        resume = sale_summary(self.conn, self.vente.sale_id)
        self.assertEqual((resume["paye_a_la_vente"], resume["paye_depuis"], resume["reste_du"]),
                         (1_000_000, 700_000, 2_100_000))

    def test_pas_deux_echeanciers_pour_une_vente(self):
        self.plan(6)
        with self.assertRaises(ValidationError):
            self.plan(6)


class ChequesTests(unittest.TestCase):
    """Un chèque par mensualité (hypothèse retenue : 6 chèques en général)."""

    def setUp(self):
        self.conn = new_db()
        self.produit = tv(self.conn, stock=3)
        self.session = open_cash(self.conn, opening=0)
        self.client = credit_customer(self.conn)
        vente = create_sale(self.conn, [Line(self.produit, 1, unit_price_centimes=3_800_000)],
                            [PaymentIn("especes", 1_000_000)], customer_id=self.client)
        self.plan = create_plan(self.conn, vente.sale_id, 8, PREMIERE_ECHEANCE)
        self.cheques = add_cheques_for_plan(self.conn, self.plan, first_number=1001, bank="Banque test")

    def statut(self, cheque_id):
        return self.conn.execute("SELECT status FROM cheques WHERE id = ?", (cheque_id,)).fetchone()["status"]

    def test_un_cheque_par_mensualite(self):
        self.assertEqual(len(self.cheques), 8)
        montants = [r["amount_centimes"] for r in self.conn.execute("SELECT amount_centimes FROM cheques")]
        self.assertEqual(montants, [350_000] * 8)

    def test_un_cheque_en_dossier_ne_reduit_pas_la_dette(self):
        self.assertEqual(customer_debt(self.conn, self.client), 2_800_000)

    def test_encaissement_cree_le_versement(self):
        c1 = self.cheques[0]
        set_cheque_status(self.conn, c1, "remis_banque")
        set_cheque_status(self.conn, c1, "encaisse", today=date(2026, 11, 25))
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "24 500,00 DA")
        premiere = list_installments(self.conn, plan_id=self.plan, today=date(2026, 11, 25))[0]
        self.assertEqual(premiere["status"], "payee")
        # un chèque n'entre pas dans le tiroir-caisse
        self.assertEqual(expected_balance(self.conn, self.session), 1_000_000)

    def test_cheque_impaye_met_l_echeance_en_retard_et_le_client_en_retard(self):
        for index in (0, 1):  # chèques 1 et 2 encaissés
            set_cheque_status(self.conn, self.cheques[index], "remis_banque")
            set_cheque_status(self.conn, self.cheques[index], "encaisse", today=date(2027, 1, 5))
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "21 000,00 DA")
        c3 = self.cheques[2]
        set_cheque_status(self.conn, c3, "remis_banque")
        set_cheque_status(self.conn, c3, "impaye", today=date(2027, 1, 23))
        inst = list_installments(self.conn, plan_id=self.plan, today=date(2027, 1, 23))
        self.assertEqual(inst[2]["status"], "en_retard")
        self.assertEqual(get_customer(self.conn, self.client)["status"], "en_retard")
        self.assertEqual(format_da(customer_debt(self.conn, self.client)), "21 000,00 DA")  # inchangée
        self.assertTrue(self.conn.execute("SELECT 1 FROM audit_log WHERE action = 'cheque_statut'").fetchone())

    def test_le_client_redevient_actif_quand_le_retard_est_regle(self):
        c1 = self.cheques[0]
        set_cheque_status(self.conn, c1, "remis_banque")
        set_cheque_status(self.conn, c1, "impaye", today=date(2026, 11, 23))
        self.assertEqual(get_customer(self.conn, self.client)["status"], "en_retard")
        record_payment(self.conn, self.client, 350_000, method="especes", today=date(2026, 11, 24))
        self.assertEqual(get_customer(self.conn, self.client)["status"], "actif")
        self.assertEqual(self.statut(c1), "a_restituer")  # payé en espèces : le chèque est à rendre

    def test_paiement_en_especes_d_une_echeance_couverte_par_un_cheque(self):
        inst4 = self.conn.execute("SELECT id FROM installments WHERE plan_id = ? AND number = 4",
                                  (self.plan,)).fetchone()["id"]
        record_payment(self.conn, self.client, 350_000, method="especes", installment_id=inst4,
                       today=date(2026, 11, 1))
        self.assertEqual(self.statut(self.cheques[3]), "a_restituer")
        self.assertEqual(self.statut(self.cheques[0]), "en_dossier")
        set_cheque_status(self.conn, self.cheques[3], "restitue")
        self.assertEqual(self.statut(self.cheques[3]), "restitue")

    def test_transitions_interdites(self):
        with self.assertRaises(InvalidTransition):
            set_cheque_status(self.conn, self.cheques[0], "encaisse")  # pas passé par la banque
        set_cheque_status(self.conn, self.cheques[0], "remis_banque")
        set_cheque_status(self.conn, self.cheques[0], "encaisse", today=date(2026, 11, 25))
        with self.assertRaises(InvalidTransition):
            set_cheque_status(self.conn, self.cheques[0], "restitue")


if __name__ == "__main__":
    unittest.main()

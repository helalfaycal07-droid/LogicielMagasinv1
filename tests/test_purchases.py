import unittest

from magasin.cash import expected_balance, open_session
from magasin.errors import ValidationError
from magasin.integrity import check_all
from magasin.products import create_product
from magasin.purchases import (ReceiptLine, receive_stock, record_supplier_payment,
                               cancel_purchase_receipt, get_purchase_receipt,
                               create_supplier, list_suppliers,
                               record_supplier_payment_for_supplier, supplier_balances,
                               supplier_receipt_balances, update_purchase_receipt)
from magasin.serials import add_units, complete_imei, count_in_stock, get_unit
from magasin.stock import add_movement, list_movements, stock_of
from tests.helpers import new_db


class ReceptionFournisseurTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.produit = create_product(
            self.conn, "A-001", "Produit de test", 15_000, initial_stock=2
        )

    def test_reception_augmente_le_stock_et_trace_le_fournisseur(self):
        numero = receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 4)],
            "Fournisseur Test",
            supplier_reference="FAC-123",
            at="2026-11-01T10:00:00+01:00",
        )

        self.assertEqual(numero, "REC-000001")
        self.assertEqual(
            self.conn.execute(
                "SELECT created_at FROM purchase_receipts WHERE number = ?", (numero,)
            ).fetchone()["created_at"],
            "2026-11-01T10:00:00+01:00",
        )
        self.assertEqual(stock_of(self.conn, self.produit), 6)
        mouvement = list_movements(self.conn, self.produit)[-1]
        self.assertEqual(mouvement["movement_type"], "reception_achat")
        self.assertEqual(mouvement["quantity"], 4)
        self.assertEqual(mouvement["qty_before"], 2)
        self.assertEqual(mouvement["qty_after"], 6)
        self.assertEqual(mouvement["ref_number"], numero)
        self.assertEqual(mouvement["counterparty"], "Fournisseur Test")
        self.assertIn("FAC-123", mouvement["note"])
        self.assertEqual(check_all(self.conn), [])

    def test_reception_imei_cree_les_appareils_et_les_mouvements(self):
        telephone = create_product(
            self.conn, "T-001", "Téléphone", 100_000, track_serial=True
        )
        imei1 = complete_imei("35999900000001")
        imei2 = complete_imei("35999900000002")

        numero = receive_stock(
            self.conn,
            [ReceiptLine(telephone, 2, [(imei1, None), (imei2, None)])],
            "Fournisseur Téléphones",
        )

        self.assertEqual(numero, "REC-000001")
        self.assertEqual(stock_of(self.conn, telephone), 2)
        self.assertEqual(count_in_stock(self.conn, telephone), 2)
        self.assertEqual(get_unit(self.conn, imei1)["status"], "en_stock")
        mouvements = list_movements(self.conn, telephone)
        self.assertEqual([m["ref_number"] for m in mouvements], [numero, numero])
        self.assertEqual(
            [m["counterparty"] for m in mouvements],
            ["Fournisseur Téléphones", "Fournisseur Téléphones"],
        )
        self.assertEqual(check_all(self.conn), [])

    def test_reception_est_atomique_si_un_imei_est_manquant(self):
        telephone = create_product(
            self.conn, "T-001", "Téléphone", 100_000, track_serial=True
        )

        with self.assertRaises(ValidationError):
            receive_stock(
                self.conn,
                [ReceiptLine(self.produit, 3), ReceiptLine(telephone, 1)],
                "Fournisseur Test",
            )

        self.assertEqual(stock_of(self.conn, self.produit), 2)
        self.assertEqual(stock_of(self.conn, telephone), 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'reception_achat'"
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM document_sequences WHERE code = 'REC'"
        ).fetchone()[0], 0)

    def test_fournisseur_lignes_et_quantites_sont_obligatoires(self):
        essais = (
            ([], "Fournisseur Test"),
            ([ReceiptLine(self.produit, 0)], "Fournisseur Test"),
            ([ReceiptLine(self.produit, 1)], "  "),
        )
        for lignes, fournisseur in essais:
            with self.subTest(lignes=lignes, fournisseur=fournisseur):
                with self.assertRaises(ValidationError):
                    receive_stock(self.conn, lignes, fournisseur)

        self.assertEqual(stock_of(self.conn, self.produit), 2)

    def test_un_imei_existant_est_refuse_et_rollback(self):
        telephone = create_product(
            self.conn, "T-001", "Téléphone", 100_000, track_serial=True
        )
        imei = complete_imei("35999900000001")
        add_units(self.conn, telephone, [(imei, None)])

        with self.assertRaises(ValidationError):
            receive_stock(
                self.conn,
                [ReceiptLine(telephone, 1, [(imei, None)])],
                "Fournisseur Test",
            )

        self.assertEqual(stock_of(self.conn, telephone), 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM document_sequences WHERE code = 'REC'"
        ).fetchone()[0], 0)

    def test_reception_enregistre_le_cout_et_cree_le_solde_fournisseur(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        numero = receive_stock(
            self.conn,
            [ReceiptLine(produit, 3, unit_cost_centimes=25_000)],
            "Fournisseur Test",
            supplier_reference="FAC-9",
        )

        solde, = supplier_receipt_balances(self.conn)
        self.assertEqual(numero, solde["number"])
        self.assertEqual(solde["total_centimes"], 75_000)
        self.assertEqual(solde["paid_centimes"], 0)
        self.assertEqual(solde["due_centimes"], 75_000)
        self.assertEqual(
            self.conn.execute(
                "SELECT cost_centimes FROM products WHERE id = ?", (produit,)
            ).fetchone()["cost_centimes"],
            25_000,
        )

    def test_reception_produit_existant_met_a_jour_stock_cout_et_prix_vente(self):
        produit = create_product(
            self.conn, "EXIST-1", "Produit existant", 30_000,
            cost_centimes=12_000, initial_stock=4,
        )

        receive_stock(
            self.conn,
            [ReceiptLine(
                produit, 3, unit_cost_centimes=15_000, unit_price_centimes=35_000
            )],
            "Fournisseur Test",
        )

        self.assertEqual(stock_of(self.conn, produit), 7)
        details = self.conn.execute(
            "SELECT cost_centimes, price_centimes FROM products WHERE id = ?", (produit,)
        ).fetchone()
        self.assertEqual(details["cost_centimes"], 15_000)
        self.assertEqual(details["price_centimes"], 35_000)
        item = self.conn.execute(
            "SELECT unit_price_centimes FROM purchase_receipt_items"
        ).fetchone()
        self.assertEqual(item["unit_price_centimes"], 35_000)

    def test_correction_reception_ajuste_stock_date_prix_et_solde(self):
        supplier = "Fournisseur correction"
        produit = create_product(
            self.conn, "EDIT-1", "Produit corrigé", 30_000, cost_centimes=12_000
        )
        number = receive_stock(
            self.conn,
            [ReceiptLine(
                produit, 2, unit_cost_centimes=15_000, unit_price_centimes=35_000
            )],
            supplier,
            payment_centimes=5_000,
            payment_method="virement",
            at="2026-11-01T12:00:00+01:00",
        )
        receipt = get_purchase_receipt(
            self.conn,
            self.conn.execute(
                "SELECT id FROM purchase_receipts WHERE number = ?", (number,)
            ).fetchone()["id"],
        )

        update_purchase_receipt(
            self.conn, receipt["id"],
            [ReceiptLine(
                produit, 3, unit_cost_centimes=17_000, unit_price_centimes=40_000
            )],
            supplier,
            at="2026-11-04T12:00:00+01:00",
        )

        receipt = get_purchase_receipt(self.conn, receipt["id"])
        balance, = supplier_receipt_balances(self.conn)
        self.assertEqual(stock_of(self.conn, produit), 3)
        self.assertEqual(receipt["created_at"], "2026-11-04T12:00:00+01:00")
        self.assertEqual(receipt["items"][0]["unit_price_centimes"], 40_000)
        self.assertEqual(balance["total_centimes"], 51_000)
        self.assertEqual(balance["paid_centimes"], 5_000)
        self.assertEqual(balance["due_centimes"], 46_000)

    def test_annulation_garde_historique_ajuste_stock_et_retire_dette_globale(self):
        supplier_id = create_supplier(self.conn, None, "Fournisseur annulé")
        number = receive_stock(
            self.conn, [ReceiptLine(self.produit, 2)], "Fournisseur annulé"
        )
        add_movement(self.conn, self.produit, -3, "vente")
        receipt_id = self.conn.execute(
            "SELECT id FROM purchase_receipts WHERE number = ?", (number,)
        ).fetchone()["id"]

        cancel_purchase_receipt(self.conn, receipt_id)

        receipt = get_purchase_receipt(self.conn, receipt_id)
        supplier, = list_suppliers(self.conn)
        self.assertEqual(stock_of(self.conn, self.produit), 0)
        self.assertEqual(receipt["status"], "annulee")
        self.assertEqual(supplier["id"], supplier_id)
        self.assertEqual(supplier["total_centimes"], 0)
        self.assertEqual(supplier["due_centimes"], 0)
        self.assertEqual(supplier_receipt_balances(self.conn)[0]["due_centimes"], 0)

    def test_annulation_avec_acompte_conserve_un_credit_fournisseur(self):
        create_supplier(self.conn, None, "Fournisseur avec acompte")
        number = receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 1, unit_cost_centimes=5_000)],
            "Fournisseur avec acompte",
            payment_centimes=2_000,
            payment_method="virement",
        )
        receipt_id = self.conn.execute(
            "SELECT id FROM purchase_receipts WHERE number = ?", (number,)
        ).fetchone()["id"]

        cancel_purchase_receipt(self.conn, receipt_id)

        self.assertEqual(
            list_suppliers(self.conn)[0]["due_centimes"], -2_000
        )
        self.assertEqual(supplier_balances(self.conn)[0]["due_centimes"], -2_000)
        with self.assertRaises(ValidationError):
            record_supplier_payment_for_supplier(
                self.conn,
                self.conn.execute(
                    "SELECT supplier_id FROM purchase_receipts WHERE id = ?", (receipt_id,)
                ).fetchone()["supplier_id"],
                1,
                "virement",
            )

    def test_versement_fournisseur_est_reparti_sur_les_dettes_anciennes(self):
        supplier_id = create_supplier(self.conn, None, "Fournisseur global")
        receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 1, unit_cost_centimes=5_000)],
            "Fournisseur global",
        )
        receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 2, unit_cost_centimes=5_000)],
            "Fournisseur global",
        )

        self.assertEqual(supplier_balances(self.conn)[0]["due_centimes"], 15_000)
        record_supplier_payment_for_supplier(
            self.conn, supplier_id, 7_000, "virement"
        )

        balances = supplier_receipt_balances(self.conn)
        self.assertEqual([row["paid_centimes"] for row in balances], [2_000, 5_000])
        self.assertEqual(supplier_balances(self.conn)[0]["due_centimes"], 8_000)

    def test_credit_dune_reception_corrigee_est_deduit_des_versements_globaux(self):
        supplier = "Fournisseur avec crédit"
        number = receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 1, unit_cost_centimes=5_000)],
            supplier,
            payment_centimes=4_000,
            payment_method="virement",
        )
        receipt_id = self.conn.execute(
            "SELECT id FROM purchase_receipts WHERE number = ?", (number,)
        ).fetchone()["id"]
        update_purchase_receipt(
            self.conn, receipt_id,
            [ReceiptLine(self.produit, 1, unit_cost_centimes=3_000)],
            supplier,
        )
        receive_stock(
            self.conn,
            [ReceiptLine(self.produit, 1, unit_cost_centimes=10_000)],
            supplier,
        )
        supplier_id = self.conn.execute(
            "SELECT supplier_id FROM purchase_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()["supplier_id"]

        with self.assertRaises(ValidationError):
            record_supplier_payment_for_supplier(
                self.conn, supplier_id, 10_000, "virement"
            )
        record_supplier_payment_for_supplier(
            self.conn, supplier_id, 9_000, "virement"
        )

        self.assertEqual(supplier_balances(self.conn)[0]["due_centimes"], 0)

    def test_reglement_especes_reduit_la_caisse_et_la_dette(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        session_id = open_session(self.conn, opening_centimes=100_000)
        receive_stock(
            self.conn,
            [ReceiptLine(produit, 3, unit_cost_centimes=25_000)],
            "Fournisseur Test",
        )
        solde, = supplier_receipt_balances(self.conn)

        record_supplier_payment(self.conn, solde["id"], 30_000, "especes")

        solde, = supplier_receipt_balances(self.conn)
        self.assertEqual(solde["paid_centimes"], 30_000)
        self.assertEqual(solde["due_centimes"], 45_000)
        self.assertEqual(expected_balance(self.conn, session_id), 70_000)

    def test_acompte_especes_peut_etre_enregistre_avec_la_reception(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        session_id = open_session(self.conn, opening_centimes=100_000)

        receive_stock(
            self.conn,
            [ReceiptLine(produit, 3, unit_cost_centimes=25_000)],
            "Fournisseur Test",
            payment_centimes=30_000,
            payment_method="especes",
        )

        solde, = supplier_receipt_balances(self.conn)
        self.assertEqual(solde["paid_centimes"], 30_000)
        self.assertEqual(solde["due_centimes"], 45_000)
        self.assertEqual(expected_balance(self.conn, session_id), 70_000)

    def test_acompte_especes_sans_caisse_annule_la_reception(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )

        with self.assertRaises(ValidationError):
            receive_stock(
                self.conn,
                [ReceiptLine(produit, 1, unit_cost_centimes=25_000)],
                "Fournisseur Test",
                payment_centimes=10_000,
                payment_method="especes",
            )

        self.assertEqual(stock_of(self.conn, produit), 0)
        self.assertEqual(supplier_receipt_balances(self.conn), [])

    def test_echec_du_reglement_initial_annule_toute_la_reception(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        receive_stock(
            self.conn,
            [ReceiptLine(produit, 1, unit_cost_centimes=25_000)],
            "Fournisseur Test",
        )
        solde, = supplier_receipt_balances(self.conn)

        with self.assertRaises(ValidationError):
            receive_stock(
                self.conn,
                [ReceiptLine(produit, 2, unit_cost_centimes=25_000)],
                "Fournisseur Test",
                payment_centimes=60_000,
            )

        self.assertEqual(stock_of(self.conn, produit), 1)
        self.assertEqual(len(supplier_receipt_balances(self.conn)), 1)

    def test_reglement_especes_sans_caisse_ouverte_est_refuse(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        receive_stock(
            self.conn,
            [ReceiptLine(produit, 1, unit_cost_centimes=25_000)],
            "Fournisseur Test",
        )
        solde, = supplier_receipt_balances(self.conn)

        with self.assertRaises(ValidationError):
            record_supplier_payment(self.conn, solde["id"], 10_000, "especes")

        solde, = supplier_receipt_balances(self.conn)
        self.assertEqual(solde["paid_centimes"], 0)

    def test_reglement_partiel_par_virement_et_depassement_refuse(self):
        produit = create_product(
            self.conn, "COUT-1", "Article acheté", 100_000, cost_centimes=20_000
        )
        receive_stock(
            self.conn,
            [ReceiptLine(produit, 2, unit_cost_centimes=25_000)],
            "Fournisseur Test",
        )
        solde, = supplier_receipt_balances(self.conn)
        record_supplier_payment(self.conn, solde["id"], 15_000, "virement")
        with self.assertRaises(ValidationError):
            record_supplier_payment(self.conn, solde["id"], 35_001, "cheque")

        solde, = supplier_receipt_balances(self.conn)
        self.assertEqual(solde["due_centimes"], 35_000)


if __name__ == "__main__":
    unittest.main()

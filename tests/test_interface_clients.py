"""Tests de l'écran Clients et crédit (sans écran physique)."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.customers import create_customer, customer_debt, get_customer
from magasin.money import format_da
from magasin.products import create_product
from magasin.sales import Line, PaymentIn, create_sale
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageClientsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_clients import PageClients

        self.conn = new_db()
        self.client_id = create_customer(
            self.conn,
            "Client Test",
            "0555000000",
            address="Alger",
            credit_allowed=True,
        )
        produit_id = create_product(
            self.conn, "CL-001", "Produit crédit", 390_000, initial_stock=2
        )
        self.vente = create_sale(
            self.conn,
            [Line(produit_id)],
            [PaymentIn("ccp", 100_000)],
            customer_id=self.client_id,
        )
        self.page = PageClients(self.conn)

    def choisir_client(self):
        self.page.tableau_clients.selectRow(0)

    def test_selection_affiche_facture_et_dette_calculee(self):
        self.choisir_client()

        self.assertEqual(self.page.tableau_clients.rowCount(), 1)
        self.assertEqual(self.page.dette.text(), "Dette actuelle : 2 900,00 DA")
        self.assertEqual(self.page.tableau_factures.rowCount(), 1)
        self.assertEqual(self.page.tableau_factures.item(0, 0).text(), self.vente.number)
        self.assertEqual(self.page.tableau_factures.item(0, 4).text(), "2 900,00 DA")
        self.assertEqual(self.page.tableau_clients.item(0, 3).text(), "39")
        self.assertEqual(self.page.points_fidelite.text(), "Points fidélité disponibles : 39")

    def test_impression_de_l_etiquette_80x50_avec_les_donnees_du_dossier(self):
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize

        client_id = create_customer(
            self.conn, "Client <A&B>", "0555000022"
        )
        self.page.rafraichir()
        self.page.tableau_clients.selectRow(self.page._ligne_client(client_id))
        client = get_customer(self.conn, client_id)
        printer = patch("interface.page_clients.QPrinter").start()
        printer.return_value.setPageLayout.return_value = True
        printer.return_value.pageLayout.return_value = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape,
            QMarginsF(2, 2, 2, 2),
            QPageLayout.Unit.Millimeter,
        )
        dialogue = patch("interface.page_clients.QPrintDialog").start()
        dialogue.return_value.exec.return_value = QDialog.DialogCode.Accepted
        document = patch("interface.page_clients.QTextDocument").start()
        try:
            self.page.bouton_etiquette.click()
        finally:
            patch.stopall()

        layout = printer.return_value.setPageLayout.call_args.args[0]
        dimensions = layout.pageSize().size(QPageSize.Unit.Millimeter)
        self.assertAlmostEqual(dimensions.width(), 80)
        self.assertAlmostEqual(dimensions.height(), 50)
        self.assertEqual(layout.orientation(), QPageLayout.Orientation.Landscape)
        html = document.return_value.setHtml.call_args.args[0]
        self.assertIn("Client &lt;A&amp;B&gt;", html)
        self.assertIn(client["dossier_number"], html)
        self.assertIn("0555000022", html)
        document.return_value.print_.assert_called_once_with(printer.return_value)

    def test_recherche_par_nom_ou_telephone(self):
        self.page.recherche.setText("Client")
        self.assertEqual(self.page.tableau_clients.rowCount(), 1)
        self.page.recherche.setText("055500")
        self.assertEqual(self.page.tableau_clients.rowCount(), 1)
        self.page.recherche.setText("inconnu")
        self.assertEqual(self.page.tableau_clients.rowCount(), 0)
        self.assertIsNone(self.page.client_id)

    def test_versement_met_a_jour_dette_et_historique(self):
        self.choisir_client()
        self.page.montant_versement.setText("500")
        self.page.mode_versement.setCurrentIndex(self.page.mode_versement.findData("ccp"))
        self.page.note_versement.setText("Versement test")
        self.page.bouton_versement.click()

        self.assertEqual(customer_debt(self.conn, self.client_id), 240_000)
        self.assertEqual(self.page.tableau_clients.item(0, 2).text(), format_da(240_000))
        self.assertEqual(self.page.dette.text(), "Dette actuelle : 2 400,00 DA")
        self.assertEqual(self.page.tableau_versements.rowCount(), 1)
        self.assertEqual(self.page.tableau_versements.item(0, 3).text(), "500,00 DA")
        self.assertEqual(self.page.tableau_versements.item(0, 4).text(), "Versement test")

    def test_modifier_les_coordonnees_est_audite(self):
        from interface.page_clients import ClientDialogue

        self.choisir_client()
        dialogue = ClientDialogue(self.page, get_customer(self.conn, self.client_id))
        dialogue.nom.setText("Client Modifié")
        dialogue.telephone.setText("0666000000")
        dialogue.adresse.setText("Oran")
        dialogue._valider()
        self.assertEqual(dialogue.valeurs["phone"], "0666000000")

        with (
            patch("interface.page_clients.ClientDialogue", return_value=dialogue),
            patch("interface.page_clients.ouvrir_dialogue", return_value=QDialog.Accepted),
        ):
            self.page.modifier_client()

        client = get_customer(self.conn, self.client_id)
        self.assertEqual((client["name"], client["phone"], client["address"]),
                         ("Client Modifié", "0666000000", "Oran"))
        self.assertEqual(
            self.conn.execute(
                "SELECT action FROM audit_log WHERE entity = 'customers' AND entity_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.client_id,),
            ).fetchone()["action"],
            "client_modifie",
        )

    def test_creation_et_modification_gerent_l_autorisation_de_credit(self):
        from interface.page_clients import ClientDialogue

        nouveau = ClientDialogue(self.page)
        self.assertFalse(nouveau.credit_autorise.isChecked())
        nouveau.nom.setText("Client crédit UI")
        nouveau.telephone.setText("0555000011")
        nouveau.credit_autorise.setChecked(True)
        nouveau._valider()
        with (
            patch("interface.page_clients.ClientDialogue", return_value=nouveau),
            patch("interface.page_clients.ouvrir_dialogue", return_value=QDialog.Accepted),
        ):
            self.page.ajouter_client()

        created = self.conn.execute(
            "SELECT id FROM customers WHERE name = ?", ("Client crédit UI",)
        ).fetchone()
        client_id = created["id"]
        self.assertEqual(get_customer(self.conn, client_id)["credit_allowed"], 1)

        self.page.tableau_clients.selectRow(self.page._ligne_client(client_id))
        modification = ClientDialogue(self.page, get_customer(self.conn, client_id))
        self.assertTrue(modification.credit_autorise.isChecked())
        modification.credit_autorise.setChecked(False)
        modification._valider()
        with (
            patch("interface.page_clients.ClientDialogue", return_value=modification),
            patch("interface.page_clients.ouvrir_dialogue", return_value=QDialog.Accepted),
        ):
            self.page.modifier_client()
        self.assertEqual(get_customer(self.conn, client_id)["credit_allowed"], 0)

        self.page.tableau_clients.selectRow(self.page._ligne_client(client_id))
        modification = ClientDialogue(self.page, get_customer(self.conn, client_id))
        modification.credit_autorise.setChecked(True)
        modification._valider()
        with (
            patch("interface.page_clients.ClientDialogue", return_value=modification),
            patch("interface.page_clients.ouvrir_dialogue", return_value=QDialog.Accepted),
        ):
            self.page.modifier_client()
        self.assertEqual(get_customer(self.conn, client_id)["credit_allowed"], 1)

        product_id = self.conn.execute(
            "SELECT product_id FROM sale_items WHERE sale_id = ?", (self.vente.sale_id,)
        ).fetchone()["product_id"]
        vente_credit = create_sale(
            self.conn,
            [Line(product_id)],
            [PaymentIn("ccp", 100_000)],
            customer_id=client_id,
        )
        self.assertEqual(vente_credit.due_centimes, 290_000)

    def test_supprimer_un_client_avec_historique_l_archive(self):
        self.choisir_client()
        with (
            patch("interface.page_clients.demander_confirmation", return_value=True),
            patch("interface.page_clients.QMessageBox.information"),
        ):
            self.page.bouton_supprimer.click()

        client = get_customer(self.conn, self.client_id)
        self.assertEqual(client["status"], "archive")
        self.assertEqual(client["credit_allowed"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT customer_id FROM sales WHERE id = ?", (self.vente.sale_id,)
            ).fetchone()["customer_id"],
            self.client_id,
        )
        self.assertEqual(self.page.tableau_clients.rowCount(), 0)
        self.assertIsNone(self.page.client_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT action FROM audit_log WHERE entity = 'customers' AND entity_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.client_id,),
            ).fetchone()["action"],
            "client_archive",
        )

    def test_supprimer_un_client_sans_historique_efface_sa_fiche(self):
        client_id = create_customer(self.conn, "Client sans historique", "0555000001")
        self.page.rafraichir()
        ligne = self.page._ligne_client(client_id)
        self.assertGreaterEqual(ligne, 0)
        self.page.tableau_clients.selectRow(ligne)

        with (
            patch("interface.page_clients.demander_confirmation", return_value=True),
            patch("interface.page_clients.QMessageBox.information"),
        ):
            self.page.bouton_supprimer.click()

        self.assertIsNone(get_customer(self.conn, client_id))
        self.assertEqual(
            self.conn.execute(
                "SELECT action FROM audit_log WHERE entity = 'customers' AND entity_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (client_id,),
            ).fetchone()["action"],
            "client_supprime",
        )

    def test_annuler_confirmation_ne_supprime_pas_le_client(self):
        self.choisir_client()
        with patch("interface.page_clients.demander_confirmation", return_value=False):
            self.page.bouton_supprimer.click()

        self.assertIsNotNone(get_customer(self.conn, self.client_id))
        self.assertEqual(self.page.tableau_clients.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()

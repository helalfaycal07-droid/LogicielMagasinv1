"""Tests du ticket imprimable de la vente rapide."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.customers import create_customer
from magasin.clock import format_dt
from magasin.money import format_da
from magasin.products import create_product
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import add_units, complete_imei
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class TicketVenteRapideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_vente_rapide import PageVenteRapide

        self.conn = new_db()
        self.client_id = create_customer(
            self.conn, "Client Ticket", "0555000000", credit_allowed=True
        )
        self.produit_id = create_product(
            self.conn, "T-001", "Produit & spécial", 1_000_000, initial_stock=2
        )
        self.page = PageVenteRapide(self.conn)
        self.page.panier = [{
            "product_id": self.produit_id,
            "reference": "T-001",
            "name": "Produit & spécial",
            "quantity": 1,
            "unit_price_centimes": 1_000_000,
            "imeis": [],
        }]
        self.page.paiements = [PaymentIn("ccp", 5_000)]
        self.page.client.setCurrentIndex(self.page.client.findData(self.client_id))

    def test_validation_active_un_ticket_complet_avec_credit(self):
        self.assertFalse(self.page.bouton_imprimer.isEnabled())
        with (
            patch("interface.page_vente_rapide.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
            patch.object(QMessageBox, "warning"),
        ):
            self.page.valider_vente()

        self.assertTrue(self.page.bouton_imprimer.isEnabled())
        ticket_donnees = self.page._dernier_ticket
        self.assertIsNotNone(ticket_donnees)
        assert ticket_donnees is not None
        ticket, _hauteur = ticket_donnees
        created_at = self.conn.execute(
            "SELECT created_at FROM sales ORDER BY id DESC LIMIT 1"
        ).fetchone()["created_at"]
        self.assertIn("تجهيز دارك", ticket)
        self.assertIn("Tadjhiz Darek — Médéa", ticket)
        self.assertIn("max-width: 80mm", ticket)
        self.assertIn("margin: 0 auto", ticket)
        self.assertIn("<colgroup>", ticket)
        self.assertIn('<col style="width: 46%">', ticket)
        self.assertIn("border-bottom: 1px dashed", ticket)
        self.assertIn("Produit &amp; spécial", ticket)
        self.assertIn("10 000,00 DA", ticket)
        self.assertIn("Montant reçu", ticket)
        self.assertIn(format_da(5_000), ticket)
        self.assertIn("Crédit", ticket)
        self.assertIn(format_dt(created_at), ticket)
        self.assertTrue(self.page.btn_imprimer_facture.isEnabled())
        self.assertTrue(self.page.btn_bon_livraison.isEnabled())
        self.assertIsNotNone(self.page._derniere_vente_id)
        self.assertFalse(self.page.btn_imprimer_proforma.isEnabled())

    def test_points_client_affiches_utilises_a_la_caisse_et_imprimes_sur_le_ticket(self):
        create_sale(
            self.conn,
            [Line(self.produit_id)],
            [],
            customer_id=self.client_id,
        )
        self.page.rafraichir()
        self.assertIn("100 point(s)", self.page.solde_points.text())
        self.page.points_fidelite.setValue(10)
        self.assertIn("-10,00 DA", self.page.resume.text())

        with (
            patch("interface.page_vente_rapide.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
            patch.object(QMessageBox, "warning"),
        ):
            self.page.valider_vente()

        sale = self.conn.execute(
            "SELECT id, total_centimes, loyalty_points_redeemed "
            "FROM sales ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(sale["total_centimes"], 999_000)
        self.assertEqual(sale["loyalty_points_redeemed"], 10)
        self.assertIn("Fidélité (10 points)", self.page._dernier_ticket[0])
        self.assertIn("Points gagnés", self.page._dernier_ticket[0])

    def test_facture_a4_reprend_vente_client_remise_et_paiement(self):
        vente = create_sale(
            self.conn,
            [Line(self.produit_id)],
            [PaymentIn("ccp", 500_000)],
            customer_id=self.client_id,
            discount_centimes=100_000,
        )

        html = self.page._construire_facture_a4_html(vente.sale_id)

        self.assertIn("FACTURE", html)
        self.assertIn(vente.number, html)
        self.assertIn("Helal Cristor Store", html)
        self.assertIn("Bazar El Mokhbat, Thniet El Hadjar", html)
        self.assertIn("RC : 26/00-175185A13", html)
        self.assertIn("NIF : 17226010281710702600", html)
        self.assertIn("faycal-7205@hotmail.fr", html)
        self.assertIn("007999990019243799/08", html)
        self.assertIn("Client Ticket", html)
        self.assertIn("CLI-", html)
        self.assertIn("T-001", html)
        self.assertIn("Produit &amp; spécial", html)
        self.assertIn('<table class="articles">', html)
        self.assertIn("<th>Description</th>", html)
        self.assertIn("<th>Total TTC</th>", html)
        self.assertIn("border: 1px solid #aebdc5", html)
        self.assertIn("Prix unitaire HT", html)
        self.assertIn("Total TVA", html)
        self.assertIn("Total TTC avant remise", html)
        self.assertIn("10 000,00 DA", html)
        self.assertIn("9 000,00 DA", html)
        self.assertIn("7 563,03 DA", html)
        self.assertIn("1 436,97 DA", html)
        self.assertIn("Remise", html)
        self.assertIn("-1 000,00 DA", html)
        self.assertIn("CCP", html)
        self.assertIn("Montant payé", html)
        self.assertIn("4 000,00 DA", html)
        self.assertIn("Détails bancaires", html)
        self.assertIn("Compte : 007999990019243799/08", html)
        self.assertNotIn("+213", html)
        self.assertNotIn("12345678", html)

    def test_proforma_depuis_panier_sans_enregistrer_vente_ni_stock(self):
        from magasin.stock import stock_of

        self.page._mettre_a_jour_boutons()
        stock_avant = stock_of(self.conn, self.produit_id)
        html = self.page._construire_proforma_html("PRO-TEST")

        self.assertTrue(self.page.btn_imprimer_proforma.isEnabled())
        self.assertIn("FACTURE PROFORMA", html)
        self.assertIn("PRO-TEST", html)
        self.assertIn("Client Ticket", html)
        self.assertIn("Produit &amp; spécial", html)
        self.assertIn("Total proposé", html)
        self.assertIn("10 000,00 DA", html)
        self.assertIn("Cette proforma ne constitue pas une facture", html)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(stock_of(self.conn, self.produit_id), stock_avant)

    def test_bouton_proforma_ouvre_l_impression_du_panier(self):
        self.page._mettre_a_jour_boutons()
        destinataire = {
            "name": "Nouveau client & société",
            "phone": "+213 555 00 00 00",
            "address": "Rue de Test <Bloc A>",
        }
        dialogue = Mock(valeurs=destinataire)
        with (
            patch("interface.page_vente_rapide.ProformaDestinataireDialogue",
                  return_value=dialogue) as creer_dialogue,
            patch("interface.page_vente_rapide.ouvrir_dialogue", return_value=QDialog.DialogCode.Accepted),
            patch.object(self.page, "_imprimer_document_a4") as imprimer,
        ):
            self.page.btn_imprimer_proforma.click()

        creer_dialogue.assert_called_once()
        contenu, titre = imprimer.call_args.args
        self.assertIn("FACTURE PROFORMA", contenu)
        self.assertIn("PRO-000001", contenu)
        self.assertIn("Nouveau client &amp; société", contenu)
        self.assertIn("+213 555 00 00 00", contenu)
        self.assertIn("Rue de Test &lt;Bloc A&gt;", contenu)
        self.assertEqual(titre, "Imprimer la facture proforma")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

    def test_annuler_destinataire_ne_consomme_pas_de_numero_proforma(self):
        dialogue = Mock(valeurs=None)
        with (
            patch("interface.page_vente_rapide.ProformaDestinataireDialogue",
                  return_value=dialogue),
            patch("interface.page_vente_rapide.ouvrir_dialogue", return_value=QDialog.DialogCode.Rejected),
            patch.object(self.page, "_imprimer_document_a4") as imprimer,
        ):
            self.page.imprimer_proforma()

        imprimer.assert_not_called()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM document_sequences WHERE code = 'PRO'"
            ).fetchone()[0],
            0,
        )

    def test_bon_de_livraison_reprend_les_articles_sans_prix(self):
        vente = create_sale(
            self.conn,
            [Line(self.produit_id)],
            [PaymentIn("ccp", 1_000_000)],
            customer_id=self.client_id,
        )

        html = self.page._construire_bon_livraison_html(vente.sale_id)

        self.assertIn("BON DE LIVRAISON", html)
        self.assertIn(f"BL-{vente.number}", html)
        self.assertIn("Client Ticket", html)
        self.assertIn("Produit &amp; spécial", html)
        self.assertIn("<th>Quantité</th>", html)
        self.assertIn("<td class='centre'>1</td>", html)
        self.assertIn("Signature et cachet du vendeur", html)
        self.assertIn("Nom, date et signature du client", html)
        self.assertNotIn("Prix unitaire", html)
        self.assertNotIn("Total TTC", html)

    def test_bon_de_livraison_affiche_les_imeis_des_appareils(self):
        telephone_id = create_product(
            self.conn, "TEL-001", "Smartphone suivi", 1_000_000, track_serial=True
        )
        imei1 = complete_imei("35999900000001")
        imei2 = complete_imei("35999900000002")
        add_units(self.conn, telephone_id, [(imei1, imei2)])
        vente = create_sale(
            self.conn,
            [Line(telephone_id, imeis=[imei1])],
            [PaymentIn("ccp", 1_000_000)],
        )

        html = self.page._construire_bon_livraison_html(vente.sale_id)

        self.assertIn(f"IMEI1 : {imei1}", html)
        self.assertIn(f"IMEI2 : {imei2}", html)

    def test_bouton_bon_de_livraison_lance_l_impression(self):
        vente = create_sale(
            self.conn,
            [Line(self.produit_id)],
            [PaymentIn("ccp", 1_000_000)],
            customer_id=self.client_id,
        )
        self.page._derniere_vente_id = vente.sale_id
        self.page.btn_bon_livraison.setEnabled(True)

        with patch.object(self.page, "_imprimer_document_a4") as imprimer:
            self.page.btn_bon_livraison.click()

        contenu, titre = imprimer.call_args.args
        self.assertIn(f"BL-{vente.number}", contenu)
        self.assertEqual(titre, "Imprimer le bon de livraison")

    def test_bouton_facture_imprime_sur_a4_portrait(self):
        from PySide6.QtGui import QPageLayout, QPageSize

        vente = create_sale(
            self.conn,
            [Line(self.produit_id)],
            [PaymentIn("ccp", 1_000_000)],
            customer_id=self.client_id,
        )
        self.page._derniere_vente_id = vente.sale_id
        imprimante = Mock()
        imprimante.setPageLayout.return_value = True
        dialogue = Mock()
        dialogue.exec.return_value = QDialog.DialogCode.Accepted
        document = Mock()

        with (
            patch("interface.page_vente_rapide.QPrinter", return_value=imprimante),
            patch("interface.page_vente_rapide.QPrintDialog", return_value=dialogue),
            patch("interface.page_vente_rapide.QTextDocument", return_value=document),
        ):
            self.page.btn_imprimer_facture.setEnabled(True)
            self.page.btn_imprimer_facture.click()

        layout = imprimante.setPageLayout.call_args.args[0]
        self.assertEqual(layout.pageSize().id(), QPageSize.PageSizeId.A4)
        self.assertEqual(layout.orientation(), QPageLayout.Orientation.Portrait)
        document.print_.assert_called_once_with(imprimante)

    def test_impression_envoie_le_document_a_l_imprimante_selectionnee(self):
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize

        self.page._dernier_ticket = ("<html><body>Ticket test</body></html>", 100)
        imprimante = Mock()
        imprimante.setPageLayout.return_value = True
        layout_actuel = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape,
            QMarginsF(5, 5, 5, 5),
            QPageLayout.Unit.Millimeter,
        )
        imprimante.pageLayout.return_value = layout_actuel
        dialogue = Mock()
        dialogue.exec.return_value = QDialog.DialogCode.Accepted
        document = Mock()
        info_imprimante = Mock()
        info_imprimante.isNull.return_value = False
        info_imprimante.supportedPageSizes.return_value = []

        with (
            patch("interface.page_vente_rapide.QPrinter", return_value=imprimante),
            patch("interface.page_vente_rapide.QPrintDialog", return_value=dialogue),
            patch("interface.page_vente_rapide.QPrinterInfo.printerInfo", return_value=info_imprimante),
            patch("interface.page_vente_rapide.QTextDocument", return_value=document),
        ):
            self.page.btn_imprimer.setEnabled(True)
            self.page.btn_imprimer.click()

        layout_applique = imprimante.setPageLayout.call_args.args[0]
        self.assertEqual(layout_applique.pageSize().id(), QPageSize.PageSizeId.A4)
        self.assertEqual(layout_applique.orientation(), QPageLayout.Orientation.Landscape)
        self.assertEqual(layout_applique.margins(QPageLayout.Unit.Millimeter).left(), 10)
        dialogue.exec.assert_called_once()
        document.setHtml.assert_called_once_with("<html><body>Ticket test</body></html>")
        document.print_.assert_called_once_with(imprimante)

    def test_impression_adapte_la_largeur_au_rouleau_thermique(self):
        from PySide6.QtCore import QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize

        self.page._dernier_ticket = ("<html><body>Ticket thermique</body></html>", 100)
        imprimante = Mock()
        imprimante.setPageLayout.return_value = True
        dialogue = Mock()
        dialogue.exec.return_value = QDialog.DialogCode.Accepted
        document = Mock()
        format_rouleau = QPageSize(
            QSizeF(58, 200),
            QPageSize.Unit.Millimeter,
            "Rouleau 58 mm",
            QPageSize.SizeMatchPolicy.ExactMatch,
        )
        info_imprimante = Mock()
        info_imprimante.isNull.return_value = False
        info_imprimante.supportedPageSizes.return_value = [format_rouleau]

        with (
            patch("interface.page_vente_rapide.QPrinter", return_value=imprimante),
            patch("interface.page_vente_rapide.QPrintDialog", return_value=dialogue),
            patch("interface.page_vente_rapide.QPrinterInfo.printerInfo", return_value=info_imprimante),
            patch("interface.page_vente_rapide.QTextDocument", return_value=document),
        ):
            self.page.imprimer_ticket()

        layout_applique = imprimante.setPageLayout.call_args.args[0]
        dimensions = layout_applique.pageSize().size(QPageSize.Unit.Millimeter)
        self.assertEqual((dimensions.width(), dimensions.height()), (58.0, 100.0))
        self.assertEqual(layout_applique.orientation(), QPageLayout.Orientation.Portrait)
        self.assertEqual(layout_applique.margins(QPageLayout.Unit.Millimeter).left(), 2)
        document.print_.assert_called_once_with(imprimante)


if __name__ == "__main__":
    unittest.main()

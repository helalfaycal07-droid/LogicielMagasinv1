import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.clock import format_date
from magasin.customers import create_customer
from magasin.products import create_product
from magasin.quotes import QuoteLine, create_quote, get_quote
from magasin.sales import PaymentIn
from magasin.stock import stock_of
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageDevisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_devis import PageDevis

        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "DEV-UI", "Produit devis UI", 10_000, initial_stock=2
        )
        self.customer_id = create_customer(self.conn, "Client devis UI", "0555000005")
        self.quote = create_quote(
            self.conn, [QuoteLine(self.product_id, 1)],
            recipient_name="Client devis UI", customer_id=self.customer_id,
        )
        self.loaded = []
        self.page = PageDevis(self.conn, None, self.loaded.append)

    def test_liste_et_charge_un_devis_ouvert(self):
        self.page.tableau.selectRow(0)

        self.assertEqual(self.page.tableau.item(0, 0).text(), self.quote["number"])
        self.assertEqual(self.page.tableau.item(0, 5).text(), "Ouvert")
        self.assertTrue(self.page.bouton_charger.isEnabled())
        self.page.charger()
        self.assertEqual(self.loaded, [self.quote["id"]])

    def test_annule_un_devis_sans_effacer_ses_lignes(self):
        self.page.tableau.selectRow(0)
        with (
            patch("interface.page_devis.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.annuler()

        self.assertEqual(get_quote(self.conn, self.quote["id"])["status"], "cancelled")
        self.assertEqual(self.page.tableau.item(0, 5).text(), "Annulé")


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class CaisseDevisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_vente_rapide import PageVenteRapide

        self.conn = new_db()
        self.customer_id = create_customer(
            self.conn, "Client Ticket", "0555000000", credit_allowed=True
        )
        self.product_id = create_product(
            self.conn, "DEV-CAISSE", "Produit devis caisse", 10_000, initial_stock=2
        )
        self.page = PageVenteRapide(self.conn)

    def _dialogue_destinataire(self):
        from PySide6.QtWidgets import QDialog
        from unittest.mock import Mock

        return (
            Mock(valeurs={
                "name": "Client Ticket",
                "phone": "0555000000",
                "address": "",
            }),
            QDialog.DialogCode.Accepted,
        )

    def test_enregistre_un_devis_sans_vendre_et_permet_de_l_imprimer(self):
        self.page.client.setCurrentIndex(self.page.client.findData(self.customer_id))
        self.page.panier = [{
            "product_id": self.product_id, "reference": "DEV-CAISSE",
            "name": "Produit devis caisse", "quantity": 1,
            "unit_price_centimes": 10_000, "imeis": [],
        }]
        self.page._rafraichir_panier()
        dialogue, accepte = self._dialogue_destinataire()
        with (
            patch("interface.page_vente_rapide.ProformaDestinataireDialogue",
                  return_value=dialogue),
            patch("interface.page_vente_rapide.ouvrir_dialogue", return_value=accepte),
            patch.object(QMessageBox, "information"),
        ):
            self.page.enregistrer_devis()

        quote = get_quote(self.conn, self.page._quote_id)
        self.assertEqual(quote["number"], "DEV-000001")
        self.assertEqual(quote["status"], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)
        self.assertEqual(stock_of(self.conn, self.product_id), 2)
        self.assertEqual(self.page.paiements, [])
        self.assertFalse(self.page.bouton_enregistrer_devis.isEnabled())

        with patch.object(self.page, "_imprimer_document_a4") as imprimer:
            self.page.imprimer_proforma()
        contenu, titre = imprimer.call_args.args
        self.assertIn("DEVIS", contenu)
        self.assertIn(quote["number"], contenu)
        self.assertIn(format_date(quote["valid_until"]), contenu)
        self.assertIn("ne réserve pas le stock", contenu)
        self.assertEqual(titre, "Imprimer le devis")

    def test_charge_et_convertit_le_devis_en_vente_une_seule_fois(self):
        quote = create_quote(
            self.conn,
            [QuoteLine(self.product_id, 1)],
            recipient_name="Client Ticket",
            customer_id=self.customer_id,
        )
        self.page.panier = [{
            "product_id": self.product_id, "reference": "DEV-CAISSE",
            "name": "Produit devis caisse", "quantity": 1,
            "unit_price_centimes": 10_000, "imeis": [],
        }]
        with patch("interface.page_vente_rapide.demander_confirmation", return_value=True):
            self.assertTrue(self.page.charger_devis(quote["id"]))
        self.page.paiements = [PaymentIn("ccp", 10_000)]

        with (
            patch("interface.page_vente_rapide.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.valider_vente()

        sale = self.conn.execute(
            "SELECT id, quote_id FROM sales WHERE quote_id = ?", (quote["id"],)
        ).fetchone()
        self.assertIsNotNone(sale)
        self.assertEqual(get_quote(self.conn, quote["id"])["status"], "converted")
        self.assertEqual(stock_of(self.conn, self.product_id), 1)

    def test_le_chargement_depuis_la_page_devis_conserve_le_client(self):
        from interface.fenetre_principale import FenetrePrincipale

        quote = create_quote(
            self.conn,
            [QuoteLine(self.product_id, 1)],
            recipient_name="Client Ticket",
            customer_id=self.customer_id,
        )
        window = FenetrePrincipale(self.conn)

        window._charger_devis(quote["id"])

        self.assertEqual(window.menu.currentRow(), 1)
        self.assertEqual(window.page_vente_rapide.client.currentData(), self.customer_id)
        self.assertEqual(window.page_vente_rapide._quote_id, quote["id"])
        self.assertEqual(len(window.page_vente_rapide.panier), 1)


if __name__ == "__main__":
    unittest.main()

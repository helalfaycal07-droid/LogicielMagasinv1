"""Tests de l'écran Retours."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.cash import open_session
from magasin.clock import format_dt
from magasin.customers import create_customer
from magasin.products import create_product
from magasin.sales import Line, PaymentIn, create_sale
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageRetoursTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_retours import PageRetours

        self.conn = new_db()
        product_id = create_product(
            self.conn, "UI-RET-001", "Produit retour UI", 25_000,
            cost_centimes=10_000, initial_stock=2,
        )
        open_session(self.conn, opening_centimes=30_000)
        customer_id = create_customer(self.conn, "Client retour UI", "0555000002")
        sale = create_sale(
            self.conn, [Line(product_id)], [PaymentIn("ccp", 25_000)],
            customer_id=customer_id,
        )
        self.sale_number = sale.number
        self.page = PageRetours(self.conn)
        self.page.numero_vente.setText(sale.number)
        self.page.charger_vente()

    def tearDown(self):
        from PySide6.QtCore import QEvent

        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.conn.close()

    def test_recherche_et_apercu_du_montant(self):
        self.assertEqual(self.page.tableau.rowCount(), 1)
        self.page.tableau.cellWidget(0, 4).setValue(1)

        self.assertIn("250,00 DA", self.page.montant.text())
        self.assertIn("250,00 DA", self.page.remboursement.text())
        self.assertTrue(self.page.bouton_valider.isEnabled())

    def test_selection_d_une_vente_recente_charge_automatiquement_ses_articles(self):
        self.page.numero_vente.clear()
        index = self.page.ventes_recentes.findData(self.sale_number)
        self.assertGreater(index, 0)

        self.page.ventes_recentes.setCurrentIndex(0)
        self.page.ventes_recentes.setCurrentIndex(index)

        self.assertEqual(self.page.numero_vente.text(), self.sale_number)
        label = self.page.ventes_recentes.currentText()
        created_at = self.conn.execute(
            "SELECT created_at FROM sales WHERE number = ?", (self.sale_number,)
        ).fetchone()["created_at"]
        self.assertIn(format_dt(created_at), label)
        self.assertIn("Client retour UI", label)
        self.assertIn("250,00 DA", label)
        self.assertEqual(self.page.tableau.rowCount(), 1)
        self.assertEqual(self.page.tableau.item(0, 1).text(), "Produit retour UI")
        self.assertIn("250,00 DA", self.page.info_vente.text())

    def test_recherche_par_client_remplit_la_liste_et_charge_la_vente(self):
        self.page.recherche_ventes.setText("Client retour")
        self.page.bouton_filtrer_ventes.click()

        self.assertEqual(self.page.ventes_recentes.count(), 2)
        self.assertIn(self.sale_number, self.page.ventes_recentes.itemText(1))
        self.page.ventes_recentes.setCurrentIndex(1)

        self.assertEqual(self.page.numero_vente.text(), self.sale_number)
        self.assertEqual(self.page.tableau.rowCount(), 1)

    def test_bouton_ventes_recentes_efface_le_filtre(self):
        self.page.recherche_ventes.setText("aucun client correspondant")
        self.page.bouton_filtrer_ventes.click()
        self.assertEqual(self.page.ventes_recentes.count(), 1)
        self.assertEqual(
            self.page.ventes_recentes.itemText(0), "Aucune vente correspondante"
        )

        self.page.bouton_ventes_recentes.click()

        self.assertEqual(self.page.recherche_ventes.text(), "")
        self.assertEqual(self.page.ventes_recentes.count(), 2)

    def test_pagination_permet_de_selectionner_une_vente_apres_les_50_premieres(self):
        product_id = self.conn.execute(
            "SELECT id FROM products WHERE reference = 'UI-RET-001'"
        ).fetchone()["id"]
        for _ in range(50):
            create_sale(
                self.conn, [Line(product_id)], [PaymentIn("ccp", 25_000)]
            )
        self.page.rafraichir_ventes_recentes()

        self.assertTrue(self.page.bouton_page_suivante.isEnabled())
        self.assertEqual(self.page.indicateur_page.text(), "Page 1")
        self.page.bouton_page_suivante.click()

        self.assertEqual(self.page.indicateur_page.text(), "Page 2")
        self.assertFalse(self.page.bouton_page_suivante.isEnabled())
        self.assertTrue(self.page.bouton_page_precedente.isEnabled())
        index = self.page.ventes_recentes.findData(self.sale_number)
        self.assertGreater(index, 0)
        self.page.ventes_recentes.setCurrentIndex(index)
        self.assertEqual(self.page.numero_vente.text(), self.sale_number)

        self.page.bouton_page_precedente.click()
        self.assertEqual(self.page.indicateur_page.text(), "Page 1")

    def test_validation_demande_confirmation_et_enregistre_le_retour(self):
        self.page.tableau.cellWidget(0, 4).setValue(1)
        self.page.raison.setText("Produit non conforme")

        with (
            patch("interface.page_retours.demander_confirmation", return_value=True),
            patch("interface.page_retours.QMessageBox.information"),
            patch("interface.page_retours.montrer_erreur") as montrer_erreur,
        ):
            self.page.valider_retour()

        montrer_erreur.assert_not_called()
        returned = self.conn.execute("SELECT COUNT(*) FROM sale_returns").fetchone()[0]
        self.assertEqual(returned, 1)
        self.assertEqual(self.page.tableau.item(0, 3).text(), "1")

    def test_motif_obligatoire_empeche_la_validation(self):
        self.page.tableau.cellWidget(0, 4).setValue(1)

        with patch("interface.page_retours.montrer_erreur") as montrer_erreur:
            self.page.valider_retour()

        montrer_erreur.assert_called_once()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sale_returns").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

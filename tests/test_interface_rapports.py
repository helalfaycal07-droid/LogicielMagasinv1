"""Tests de l'écran Rapports."""

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.customers import create_customer
from magasin.products import create_product
from magasin.returns import ReturnLine, create_return
from magasin.sales import Line, PaymentIn, create_sale
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageRapportsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_rapports import PageRapports

        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "UI-R-001", "Produit rapport UI", 25_000,
            cost_centimes=10_000, initial_stock=2,
        )
        create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 25_000)],
            at="2026-11-02T10:00:00+01:00",
        )
        self.page = PageRapports(self.conn)

    def test_affiche_les_ventes_et_valorise_le_stock(self):
        jour = QDate.fromString("02/11/2026", "dd/MM/yyyy")
        self.page.date_debut.setDate(jour)
        self.page.date_fin.setDate(jour)
        self.page.rafraichir()

        self.assertIn("1 vente(s)", self.page.resume.text())
        self.assertIn("250,00 DA de chiffre d'affaires", self.page.resume.text())
        self.assertIn("chiffre d'affaires net : 250,00 DA", self.page.resume.text())
        self.assertEqual(self.page.tableau_produits.rowCount(), 1)
        self.assertEqual(self.page.tableau_paiements.item(0, 0).text(), "CCP")
        self.assertEqual(self.page.tableau_stock.item(0, 4).text(), "100,00 DA")

    def test_export_csv_contient_le_titre_la_periode_et_les_lignes(self):
        jour = QDate.fromString("02/11/2026", "dd/MM/yyyy")
        self.page.date_debut.setDate(jour)
        self.page.date_fin.setDate(jour)
        self.page.rafraichir()

        with tempfile.TemporaryDirectory() as dossier:
            chemin = str(Path(dossier) / "rapport.csv")
            with (
                patch("interface.page_rapports.QFileDialog.getSaveFileName",
                      return_value=(chemin, "Fichier CSV (*.csv)")),
                patch("interface.page_rapports.QMessageBox.information"),
            ):
                self.page.exporter_csv()

            with open(chemin, encoding="utf-8-sig", newline="") as fichier:
                lignes = list(csv.reader(fichier, delimiter=";"))

        self.assertEqual(lignes[0], ["Rapport", "Ventes par produit"])
        self.assertEqual(lignes[1], ["Période", "02/11/2026", "au", "02/11/2026"])
        self.assertEqual(lignes[2], ["Référence", "Produit", "Quantité vendue", "Chiffre d'affaires"])
        self.assertEqual(lignes[3], ["UI-R-001", "Produit rapport UI", "1", "250,00 DA"])

    def test_onglet_retours_est_exportable_et_affiche_les_remboursements(self):
        customer_id = create_customer(self.conn, "Client retour UI", "0555000003")
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 25_000)],
            customer_id=customer_id, at="2026-11-01T10:00:00+01:00",
        )
        item_id = self.conn.execute(
            "SELECT id FROM sale_items WHERE sale_id = ?", (sale.sale_id,)
        ).fetchone()["id"]
        create_return(
            self.conn, sale.sale_id, [ReturnLine(item_id, 1)],
            "avoir", "Retour visible", at="2026-11-02T10:00:00+01:00",
        )
        jour = QDate.fromString("02/11/2026", "dd/MM/yyyy")
        self.page.date_debut.setDate(jour)
        self.page.date_fin.setDate(jour)
        self.page.rafraichir()
        self.page.onglets.setCurrentWidget(self.page.tableau_retours)

        self.assertEqual(self.page.onglets.tabText(self.page.onglets.currentIndex()), "Retours")
        self.assertEqual(self.page.tableau_retours.rowCount(), 1)
        self.assertEqual(self.page.tableau_retours.item(0, 4).text(), "Produit rapport UI")
        self.assertEqual(self.page.tableau_retours.item(0, 6).text(), "250,00 DA")
        self.assertEqual(self.page.tableau_retours.item(0, 8).text(), "Retour visible")

    def test_benefice_mensuel_et_annuel_sont_affiches(self):
        jour = QDate.fromString("02/11/2026", "dd/MM/yyyy")
        self.page.date_debut.setDate(jour)
        self.page.date_fin.setDate(jour)
        self.page.rafraichir()
        self.page.onglets.setCurrentWidget(self.page.tableau_benefice)

        self.assertEqual(self.page.tableau_benefice.rowCount(), 1)
        self.assertEqual(self.page.tableau_benefice.item(0, 0).text(), "2026-11")
        self.assertEqual(self.page.tableau_benefice.item(0, 5).text(), "150,00 DA")
        self.assertIn("Bénéfice brut : 150,00 DA", self.page.resume.text())

        self.page.regroupement_benefice.setCurrentIndex(1)

        self.assertEqual(self.page.date_debut.date(), QDate(2026, 1, 1))
        self.assertEqual(self.page.date_fin.date(), QDate(2026, 12, 31))
        self.assertEqual(self.page.tableau_benefice.item(0, 0).text(), "2026")


if __name__ == "__main__":
    unittest.main()

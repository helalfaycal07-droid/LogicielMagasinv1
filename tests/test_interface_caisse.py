"""Tests de l'écran Caisse (sans écran physique)."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.cash import current_session
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageCaisseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_caisse import PageCaisse

        self.conn = new_db()
        self.page = PageCaisse(self.conn)

    def test_ouverture_active_les_actions_de_caisse(self):
        self.page.solde_ouverture.setText("1000")
        self.page.bouton_ouvrir.click()

        self.assertIsNotNone(current_session(self.conn))
        self.assertEqual(self.page.session_id, current_session(self.conn)["id"])
        self.assertTrue(self.page.bouton_depense.isEnabled())
        self.assertTrue(self.page.bouton_cloturer.isEnabled())
        self.assertFalse(self.page.bouton_reinitialiser_ouverture.isEnabled())
        self.assertEqual(self.page.affichage_solde.text(), "1 000,00 DA")
        self.assertEqual(self.page.libelle_solde.text(), "SOLDE ATTENDU")
        self.assertEqual(self.page.affichage_solde.graphicsEffect().blurRadius(), 24)

    def test_reinitialiser_le_solde_prepare_une_ouverture_a_zero(self):
        self.page.solde_ouverture.setText("1000")
        self.page.bouton_reinitialiser_ouverture.click()

        self.assertEqual(self.page.solde_ouverture.text(), "0")
        self.assertEqual(self.page.affichage_solde.text(), "0,00 DA")
        self.assertIn("pour l'appliquer", self.page.resume.text())
        self.assertIsNone(current_session(self.conn))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM cash_sessions").fetchone()[0], 0
        )

        self.page.bouton_ouvrir.click()
        session = current_session(self.conn)
        self.assertIsNotNone(session)
        self.assertEqual(session["opening_centimes"], 0)
        self.assertEqual(self.page.affichage_solde.text(), "0,00 DA")

    def test_depense_met_a_jour_le_resume_et_la_cloture(self):
        self.page.solde_ouverture.setText("1000")
        self.page.bouton_ouvrir.click()
        self.page.montant_depense.setText("125")
        self.page.description_depense.setText("Fournitures")
        self.page.bouton_depense.click()

        self.assertIn("Dépenses : -125,00 DA", self.page.resume.text())
        self.assertEqual(self.page.affichage_solde.text(), "875,00 DA")
        self.assertEqual(self.page.montant_compte.text(), "875")

        with patch("interface.page_caisse.demander_confirmation", return_value=True):
            self.page.bouton_cloturer.click()

        self.assertIsNone(current_session(self.conn))
        self.assertIn("Caisse clôturée", self.page.resume.text())
        self.assertIn("Écart : 0,00 DA", self.page.resume.text())
        self.assertEqual(self.page.affichage_solde.text(), "875,00 DA")
        self.assertEqual(self.page.libelle_solde.text(), "SOLDE COMPTÉ À LA CLÔTURE")


if __name__ == "__main__":
    unittest.main()

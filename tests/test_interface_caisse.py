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
        self.assertIn("1 000,00 DA", self.page.resume.text())

    def test_depense_met_a_jour_le_resume_et_la_cloture(self):
        self.page.solde_ouverture.setText("1000")
        self.page.bouton_ouvrir.click()
        self.page.montant_depense.setText("125")
        self.page.description_depense.setText("Fournitures")
        self.page.bouton_depense.click()

        self.assertIn("Dépenses : -125,00 DA", self.page.resume.text())
        self.assertIn("Solde attendu : 875,00 DA", self.page.resume.text())
        self.assertEqual(self.page.montant_compte.text(), "875")

        with patch("interface.page_caisse.demander_confirmation", return_value=True):
            self.page.bouton_cloturer.click()

        self.assertIsNone(current_session(self.conn))
        self.assertIn("Caisse clôturée", self.page.resume.text())
        self.assertIn("Écart : 0,00 DA", self.page.resume.text())


if __name__ == "__main__":
    unittest.main()

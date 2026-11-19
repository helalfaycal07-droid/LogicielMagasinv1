import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.purchases import create_supplier, list_suppliers
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageFournisseursTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_fournisseurs import PageFournisseurs

        self.conn = new_db()
        self.page = PageFournisseurs(self.conn)

    def test_cree_une_fiche_fournisseur(self):
        self.page.nom.setText("Fournisseur UI")
        self.page.telephone.setText("0555000001")
        self.page.email.setText("contact@example.com")

        with patch.object(QMessageBox, "information"):
            self.page.enregistrer()

        supplier, = list_suppliers(self.conn)
        self.assertEqual(supplier["name"], "Fournisseur UI")
        self.assertEqual(supplier["phone"], "0555000001")
        self.assertEqual(supplier["email"], "contact@example.com")
        self.assertEqual(self.page.tableau.rowCount(), 1)

    def test_modifie_et_archive_une_fiche_sans_supprimer_la(self):
        create_supplier(self.conn, None, "Fournisseur UI", phone="0555000001")
        self.page.rafraichir()
        self.page.tableau.selectRow(0)
        self.page.nom.setText("Fournisseur modifié")
        self.page.telephone.setText("0555000002")

        with patch.object(QMessageBox, "information"):
            self.page.enregistrer()

        self.assertEqual(list_suppliers(self.conn)[0]["name"], "Fournisseur modifié")
        self.page.tableau.selectRow(0)
        with patch("interface.page_fournisseurs.demander_confirmation", return_value=True):
            self.page.changer_etat()
        supplier, = list_suppliers(self.conn)
        self.assertFalse(supplier["active"])
        self.assertEqual(supplier["name"], "Fournisseur modifié")


if __name__ == "__main__":
    unittest.main()

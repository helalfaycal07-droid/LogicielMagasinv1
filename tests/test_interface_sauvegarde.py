"""Tests de l'écran de sauvegarde et de restauration."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.backups import backup_database
from magasin.products import create_product
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageSauvegardeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_sauvegarde import PageSauvegarde

        self.conn = new_db()
        create_product(self.conn, "UI-BKP-001", "Produit courant", 10_000)
        self.after_restore = Mock()
        self.page = PageSauvegarde(self.conn, self.after_restore)

    def test_restauration_confirmee_cree_une_copie_precedente_et_recharge(self):
        with tempfile.TemporaryDirectory() as dossier:
            racine = Path(dossier)
            sauvegarde = racine / "sauvegarde.sqlite"
            dossier_copies = racine / "copies"
            dossier_copies.mkdir()
            backup_database(self.conn, sauvegarde)
            create_product(self.conn, "UI-BKP-002", "Produit courant supplémentaire", 5_000)

            with (
                patch("interface.page_sauvegarde.data_dir", return_value=dossier_copies),
                patch("interface.page_sauvegarde.QFileDialog.getOpenFileName",
                      return_value=(str(sauvegarde), "Sauvegarde SQLite (*.sqlite *.db)")),
                patch("interface.page_sauvegarde.QMessageBox.question",
                      return_value=QMessageBox.Yes),
                patch("interface.page_sauvegarde.QMessageBox.information"),
            ):
                self.page.restaurer_sauvegarde()

            self.assertIsNotNone(self.conn.execute(
                "SELECT id FROM products WHERE reference = 'UI-BKP-001'"
            ).fetchone())
            self.assertIsNone(self.conn.execute(
                "SELECT id FROM products WHERE reference = 'UI-BKP-002'"
            ).fetchone())
            self.after_restore.assert_called_once_with()
            copies = list(dossier_copies.glob("avant-restauration-*.sqlite"))
            self.assertEqual(len(copies), 1)
            recovery = sqlite3.connect(copies[0])
            try:
                self.assertIsNotNone(recovery.execute(
                    "SELECT id FROM products WHERE reference = 'UI-BKP-002'"
                ).fetchone())
            finally:
                recovery.close()

    def test_fermeture_propose_une_sauvegarde_et_la_cree(self):
        from interface.fenetre_principale import FenetrePrincipale
        from magasin.users import authenticate, create_first_admin

        admin_id = create_first_admin(self.conn, "admin-close", "MotDePasseAdminClose")
        user = authenticate(self.conn, "admin-close", "MotDePasseAdminClose")
        self.assertEqual(user["id"], admin_id)
        window = FenetrePrincipale(self.conn, current_user=user)

        with tempfile.TemporaryDirectory() as dossier:
            destination = Path(dossier) / "fermeture.sqlite"
            event = QCloseEvent()
            with (
                patch("interface.fenetre_principale.QMessageBox.question",
                      return_value=QMessageBox.StandardButton.Save),
                patch("interface.fenetre_principale.QFileDialog.getSaveFileName",
                      return_value=(str(destination), "Sauvegarde SQLite (*.sqlite *.db)")),
                patch("interface.fenetre_principale.QMessageBox.information"),
            ):
                window.closeEvent(event)

            self.assertTrue(event.isAccepted())
            self.assertTrue(destination.is_file())
            backup = sqlite3.connect(destination)
            try:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                backup.close()

    def test_fermeture_sans_sauvegarde_est_possible_sur_confirmation(self):
        from interface.fenetre_principale import FenetrePrincipale

        window = FenetrePrincipale(self.conn)
        event = QCloseEvent()
        with patch(
            "interface.fenetre_principale.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            window.closeEvent(event)

        question.assert_called_once()
        self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()

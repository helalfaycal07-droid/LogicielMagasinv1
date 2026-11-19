"""Tests des écrans de création du premier compte et de connexion."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.errors import AuthenticationError
from magasin.users import has_users
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class SecurityDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.security import (AccountRecoveryDialog, FirstAdminDialog,
                                        LoginDialog)

        self.conn = new_db()
        self.setup_dialog = FirstAdminDialog(self.conn)
        self.login_dialog = LoginDialog(self.conn)
        self.recovery_dialog_type = AccountRecoveryDialog

    def test_creation_initiale_et_connexion(self):
        self.setup_dialog.username.setText("admin-ui")
        self.setup_dialog.password.setText("MotDePasseAdminUI")
        self.setup_dialog.confirmation.setText("MotDePasseAdminUI")
        self.setup_dialog._create()

        self.assertEqual(self.setup_dialog.result(), QDialog.Accepted)
        self.assertTrue(has_users(self.conn))
        self.assertEqual(self.setup_dialog.user["role"], "administrateur")
        self.assertTrue(self.setup_dialog.recovery_code)

        self.login_dialog.username.setText("admin-ui")
        self.login_dialog.password.setText("MotDePasseAdminUI")
        self.login_dialog._login()

        self.assertEqual(self.login_dialog.result(), QDialog.Accepted)

    def test_recuperation_depuis_la_fenetre_de_connexion(self):
        self.setup_dialog.username.setText("admin-ui")
        self.setup_dialog.password.setText("MotDePasseAdminUI")
        self.setup_dialog.confirmation.setText("MotDePasseAdminUI")
        self.setup_dialog._create()

        def remplir_et_valider(recovery):
            recovery.recovery_code.setText(self.setup_dialog.recovery_code)
            recovery.username.setText("nouvel-admin")
            recovery.password.setText("NouveauMotDePasse1")
            recovery.confirmation.setText("NouveauMotDePasse1")
            recovery._reset()
            return recovery.result()

        with patch.object(self.recovery_dialog_type, "exec", remplir_et_valider):
            self.login_dialog._recover()
        self.assertEqual(self.login_dialog.result(), QDialog.Accepted)
        self.assertEqual(self.login_dialog.user["username"], "nouvel-admin")

    def test_echec_de_connexion_ne_cree_pas_de_compte(self):
        self.login_dialog.username.setText("absent")
        self.login_dialog.password.setText("MotDePasseFaux")

        self.login_dialog._login()

        self.assertEqual(self.login_dialog.result(), QDialog.Rejected)
        self.assertFalse(has_users(self.conn))


if __name__ == "__main__":
    unittest.main()

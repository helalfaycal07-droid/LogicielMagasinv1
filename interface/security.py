"""Connexion initiale et authentification des utilisateurs du magasin."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from magasin.errors import MagasinError
from magasin.users import (authenticate, create_first_admin, generate_recovery_code,
                           reset_admin_account)


def afficher_code_secours(parent, recovery_code: str) -> None:
    message = QMessageBox(parent)
    message.setIcon(QMessageBox.Icon.Information)
    message.setWindowTitle("Conservez votre code de secours")
    message.setText(
        "Notez ce code et conservez-le en lieu sûr. Il permet de récupérer le compte "
        "administrateur si l'identifiant ou le mot de passe est oublié.\n\n"
        f"Code de secours : {recovery_code}"
    )
    message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    message.setStandardButtons(QMessageBox.StandardButton.Ok)
    message.exec()


class FirstAdminDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user = None
        self.setWindowTitle("Créer le compte administrateur")
        self.setMinimumWidth(380)

        note = QLabel(
            "Première utilisation : créez le compte administrateur. "
            "Aucun mot de passe par défaut n'est utilisé. Un code de secours sera affiché "
            "après la création : conservez-le en lieu sûr."
        )
        note.setWordWrap(True)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Au moins 3 caractères")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Au moins 10 caractères")
        self.recovery_code = None
        self.confirmation = QLineEdit()
        self.confirmation.setEchoMode(QLineEdit.Password)
        self.message = QLabel("")
        self.message.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Identifiant", self.username)
        form.addRow("Mot de passe", self.password)
        form.addRow("Confirmer", self.confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(buttons)

    def _create(self):
        if self.password.text() != self.confirmation.text():
            self.message.setText("Les deux mots de passe ne correspondent pas.")
            return
        recovery_code = generate_recovery_code()
        try:
            user_id = create_first_admin(
                self.conn, self.username.text(), self.password.text(),
                recovery_code=recovery_code,
            )
        except MagasinError as error:
            self.message.setText(str(error))
            return
        self.recovery_code = recovery_code
        self.user = authenticate(self.conn, self.username.text(), self.password.text())
        self.user["id"] = user_id
        self.accept()


class LoginDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user = None
        self.setWindowTitle("Connexion")
        self.setMinimumWidth(340)

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.recovery_button = QPushButton("Identifiant ou mot de passe oublié ?")
        self.recovery_button.setFlat(True)
        self.recovery_button.clicked.connect(self._recover)
        self.message = QLabel("")

        form = QFormLayout()
        form.addRow("Identifiant", self.username)
        form.addRow("Mot de passe", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._login)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Connectez-vous avec votre compte utilisateur."))
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(buttons)
        layout.addWidget(self.recovery_button)

    def _login(self):
        try:
            self.user = authenticate(self.conn, self.username.text(), self.password.text())
        except MagasinError as error:
            self.message.setText(str(error))
            self.password.clear()
            return
        self.accept()

    def _recover(self):
        recovery = AccountRecoveryDialog(self.conn, self)
        if recovery.exec() == QDialog.DialogCode.Accepted:
            self.user = recovery.user
            self.accept()


class AccountRecoveryDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user = None
        self.setWindowTitle("Récupération du compte administrateur")
        self.setMinimumWidth(380)

        note = QLabel(
            "Saisissez le code de secours, puis le nouvel identifiant et le nouveau mot de passe."
        )
        note.setWordWrap(True)
        self.recovery_code = QLineEdit()
        self.recovery_code.setPlaceholderText("Code noté lors de la création du compte")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Au moins 3 caractères")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Au moins 10 caractères")
        self.confirmation = QLineEdit()
        self.confirmation.setEchoMode(QLineEdit.Password)
        self.message = QLabel("")
        self.message.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Code de secours", self.recovery_code)
        form.addRow("Nouvel identifiant", self.username)
        form.addRow("Nouveau mot de passe", self.password)
        form.addRow("Confirmer le mot de passe", self.confirmation)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._reset)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(buttons)

    def _reset(self):
        if self.password.text() != self.confirmation.text():
            self.message.setText("Les deux mots de passe ne correspondent pas.")
            return
        try:
            self.user = reset_admin_account(
                self.conn, self.recovery_code.text(), self.username.text(),
                self.password.text(),
            )
        except MagasinError as error:
            self.message.setText(str(error))
            return
        self.accept()

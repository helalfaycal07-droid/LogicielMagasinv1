"""Gestion des comptes, rôles et mots de passe des employés."""

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.errors import MagasinError
from magasin.users import (ROLES, create_user, generate_recovery_code, list_users,
                           set_admin_recovery_code, update_user)

from .outils import montrer_erreur
from .security import afficher_code_secours


class PageUtilisateurs(QWidget):
    def __init__(self, conn, actor_user_id: int):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id

        titre = QLabel("Utilisateurs et permissions")
        titre.setObjectName("titre")
        self.tableau = QTableWidget(0, 4)
        self.tableau.setHorizontalHeaderLabels(["Identifiant", "Rôle", "État", "Dernière connexion"])
        self.tableau.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tableau.setSelectionBehavior(QTableWidget.SelectRows)
        self.tableau.setSelectionMode(QTableWidget.SingleSelection)
        self.tableau.horizontalHeader().setStretchLastSection(True)
        self.tableau.itemSelectionChanged.connect(self._selection_changee)

        self.nouvel_identifiant = QLineEdit()
        self.nouveau_mot_de_passe = QLineEdit()
        self.nouveau_mot_de_passe.setEchoMode(QLineEdit.Password)
        self.nouveau_role = self._role_combo()
        self.bouton_creer = QPushButton("Créer l'utilisateur")
        self.bouton_creer.clicked.connect(self.creer)

        formulaire_creation = QFormLayout()
        formulaire_creation.addRow("Identifiant", self.nouvel_identifiant)
        formulaire_creation.addRow("Mot de passe initial", self.nouveau_mot_de_passe)
        formulaire_creation.addRow("Rôle", self.nouveau_role)
        formulaire_creation.addRow(self.bouton_creer)

        self.role_selection = self._role_combo()
        self.actif_selection = QCheckBox("Compte actif")
        self.mot_de_passe_selection = QLineEdit()
        self.mot_de_passe_selection.setEchoMode(QLineEdit.Password)
        self.bouton_modifier = QPushButton("Enregistrer les modifications")
        self.bouton_modifier.clicked.connect(self.modifier)
        formulaire_modification = QFormLayout()
        formulaire_modification.addRow("Rôle sélectionné", self.role_selection)
        formulaire_modification.addRow(self.actif_selection)
        formulaire_modification.addRow("Nouveau mot de passe (facultatif)", self.mot_de_passe_selection)
        formulaire_modification.addRow(self.bouton_modifier)

        note = QLabel(
            "Le caissier peut vendre et ouvrir la caisse. Les retours, changements de prix, "
            "inventaires, clôtures et achats sont réservés au responsable ou à l'administrateur. "
            "La gestion des utilisateurs et les suppressions définitives sont réservées à l'administrateur."
        )
        note.setWordWrap(True)
        note.setObjectName("info")

        actions = QHBoxLayout()
        actions.addLayout(formulaire_creation, 1)
        actions.addLayout(formulaire_modification, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addWidget(self.tableau, 1)
        layout.addLayout(actions)
        self.bouton_code_secours = QPushButton("Générer un code de secours administrateur")
        self.bouton_code_secours.clicked.connect(self.generer_code_secours)
        layout.addWidget(self.bouton_code_secours)
        layout.addWidget(note)
        self.rafraichir()

    @staticmethod
    def _role_combo() -> QComboBox:
        combo = QComboBox()
        for role, label in ROLES.items():
            combo.addItem(label, role)
        return combo

    def rafraichir(self):
        self.utilisateurs = list_users(self.conn)
        self.tableau.setRowCount(len(self.utilisateurs))
        for row, user in enumerate(self.utilisateurs):
            values = (
                user["username"],
                ROLES[user["role"]],
                "Actif" if user["active"] else "Désactivé",
                user["last_login_at"] or "Jamais",
            )
            for column, value in enumerate(values):
                self.tableau.setItem(row, column, QTableWidgetItem(str(value)))
        self._selection_changee()

    def _utilisateur_selectionne(self):
        row = self.tableau.currentRow()
        return self.utilisateurs[row] if 0 <= row < len(self.utilisateurs) else None

    def _selection_changee(self):
        user = self._utilisateur_selectionne()
        self.bouton_modifier.setEnabled(user is not None)
        if user is None:
            self.actif_selection.setChecked(False)
            return
        self.role_selection.setCurrentIndex(self.role_selection.findData(user["role"]))
        self.actif_selection.setChecked(bool(user["active"]))
        self.mot_de_passe_selection.clear()

    def creer(self):
        try:
            create_user(
                self.conn, self.actor_user_id, self.nouvel_identifiant.text(),
                self.nouveau_mot_de_passe.text(), self.nouveau_role.currentData(),
            )
        except MagasinError as error:
            montrer_erreur(self, error)
            return
        self.nouvel_identifiant.clear()
        self.nouveau_mot_de_passe.clear()
        self.rafraichir()
        QMessageBox.information(self, "Compte créé", "Le compte utilisateur a été créé.")

    def modifier(self):
        user = self._utilisateur_selectionne()
        if user is None:
            return
        password = self.mot_de_passe_selection.text() or None
        try:
            update_user(
                self.conn, self.actor_user_id, user["id"],
                role=self.role_selection.currentData(),
                active=self.actif_selection.isChecked(),
                password=password,
            )
        except MagasinError as error:
            montrer_erreur(self, error)
            return
        self.rafraichir()
        QMessageBox.information(self, "Compte mis à jour", "Les modifications ont été enregistrées.")

    def generer_code_secours(self):
        recovery_code = generate_recovery_code()
        try:
            set_admin_recovery_code(self.conn, self.actor_user_id, recovery_code)
        except MagasinError as error:
            montrer_erreur(self, error)
            return
        afficher_code_secours(self, recovery_code)

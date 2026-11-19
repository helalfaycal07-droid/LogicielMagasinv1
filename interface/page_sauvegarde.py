"""Écran de sauvegarde et de restauration des données du magasin."""

from pathlib import Path

from PySide6.QtWidgets import (QFileDialog, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from magasin.backups import backup_database, restore_database
from magasin.clock import now
from magasin.errors import MagasinError, PermissionDenied
from magasin.paths import data_dir
from magasin.reinitialisation import reinitialiser_historique

from .outils import montrer_erreur


class PageSauvegarde(QWidget):
    """Permet de sauvegarder la base et de restaurer une copie vérifiée."""

    def __init__(self, conn, after_restore, actor_user_id: int | None = None,
                 after_reset=None):
        super().__init__()
        self.conn = conn
        self.after_restore = after_restore
        self.actor_user_id = actor_user_id
        self.after_reset = after_reset

        titre = QLabel("Sauvegarde et restauration")
        titre.setObjectName("titre")
        information = QLabel(
            "La sauvegarde contient les données du magasin. Conservez-en une copie sur un autre "
            "support. Une restauration remplacera toutes les données actuellement ouvertes. "
            "La réinitialisation efface les opérations commerciales mais conserve le stock."
        )
        information.setObjectName("info")
        information.setWordWrap(True)

        self.bouton_sauvegarder = QPushButton("Créer une sauvegarde")
        self.bouton_sauvegarder.setObjectName("principal")
        self.bouton_sauvegarder.clicked.connect(self.creer_sauvegarde)
        self.bouton_restaurer = QPushButton("Restaurer une sauvegarde")
        self.bouton_restaurer.clicked.connect(self.restaurer_sauvegarde)
        self.bouton_reinitialiser = QPushButton(
            "Réinitialiser la caisse et l'historique des ventes"
        )
        self.bouton_reinitialiser.setObjectName("danger")
        self.bouton_reinitialiser.clicked.connect(self.reinitialiser_historique)
        utilisateur = (
            self.conn.execute(
                "SELECT role, active FROM users WHERE id = ?", (self.actor_user_id,)
            ).fetchone()
            if self.actor_user_id is not None
            else None
        )
        is_admin = bool(
            utilisateur and utilisateur["active"] and utilisateur["role"] == "administrateur"
        )
        self.bouton_reinitialiser.setVisible(is_admin)

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addWidget(information)
        layout.addWidget(self.bouton_sauvegarder)
        layout.addWidget(self.bouton_restaurer)
        layout.addWidget(self.bouton_reinitialiser)
        layout.addStretch()

    def creer_sauvegarde(self) -> None:
        nom = f"magasin-sauvegarde-{now().strftime('%Y%m%d-%H%M%S')}.sqlite"
        chemin, _ = QFileDialog.getSaveFileName(
            self, "Créer une sauvegarde", str(data_dir() / nom),
            "Sauvegarde SQLite (*.sqlite *.db)",
        )
        if not chemin:
            return
        if not Path(chemin).suffix:
            chemin += ".sqlite"
        try:
            destination = backup_database(
                self.conn, chemin, actor_user_id=self.actor_user_id
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        QMessageBox.information(self, "Sauvegarde créée", f"Sauvegarde enregistrée :\n{destination}")

    def restaurer_sauvegarde(self) -> None:
        chemin, _ = QFileDialog.getOpenFileName(
            self, "Choisir une sauvegarde", str(data_dir()),
            "Sauvegarde SQLite (*.sqlite *.db)",
        )
        if not chemin:
            return
        confirmation = QMessageBox.question(
            self,
            "Confirmer la restauration",
            "Cette opération remplacera toutes les données actuellement ouvertes par celles "
            "de la sauvegarde. Les saisies non enregistrées seront perdues. Continuer ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        copie_securite = None
        try:
            horodatage = now().strftime("%Y%m%d-%H%M%S")
            dossier = data_dir()
            copie_securite = dossier / f"avant-restauration-{horodatage}.sqlite"
            indice = 1
            while copie_securite.exists() or copie_securite.resolve() == Path(chemin).resolve():
                copie_securite = dossier / f"avant-restauration-{horodatage}-{indice}.sqlite"
                indice += 1
            backup_database(
                self.conn, copie_securite, actor_user_id=self.actor_user_id
            )
            restore_database(
                self.conn, chemin, actor_user_id=self.actor_user_id
            )
        except MagasinError as erreur:
            if copie_securite is not None and copie_securite.exists():
                QMessageBox.information(
                    self, "Copie de sécurité",
                    f"L'état précédent a été sauvegardé ici :\n{copie_securite}",
                )
            montrer_erreur(self, erreur)
            return
        self.after_restore()
        QMessageBox.information(
            self, "Restauration terminée",
            f"Les données ont été restaurées.\nCopie de sécurité précédente :\n{copie_securite}",
        )

    def reinitialiser_historique(self) -> None:
        if self.actor_user_id is None:
            montrer_erreur(
                self, PermissionDenied("Connectez-vous avec un compte administrateur.")
            )
            return

        confirmation = QMessageBox.question(
            self,
            "Confirmer la réinitialisation",
            "Attention, cette action est irréversible. Êtes-vous sûr de vouloir effacer "
            "tout l'historique des ventes et des retours, les paiements, les créances et "
            "dettes, et remettre la caisse à zéro ? Les produits et leur stock, les clients, "
            "les fournisseurs et les comptes utilisateurs seront conservés.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return

        mot_de_passe, confirme = QInputDialog.getText(
            self,
            "Validation administrateur",
            "Saisissez le mot de passe de l'administrateur connecté :",
            QLineEdit.Password,
        )
        if not confirme:
            return

        try:
            reinitialiser_historique(self.conn, self.actor_user_id, mot_de_passe)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        if self.after_reset is not None:
            self.after_reset()
        QMessageBox.information(
            self,
            "Réinitialisation terminée",
            "L'historique des ventes, retours, paiements et mouvements de caisse a été effacé. "
            "Le stock et les données de référence ont été conservés.",
        )

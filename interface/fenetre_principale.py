"""Fenêtre principale : menu à gauche, écran à droite."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from .page_dashboard import PageDashboard
from .page_caisse import PageCaisse
from .page_clients import PageClients
from .page_achats import PageAchats
from .page_fournisseurs import PageFournisseurs
from .page_devis import PageDevis
from .page_produits import PageProduits
from .page_retours import PageRetours
from .page_rapports import PageRapports
from .page_sauvegarde import PageSauvegarde
from .page_utilisateurs import PageUtilisateurs
from .page_vente_rapide import PageVenteRapide
from .style import FEUILLE_DE_STYLE
from magasin.backups import backup_database
from magasin.clock import now
from magasin.errors import MagasinError
from magasin.paths import data_dir
from magasin.users import can, has_users
from version_checker import check_for_updates

PAGE_DEMARRAGE = "Tableau de bord"


@dataclass
class Entree:
    libelle: str
    permission: str | None
    creer: Callable[[], QWidget]


class FenetrePrincipale(QMainWindow):
    def __init__(self, conn, titre: str = "Gestion de magasin", info_base: str = "",
                 current_user: dict | None = None):
        super().__init__()
        self.conn = conn
        self.current_user = current_user
        self.info_base = info_base
        self.setWindowTitle(titre)
        self.setStyleSheet(FEUILLE_DE_STYLE)

        # Taille de départ adaptée à l'écran réellement disponible
        # (on retire la barre des tâches Windows), sans jamais le dépasser.
        ecran = self.screen().availableGeometry()
        self.resize(min(1200, ecran.width()), min(700, ecran.height() - 40))
        self.setMinimumSize(800, 500)

        self.entrees = self._definir_entrees()

        self.menu = QListWidget()
        self.menu.setObjectName("menu")
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for entree in self.entrees:
            self.menu.addItem(QListWidgetItem(entree.libelle))

        self.pile = QStackedWidget()
        self._construire_pages()
        self._appliquer_permissions()
        self.menu.currentRowChanged.connect(self._changer_page)

        centre = QWidget()
        disposition = QHBoxLayout(centre)
        disposition.setContentsMargins(0, 0, 0, 0)
        disposition.setSpacing(0)
        colonne_menu = QWidget()
        colonne_menu.setObjectName("colonne_menu")
        colonne_menu.setFixedWidth(210)
        pile_menu = QVBoxLayout(colonne_menu)
        pile_menu.setContentsMargins(0, 0, 0, 0)
        pile_menu.setSpacing(0)
        pile_menu.addWidget(self.menu, 1)
        note = QLabel("Écrans grisés : accès non autorisé")
        note.setObjectName("note_menu")
        pile_menu.addWidget(note)
        disposition.addWidget(colonne_menu)
        disposition.addWidget(self.pile, 1)
        self.setCentralWidget(centre)

        if info_base:
            self._maj_statut()
        self._afficher_page_demarrage()
        QTimer.singleShot(500, self._verifier_mise_a_jour)

    # ---------- définition unique des écrans ----------

    def _definir_entrees(self) -> list[Entree]:
        a = self.current_user["id"] if self.current_user else None
        return [
            Entree("Tableau de bord", None,
                   lambda: PageDashboard(self.conn)),
            Entree("Produits et stock", None,
                   lambda: PageProduits(self.conn, self.current_user)),
            Entree("Vente rapide", None,
                   lambda: PageVenteRapide(self.conn, a)),
            Entree("Retours", "return",
                   lambda: PageRetours(self.conn, a)),
            Entree("Caisse", None,
                   lambda: PageCaisse(self.conn, self.current_user)),
            Entree("Clients et crédit", None,
                   lambda: PageClients(self.conn)),
            Entree("Achats", "purchases",
                   lambda: PageAchats(self.conn, a)),
            Entree("Rapports", "reports",
                   lambda: PageRapports(self.conn)),
            Entree("Sauvegarde", "backups",
                   lambda: PageSauvegarde(
                       self.conn, self._apres_restauration, actor_user_id=a,
                       after_reset=self._apres_reinitialisation)),
            Entree("Utilisateurs", "manage_users",
                   lambda: PageUtilisateurs(self.conn, a) if a else QWidget()),
            Entree("Fournisseurs", "purchases",
                   lambda: PageFournisseurs(self.conn, a)),
            Entree("Devis", "sell",
                   lambda: PageDevis(self.conn, a, self._charger_devis)),
        ]

    def _index(self, libelle: str) -> int:
        for i, entree in enumerate(self.entrees):
            if entree.libelle == libelle:
                return i
        raise KeyError(libelle)

    def _page(self, libelle: str) -> QWidget:
        # La pile contient des zones de défilement ; la vraie page est à l'intérieur.
        return self.pile.widget(self._index(libelle)).widget()

    def _aller_a(self, libelle: str) -> None:
        """Sélectionne une page et force son affichage, même si déjà sélectionnée."""
        ligne = self._index(libelle)
        if self.menu.currentRow() == ligne:
            self._changer_page(ligne)
        else:
            self.menu.setCurrentRow(ligne)

    def _afficher_page_demarrage(self) -> None:
        self._aller_a(PAGE_DEMARRAGE)

    # ---------- construction ----------

    def _envelopper(self, page: QWidget) -> QScrollArea:
        """Met une page dans une zone de défilement.

        Sans cela, une page trop large force toute la fenêtre à s'agrandir
        et déborde de l'écran. Avec la zone de défilement, la fenêtre garde
        sa taille et des barres de défilement apparaissent seulement si besoin.
        """
        zone = QScrollArea()
        zone.setWidgetResizable(True)
        zone.setFrameShape(QFrame.NoFrame)
        zone.setWidget(page)
        return zone

    def _construire_pages(self) -> None:
        while self.pile.count():
            page = self.pile.widget(0)
            self.pile.removeWidget(page)
            page.deleteLater()

        self.entrees = self._definir_entrees()
        for entree in self.entrees:
            self.pile.addWidget(self._envelopper(entree.creer()))

    def _appliquer_permissions(self) -> None:
        restreint = has_users(self.conn)
        for i, entree in enumerate(self.entrees):
            item = self.menu.item(i)
            autorise = not (
                restreint
                and entree.permission
                and not can(self.current_user, entree.permission)
            )
            flags = item.flags()
            if autorise:
                flags |= Qt.ItemIsEnabled | Qt.ItemIsSelectable
            else:
                flags &= ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable
            item.setFlags(flags)

    def _maj_statut(self) -> None:
        identite = (
            f"Connecté : {self.current_user['username']} — "
            f"{self.current_user['role'].capitalize()}"
            if self.current_user else ""
        )
        self.statusBar().showMessage(
            "  ·  ".join(filter(None, (self.info_base, identite)))
        )

    # ---------- actions ----------

    def _verifier_mise_a_jour(self) -> None:
        check_for_updates(self, silent=True)

    def _charger_devis(self, quote_id: int) -> None:
        if self._page("Vente rapide").charger_devis(quote_id):
            self._aller_a("Vente rapide")

    def _apres_restauration(self) -> None:
        if has_users(self.conn)
            from .security import LoginDialog

            connexion = LoginDialog(self.conn, self)
            if connexion.exec() != QDialog.DialogCode.Accepted:
                self.close()
                return
            self.current_user = connexion.user

        self._construire_pages()
        self._appliquer_permissions()
        if self.current_user:
            self._maj_statut()
        self._afficher_page_demarrage()

    def _apres_reinitialisation(self) -> None:
        ligne = self.menu.currentRow()
        self._construire_pages()
        self._appliquer_permissions()
        if ligne < 0:
            self._afficher_page_demarrage()
        else:
            self._changer_page(ligne)

    def _changer_page(self, ligne: int) -> None:
        if not 0 <= ligne < self.pile.count():
            return
        self.pile.setCurrentIndex(ligne)
        page = self.pile.widget(ligne).widget()
        if hasattr(page, "rafraichir"):
            page.rafraichir()

    def closeEvent(self, event) -> None:
        if has_users(self.conn) and not can(self.current_user, "backups"):
            event.accept()
            return

        choix = QMessageBox.question(
            self,
            "Sauvegarder avant de quitter ?",
            "Voulez-vous créer une sauvegarde de vos données avant de fermer le logiciel ?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choix == QMessageBox.StandardButton.No:
            event.accept()
            return
        if choix != QMessageBox.StandardButton.Save:
            event.ignore()
            return

        nom = f"magasin-sauvegarde-{now().strftime('%Y%m%d-%H%M%S')}.sqlite"
        chemin, _ = QFileDialog.getSaveFileName(
            self, "Créer une sauvegarde", str(data_dir() / nom),
            "Sauvegarde SQLite (*.sqlite *.db)",
        )
        if not chemin:
            event.ignore()
            return
        if not Path(chemin).suffix:
            chemin += ".sqlite"

        try:
            backup_database(
                self.conn, chemin,
                actor_user_id=self.current_user["id"] if self.current_user else None,
            )
        except (MagasinError, OSError) as erreur:
            QMessageBox.critical(
                self, "Sauvegarde impossible",
                f"La sauvegarde n'a pas pu être créée :\n{erreur}\n\n"
                "Le logiciel reste ouvert. Réessayez ou choisissez de quitter sans sauvegarder.",
            )
            event.ignore()
            return

        QMessageBox.information(
            self, "Sauvegarde créée",
            f"La sauvegarde a été enregistrée ici :\n{chemin}",
        )
        event.accept()
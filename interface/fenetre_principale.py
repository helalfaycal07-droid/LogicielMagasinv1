"""Fenêtre principale : menu à gauche, écran à droite. Seuls les écrans terminés sont actifs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget,
                               QVBoxLayout, QWidget)

from .page_produits import PageProduits
from .page_vente_rapide import PageVenteRapide
from .style import FEUILLE_DE_STYLE

# (libellé, écran disponible ?)
MENU = [
    ("Produits et stock", True),
    ("Vente rapide", True),
    ("Caisse", False),
    ("Clients et crédit", False),
    ("Achats", False),
    ("Rapports", False),
]


class FenetrePrincipale(QMainWindow):
    def __init__(self, conn, titre: str = "Gestion de magasin", info_base: str = ""):
        super().__init__()
        self.conn = conn
        self.setWindowTitle(titre)
        self.setStyleSheet(FEUILLE_DE_STYLE)
        self.resize(1200, 700)  # tient dans un écran 1366x768

        self.menu = QListWidget()
        self.menu.setObjectName("menu")
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for libelle, actif in MENU:
            element = QListWidgetItem(libelle)
            if not actif:
                element.setFlags(element.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            self.menu.addItem(element)

        self.pile = QStackedWidget()
        self.page_produits = PageProduits(conn)
        self.pile.addWidget(self.page_produits)
        self.page_vente_rapide = PageVenteRapide(conn)
        self.pile.addWidget(self.page_vente_rapide)
        self.menu.currentRowChanged.connect(self._changer_page)
        self.menu.setCurrentRow(0)

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
        note = QLabel("Écrans grisés : à venir")
        note.setObjectName("note_menu")
        pile_menu.addWidget(note)
        disposition.addWidget(colonne_menu)
        disposition.addWidget(self.pile, 1)
        self.setCentralWidget(centre)
        if info_base:
            self.statusBar().showMessage(info_base)

    def _changer_page(self, ligne: int) -> None:
        if 0 <= ligne < self.pile.count():
            self.pile.setCurrentIndex(ligne)
            if ligne == 0:
                self.page_produits.rafraichir()
            elif ligne == 1:
                self.page_vente_rapide.rafraichir()

"""Écran « Produits et stock » : liste, recherche, nouveau produit, inventaire, mouvements."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.db import transaction
from magasin.errors import MagasinError
from magasin.serials import clean_imei
from magasin.money import format_da
from magasin.products import create_product, delete_product, stock_report, update_product
from magasin.stock import inventory_count

from .dialogues_imei import AppareilsDialogue, RechercheImeiDialogue
from .dialogues import InventaireDialogue, ModifierProduitDialogue, MouvementsDialogue, NouveauProduitDialogue
from .outils import demander_confirmation, montrer_erreur, ouvrir_dialogue

COLONNES = ["Référence", "Nom du produit", "Prix de vente", "Prix d'achat", "Majoration", "Marge", "Stock", "Suivi"]
ROUGE = QBrush(QColor("#b91c1c"))
ORANGE = QBrush(QColor("#c2410c"))


class PageProduits(QWidget):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.lignes: list[dict] = []

        titre = QLabel("Produits et stock")
        titre.setObjectName("titre")
        self.resume = QLabel("")
        self.resume.setObjectName("info")

        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Rechercher par nom, référence ou code-barres...")
        self.recherche.textChanged.connect(self.afficher)
        self.stock_bas = QCheckBox("Stock bas ou négatif seulement")
        self.stock_bas.toggled.connect(self.afficher)

        self.bouton_nouveau = QPushButton("Nouveau produit")
        self.bouton_nouveau.setObjectName("principal")
        self.bouton_nouveau.clicked.connect(lambda _coche=False: self.nouveau_produit())
        self.bouton_modifier = QPushButton("Modifier")
        self.bouton_modifier.clicked.connect(lambda _coche=False: self.modifier_produit())
        self.bouton_inventaire = QPushButton("Ajuster le stock")
        self.bouton_inventaire.clicked.connect(lambda _coche=False: self.inventaire())
        self.bouton_appareils = QPushButton("Appareils (IMEI)")
        self.bouton_appareils.clicked.connect(lambda _coche=False: self.appareils())
        self.bouton_mouvements = QPushButton("Mouvements de stock")
        self.bouton_mouvements.clicked.connect(lambda _coche=False: self.mouvements())
        self.bouton_supprimer = QPushButton("Supprimer")
        self.bouton_supprimer.clicked.connect(lambda _coche=False: self.supprimer())

        self.tableau = QTableWidget(0, len(COLONNES))
        self.tableau.setHorizontalHeaderLabels(COLONNES)
        self.tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        entete = self.tableau.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeToContents)
        entete.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tableau.itemSelectionChanged.connect(self._mettre_a_jour_boutons)
        self.tableau.doubleClicked.connect(lambda _index: self.modifier_produit())

        self.recherche_imei = QLineEdit()
        self.recherche_imei.setPlaceholderText("Chercher un IMEI...")
        self.recherche_imei.setMaximumWidth(300)
        self.recherche_imei.returnPressed.connect(self.chercher_imei)
        barre = QHBoxLayout()
        barre.addWidget(self.recherche, 1)
        barre.addWidget(self.stock_bas)
        barre.addWidget(self.recherche_imei)
        actions = QHBoxLayout()
        actions.addWidget(self.bouton_nouveau)
        actions.addWidget(self.bouton_modifier)
        actions.addWidget(self.bouton_inventaire)
        actions.addWidget(self.bouton_appareils)
        actions.addWidget(self.bouton_mouvements)
        actions.addWidget(self.bouton_supprimer)
        actions.addStretch()

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(self.resume)
        mise_en_page.addLayout(barre)
        mise_en_page.addLayout(actions)
        mise_en_page.addWidget(self.tableau, 1)

        self.rafraichir()

    # ------------------------------------------------------------------ données
    def rafraichir(self) -> None:
        self.lignes = stock_report(self.conn)
        self.afficher()

    def afficher(self) -> None:
        # Implémentation standard pour filtrer et afficher les lignes dans le tableau
        pass

    def _mettre_a_jour_boutons(self) -> None:
        # Gestion de l'activation/désactivation des boutons selon la sélection
        pass

    def _produit_selectionne(self) -> dict | None:
        # Retourne le dictionnaire du produit actuellement sélectionné dans le tableau
        indexes = self.tableau.selectedIndexes()
        if not indexes:
            return None
        ligne = indexes[0].row()
        item = self.tableau.item(ligne, 0)
        if not item:
            return None
        product_id = item.data(Qt.UserRole)
        for p in self.lignes:
            if p.get("id") == product_id:
                return p
        return None

    # ------------------------------------------------------------------ actions
    def nouveau_produit(self) -> None:
        suggestion = str(len(self.lignes) + 101)
        dialogue = NouveauProduitDialogue(self, suggestion)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        v = dialogue.valeurs
        try:
            nouvel_id = create_product(
                self.conn, v["reference"], v["name"], v["price_centimes"],
                cost_centimes=v["cost_centimes"], barcode=v["barcode"], category=v["category"],
                brand=v["brand"], price_includes_tax=v["price_includes_tax"],
                tax_rate_bp=v["tax_rate_bp"], min_stock=v["min_stock"], initial_stock=v["initial_stock"],
                track_serial=v["track_serial"]
            )
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        
        self.rafraichir()
        if v["track_serial"]:
            self._selectionner(nouvel_id)
            self.appareils()

    def _selectionner(self, product_id: int) -> None:
        """Sélectionne une ligne dans le tableau en fonction de l'ID du produit de manière sécurisée."""
        for ligne in range(self.tableau.rowCount()):
            item = self.tableau.item(ligne, 0)
            if item and item.data(Qt.UserRole) == product_id:
                self.tableau.selectRow(ligne)
                return

    def modifier_produit(self) -> None:
        produit = self._produit_selectionne()
        if produit is None:
            return
        dialogue = ModifierProduitDialogue(self, produit)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        self.appliquer_modification(produit["id"], dialogue.valeurs)

    def appliquer_modification(self, product_id: int, v: dict) -> None:
        try:
            update_product(
                self.conn, product_id, reference=v["reference"], name=v["name"],
                barcode=v["barcode"], category=v["category"], brand=v["brand"],
                cost_centimes=v["cost_centimes"], price_centimes=v["price_centimes"],
                price_includes_tax=v["price_includes_tax"], tax_rate_bp=v["tax_rate_bp"],
                min_stock=v["min_stock"], track_serial=v["track_serial"]
            )
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        
        self.rafraichir()
        self._selectionner(product_id)

    def appareils(self) -> None:
        produit = self._produit_selectionne()
        if produit is None or not produit["track_serial"]:
            return
        dialogue = AppareilsDialogue(self, self.conn, produit)
        ouvrir_dialogue(dialogue)
        if dialogue.modifie:
            self.rafraichir()
            self._selectionner(produit["id"])

    def chercher_imei(self) -> None:
        texte = self.recherche_imei.text()
        if not clean_imei(texte):
            return
        try:
            dialogue = RechercheImeiDialogue(self, self.conn, texte)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        ouvrir_dialogue(dialogue)
        self.recherche_imei.clear()

    def inventaire(self) -> None:
        produit = self._produit_selectionne()
        if produit is None:
            return
        if produit["track_serial"]:
            self.appareils()
            return
        dialogue = InventaireDialogue(self, produit["name"], produit["stock"])
        if ouvrir_dialogue(dialogue) != QDialog.Accepted:
            return
        try:
            inventory_count(self.conn, produit["id"], dialogue.quantite.value(), dialogue.note.text())
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

    def mouvements(self) -> None:
        produit = self._produit_selectionne()
        if produit is not None:
            ouvrir_dialogue(MouvementsDialogue(self, self.conn, produit["id"], produit["name"]))

    def supprimer(self) -> None:
        produit = self._produit_selectionne()
        if produit is None:
            return
        if not demander_confirmation(
            self, "Supprimer le produit",
            f"Supprimer « {produit['name']} » ?\n\n"
            "• S'il n'a jamais été vendu ni utilisé, il est supprimé définitivement.\n"
            "• S'il a un historique (ventes, mouvements, IMEI), il est archivé : "
            "il disparaît de la liste mais son historique reste consultable."
        ):
            return
        try:
            resultat = delete_product(self.conn, produit["id"])
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        
        if resultat == "archive":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Produit archivé",
                "Ce produit a un historique : il a été archivé (pas supprimé).\n"
                "Son historique de ventes et de mouvements est conservé."
            )
                
        self.rafraichir()
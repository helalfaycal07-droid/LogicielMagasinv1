"""Boîtes de dialogue : nouveau produit, inventaire, mouvements d'un produit."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QComboBox, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from magasin.clock import format_dt
from magasin.errors import MagasinError, ValidationError
from magasin.forms import parse_product_form
from magasin.money import centimes_to_text, da_to_centimes, format_da, margin_percent, markup_percent, split_ttc
from magasin.stock import list_movements
from magasin.supplier_links import product_suppliers
from magasin.purchases import list_suppliers

from .outils import TYPES_MOUVEMENT, montrer_erreur


class NouveauProduitDialogue(QDialog):
    """Saisie d'un produit. Les montants se tapent en dinars : 39000 ou 39 000,00."""

    def __init__(self, parent=None, reference_suggeree: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Nouveau produit")
        self.setMinimumWidth(520)
        self.valeurs: dict | None = None
        self.avec_stock_initial = True

        self.reference = QLineEdit(reference_suggeree)
        self.nom = QLineEdit()
        self.code_barres = QLineEdit()
        self.code_barres.setPlaceholderText("facultatif")
        self.categorie = QLineEdit()
        self.marque = QLineEdit()
        self.prix = QLineEdit()
        self.prix.setPlaceholderText("ex. 39 000")
        self.cout = QLineEdit()
        self.cout.setPlaceholderText("ex. 30 000")
        self.ttc = QCheckBox("Les prix saisis sont TTC (TVA comprise)")
        self.ttc.setChecked(True)
        self.tva = QLineEdit("19")
        self.suivi_imei = QCheckBox("Suivi par IMEI (smartphones)")
        self.aide_imei = QLabel("Les téléphones s'ajoutent juste après l'enregistrement (scan ou liste collée).")
        self.aide_imei.setObjectName("info")
        self.aide_imei.setWordWrap(True)
        self.aide_imei.setMinimumHeight(44)
        self.aide_imei.setVisible(False)
        self.stock_min = QLineEdit("0")
        self.stock_initial = QLineEdit("0")
        self.indicateurs = QLabel("")
        self.indicateurs.setObjectName("info")
        self.indicateurs.setWordWrap(True)
        self.indicateurs.setMinimumHeight(60)  # place pour les deux lignes de calcul

        formulaire = self.formulaire = QFormLayout()
        formulaire.addRow("Référence *", self.reference)
        formulaire.addRow("Nom du produit *", self.nom)
        formulaire.addRow("Code-barres", self.code_barres)
        formulaire.addRow("Catégorie", self.categorie)
        formulaire.addRow("Marque", self.marque)
        formulaire.addRow("Prix de vente (DA) *", self.prix)
        formulaire.addRow("Prix d'achat (DA)", self.cout)
        formulaire.addRow("", self.ttc)
        formulaire.addRow("TVA (%)", self.tva)
        formulaire.addRow("", self.indicateurs)
        formulaire.addRow("", self.suivi_imei)
        formulaire.addRow("", self.aide_imei)
        formulaire.addRow("Stock minimum", self.stock_min)
        formulaire.addRow("Stock initial", self.stock_initial)

        boutons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        boutons.button(QDialogButtonBox.Save).setText("Enregistrer")
        boutons.button(QDialogButtonBox.Save).setObjectName("principal")
        boutons.button(QDialogButtonBox.Cancel).setText("Annuler")
        boutons.accepted.connect(self._valider)
        boutons.rejected.connect(self.reject)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addLayout(formulaire)
        mise_en_page.addWidget(boutons)

        for champ in (self.prix, self.cout, self.tva):
            champ.textChanged.connect(self._mettre_a_jour_indicateurs)
        self.ttc.toggled.connect(self._mettre_a_jour_indicateurs)
        self.suivi_imei.toggled.connect(self._suivi_change)

    def _suivi_change(self, actif: bool) -> None:
        """Un produit suivi par IMEI n'a pas de stock initial : les appareils s'ajoutent un par un."""
        self.aide_imei.setVisible(actif and self.avec_stock_initial)
        if self.avec_stock_initial:
            self.stock_initial.setEnabled(not actif)
            if actif:
                self.stock_initial.setText("0")

    def _mettre_a_jour_indicateurs(self) -> None:
        try:
            prix = da_to_centimes(self.prix.text()) if self.prix.text().strip() else None
            cout = da_to_centimes(self.cout.text()) if self.cout.text().strip() else None
        except ValidationError:
            self.indicateurs.setText("")
            return
        morceaux = []
        if prix and cout:
            morceaux.append(f"Majoration {markup_percent(cout, prix)} %  ·  Marge {margin_percent(cout, prix)} %")
        if prix and self.ttc.isChecked():
            try:
                ht, tva = split_ttc(prix, int(float(self.tva.text().replace(",", ".") or 0) * 100))
                morceaux.append(f"Soit {format_da(ht)} HT + {format_da(tva)} de TVA")
            except ValueError:
                pass
        self.indicateurs.setText("\n".join(morceaux))

    def _valider(self) -> None:
        try:
            self.valeurs = parse_product_form(
                reference=self.reference.text(), name=self.nom.text(), price_text=self.prix.text(),
                cost_text=self.cout.text(), barcode=self.code_barres.text(), category=self.categorie.text(),
                brand=self.marque.text(), tax_percent_text=self.tva.text(),
                min_stock_text=self.stock_min.text(),
                initial_stock_text=self.stock_initial.text() if self.avec_stock_initial else "0",
                price_includes_tax=self.ttc.isChecked(), track_serial=self.suivi_imei.isChecked())
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        self.accept()


class ModifierProduitDialogue(NouveauProduitDialogue):
    """Correction d'un produit existant (prix, nom, TVA...). Le stock se corrige par l'inventaire."""

    def __init__(self, parent, produit: dict, conn):
        super().__init__(parent, produit["reference"])
        self.setWindowTitle(f"Modifier le produit — {produit['name']}")
        self.nom.setText(produit["name"])
        self.code_barres.setText(produit["barcode"])
        self.categorie.setText(produit["category"])
        self.marque.setText(produit["brand"])
        self.prix.setText(centimes_to_text(produit["price_centimes"]))
        self.cout.setText(centimes_to_text(produit["cost_centimes"]) if produit["cost_centimes"] else "")
        self.ttc.setChecked(produit["price_includes_tax"])
        self.tva.setText(centimes_to_text(produit["tax_rate_bp"]))
        self.stock_min.setText(str(produit["min_stock"]))
        self.suivi_imei.setChecked(produit["track_serial"])
        fournisseurs_lies = product_suppliers(conn, produit["id"])
        ids_existants = {fournisseur["id"] for fournisseur in fournisseurs_lies}
        self.fournisseur = QComboBox()
        if len(fournisseurs_lies) > 1:
            self.fournisseur.addItem(
                "Plusieurs fournisseurs — ne pas modifier", None
            )
        self.fournisseur.addItem("Aucun fournisseur", 0)
        fournisseurs = list_suppliers(conn)
        for fournisseur in fournisseurs:
            if fournisseur["active"] or fournisseur["id"] in ids_existants:
                libelle = fournisseur["name"]
                if not fournisseur["active"]:
                    libelle += " (archivé)"
                self.fournisseur.addItem(libelle, fournisseur["id"])
        if len(fournisseurs_lies) == 1:
            self.fournisseur.setCurrentIndex(
                self.fournisseur.findData(fournisseurs_lies[0]["id"])
            )
        elif len(fournisseurs_lies) > 1:
            self.fournisseur.setCurrentIndex(0)
        else:
            self.fournisseur.setCurrentIndex(self.fournisseur.findData(0))
        self.formulaire.addRow("Fournisseur", self.fournisseur)
        if produit["stock"] != 0 or produit["track_serial"]:
            self.suivi_imei.setEnabled(False)  # ne change que lorsque le stock est à 0 et sans appareil
            self.suivi_imei.setToolTip("Le suivi par IMEI ne peut changer que lorsque le stock est à 0.")
        self.avec_stock_initial = False
        self.formulaire.removeRow(self.stock_initial)
        note = QLabel(f"Stock actuel : {produit['stock']}. Pour le corriger, utilisez le bouton « Inventaire ».")
        note.setObjectName("info")
        note.setWordWrap(True)
        self.formulaire.addRow("", note)
        self._mettre_a_jour_indicateurs()

    def _valider(self) -> None:
        super()._valider()
        if self.valeurs is not None:
            self.valeurs["supplier_id"] = self.fournisseur.currentData()


class InventaireDialogue(QDialog):
    """On saisit la quantité comptée ; le logiciel crée le mouvement de l'écart."""

    def __init__(self, parent, nom_produit: str, stock_actuel: int):
        super().__init__(parent)
        self.setWindowTitle("Inventaire")
        self.setMinimumWidth(420)
        self.quantite = QSpinBox()
        self.quantite.setRange(0, 1_000_000)
        self.quantite.setValue(max(stock_actuel, 0))
        self.note = QLineEdit()
        self.note.setPlaceholderText("ex. comptage du samedi (facultatif)")
        formulaire = QFormLayout()
        formulaire.addRow("Produit", QLabel(nom_produit))
        formulaire.addRow("Stock actuel (calculé)", QLabel(str(stock_actuel)))
        formulaire.addRow("Quantité réellement comptée", self.quantite)
        formulaire.addRow("Note", self.note)
        boutons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        boutons.button(QDialogButtonBox.Save).setText("Enregistrer l'inventaire")
        boutons.button(QDialogButtonBox.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        mise_en_page = QVBoxLayout(self)
        mise_en_page.addLayout(formulaire)
        mise_en_page.addWidget(boutons)


class MouvementsDialogue(QDialog):
    """Journal des mouvements d'un produit : date, type, avant, quantité, après, lot, document."""

    COLONNES = ["Date", "Type", "Avant", "Quantité", "Après", "Lot", "Référence"]

    def __init__(self, parent, conn, product_id: int, nom_produit: str):
        super().__init__(parent)
        self.setWindowTitle(f"Mouvements de stock — {nom_produit}")
        self.resize(820, 420)
        tableau = QTableWidget(0, len(self.COLONNES))
        tableau.setHorizontalHeaderLabels(self.COLONNES)
        tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        tableau.setAlternatingRowColors(True)
        tableau.verticalHeader().setVisible(False)
        tableau.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tableau.horizontalHeader().setStretchLastSection(True)
        mouvements = list(reversed(list_movements(conn, product_id)))  # le plus récent en haut
        tableau.setRowCount(len(mouvements))
        for ligne, m in enumerate(mouvements):
            valeurs = [format_dt(m["created_at"]), TYPES_MOUVEMENT.get(m["movement_type"], m["movement_type"]),
                       str(m["qty_before"]), f"{m['quantity']:+d}", str(m["qty_after"]),
                       m["lot"] or "", m["ref_number"] or m["note"] or ""]
            for colonne, texte in enumerate(valeurs):
                case = QTableWidgetItem(texte)
                if colonne in (2, 3, 4):
                    case.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if colonne == 3:
                    case.setForeground(Qt.darkGreen if m["quantity"] > 0 else Qt.red)
                tableau.setItem(ligne, colonne, case)
        fermer = QPushButton("Fermer")
        fermer.clicked.connect(self.accept)
        pied = QHBoxLayout()
        pied.addStretch()
        pied.addWidget(fermer)
        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(tableau)
        mise_en_page.addLayout(pied)

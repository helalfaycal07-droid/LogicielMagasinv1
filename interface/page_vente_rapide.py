"""Écran de vente rapide : recherche, panier, paiements et validation d'une vente."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog)

from magasin.errors import MagasinError, ValidationError
from magasin.money import da_to_centimes, format_da
from magasin.products import find_product
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import clean_imei, validate_imei

from .outils import demander_confirmation, montrer_erreur

MODES_PAIEMENT = (
    ("Espèces", "especes"),
    ("Carte", "carte"),
    ("CCP", "ccp"),
    ("Virement", "virement"),
    ("Chèque", "cheque"),
    ("Avoir", "avoir"),
)


class PageVenteRapide(QWidget):
    """Interface de vente au comptoir, sans dupliquer les règles métier de magasin.sales."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.panier: list[dict] = []
        self.paiements: list[PaymentIn] = []

        titre = QLabel("Vente rapide")
        titre.setObjectName("titre")

        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Scanner ou saisir un code-barres, une référence ou un nom...")
        self.recherche.returnPressed.connect(self.ajouter_produit)
        self.quantite = QSpinBox()
        self.quantite.setRange(1, 9999)
        self.quantite.setValue(1)
        self.bouton_ajouter = QPushButton("Ajouter au panier")
        self.bouton_ajouter.setObjectName("principal")
        self.bouton_ajouter.clicked.connect(self.ajouter_produit)

        barre_produit = QHBoxLayout()
        barre_produit.addWidget(self.recherche, 1)
        barre_produit.addWidget(QLabel("Quantité"))
        barre_produit.addWidget(self.quantite)
        barre_produit.addWidget(self.bouton_ajouter)

        self.tableau_panier = QTableWidget(0, 5)
        self.tableau_panier.setHorizontalHeaderLabels(
            ["Référence", "Produit", "Quantité", "Prix unitaire", "Total"]
        )
        self.tableau_panier.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau_panier.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau_panier.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tableau_panier.setAlternatingRowColors(True)
        self.tableau_panier.verticalHeader().setVisible(False)
        entete = self.tableau_panier.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeToContents)
        entete.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tableau_panier.itemSelectionChanged.connect(self._mettre_a_jour_boutons)

        self.bouton_retirer = QPushButton("Retirer la ligne")
        self.bouton_retirer.clicked.connect(self.retirer_ligne)
        self.bouton_vider = QPushButton("Vider le panier")
        self.bouton_vider.clicked.connect(self.vider_panier)
        actions_panier = QHBoxLayout()
        actions_panier.addWidget(self.bouton_retirer)
        actions_panier.addWidget(self.bouton_vider)
        actions_panier.addStretch()

        self.client = QComboBox()
        self._charger_clients()
        self.client.currentIndexChanged.connect(lambda: self._mettre_a_jour_resume())
        self.remise = QLineEdit("0")
        self.remise.setPlaceholderText("0,00")
        self.remise.setMaximumWidth(150)
        self.remise.textChanged.connect(lambda: self._mettre_a_jour_resume())

        formulaire = QFormLayout()
        formulaire.addRow("Client (facultatif)", self.client)
        formulaire.addRow("Remise totale (DA)", self.remise)

        self.mode_paiement = QComboBox()
        for libelle, code in MODES_PAIEMENT:
            self.mode_paiement.addItem(libelle, code)
        self.montant_paiement = QLineEdit()
        self.montant_paiement.setPlaceholderText("Montant en DA")
        self.montant_paiement.setMaximumWidth(170)
        self.montant_paiement.returnPressed.connect(self.ajouter_paiement)
        self.bouton_ajouter_paiement = QPushButton("Ajouter paiement")
        self.bouton_ajouter_paiement.clicked.connect(self.ajouter_paiement)

        barre_paiement = QHBoxLayout()
        barre_paiement.addWidget(QLabel("Mode"))
        barre_paiement.addWidget(self.mode_paiement)
        barre_paiement.addWidget(QLabel("Montant reçu (DA)"))
        barre_paiement.addWidget(self.montant_paiement)
        barre_paiement.addWidget(self.bouton_ajouter_paiement)

        self.tableau_paiements = QTableWidget(0, 2)
        self.tableau_paiements.setHorizontalHeaderLabels(["Mode de paiement", "Montant reçu"])
        self.tableau_paiements.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau_paiements.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau_paiements.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tableau_paiements.setMaximumHeight(150)
        self.tableau_paiements.verticalHeader().setVisible(False)
        self.tableau_paiements.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tableau_paiements.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tableau_paiements.itemSelectionChanged.connect(self._mettre_a_jour_boutons)

        self.bouton_retirer_paiement = QPushButton("Retirer le paiement sélectionné")
        self.bouton_retirer_paiement.clicked.connect(self.retirer_paiement)
        self.resume = QLabel("")
        self.resume.setObjectName("info")
        self.resume.setWordWrap(True)

        self.bouton_valider = QPushButton("Valider la vente")
        self.bouton_valider.setObjectName("principal")
        self.bouton_valider.clicked.connect(self.valider_vente)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addLayout(barre_produit)
        mise_en_page.addWidget(self.tableau_panier, 1)
        mise_en_page.addLayout(actions_panier)
        mise_en_page.addLayout(formulaire)
        mise_en_page.addLayout(barre_paiement)
        mise_en_page.addWidget(self.tableau_paiements)
        mise_en_page.addWidget(self.bouton_retirer_paiement)
        mise_en_page.addWidget(self.resume)
        mise_en_page.addWidget(self.bouton_valider, alignment=Qt.AlignRight)

        self._rafraichir_panier()
        self._rafraichir_paiements()
        self._mettre_a_jour_resume()

    def _charger_clients(self) -> None:
        self.client.clear()
        self.client.addItem("Aucun client", None)
        clients = self.conn.execute(
            "SELECT id, name, phone FROM customers WHERE status <> 'archive' ORDER BY name"
        ).fetchall()
        for client in clients:
            libelle = client["name"]
            if client["phone"]:
                libelle += f" — {client['phone']}"
            self.client.addItem(libelle, client["id"])

    def rafraichir(self) -> None:
        """À appeler quand la page est rouverte, notamment après une vente."""
        self._charger_clients()
        self._mettre_a_jour_resume()

    def ajouter_produit(self) -> None:
        texte = self.recherche.text().strip()
        if not texte:
            montrer_erreur(self, ValidationError("Saisissez ou scannez un produit."))
            return

        produits = [produit for produit in find_product(self.conn, texte) if not produit["archived"]]
        if not produits:
            montrer_erreur(self, ValidationError(f"Aucun produit actif trouvé pour « {texte} »."))
            return
        if len(produits) == 1:
            produit = produits[0]
        else:
            choix = [f"{p['reference']} — {p['name']} — {format_da(p['price_centimes'])}" for p in produits]
            libelle, accepte = QInputDialog.getItem(self, "Choisir un produit", "Résultats :", choix, 0, False)
            if not accepte:
                return
            produit = produits[choix.index(libelle)]

        quantite = self.quantite.value()
        imeis: list[str] = []
        if produit["track_serial"]:
            texte_imeis, accepte = QInputDialog.getMultiLineText(
                self, "IMEI des appareils vendus",
                f"Saisissez un IMEI par ligne pour « {produit['name']} » :",
            )
            if not accepte:
                return
            try:
                imeis = [validate_imei(ligne, f"IMEI {index}")
                         for index, ligne in enumerate(texte_imeis.splitlines(), start=1) if ligne.strip()]
                if not imeis:
                    raise ValidationError("Saisissez au moins un IMEI.")
                quantite = len(imeis)
                deja_ajoutes = {
                    clean_imei(imei)
                    for ligne in self.panier
                    for imei in ligne["imeis"]
                }
                doublons = [imei for imei in imeis if clean_imei(imei) in deja_ajoutes]
                if doublons:
                    raise ValidationError(f"L'IMEI {doublons[0]} est déjà présent dans le panier.")
            except MagasinError as erreur:
                montrer_erreur(self, erreur)
                return

        prix = produit["price_centimes"]
        if not produit["track_serial"]:
            ligne_existante = next(
                (ligne for ligne in self.panier
                 if ligne["product_id"] == produit["id"] and ligne["unit_price_centimes"] == prix),
                None,
            )
            if ligne_existante is not None:
                ligne_existante["quantity"] += quantite
            else:
                self.panier.append({
                    "product_id": produit["id"],
                    "reference": produit["reference"],
                    "name": produit["name"],
                    "quantity": quantite,
                    "unit_price_centimes": prix,
                    "imeis": [],
                })
        else:
            self.panier.append({
                "product_id": produit["id"],
                "reference": produit["reference"],
                "name": produit["name"],
                "quantity": quantite,
                "unit_price_centimes": prix,
                "imeis": imeis,
            })

        self.recherche.clear()
        self.quantite.setValue(1)
        self._rafraichir_panier()

    def _rafraichir_panier(self) -> None:
        self.tableau_panier.setRowCount(len(self.panier))
        for index, ligne in enumerate(self.panier):
            valeurs = (
                ligne["reference"],
                ligne["name"],
                str(ligne["quantity"]),
                format_da(ligne["unit_price_centimes"]),
                format_da(ligne["quantity"] * ligne["unit_price_centimes"]),
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                cellule.setData(Qt.UserRole, index)
                if colonne >= 2:
                    cellule.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau_panier.setItem(index, colonne, cellule)
        self._mettre_a_jour_boutons()
        self._mettre_a_jour_resume()

    def retirer_ligne(self) -> None:
        ligne = self.tableau_panier.currentRow()
        if 0 <= ligne < len(self.panier):
            del self.panier[ligne]
            self._rafraichir_panier()

    def vider_panier(self) -> None:
        if not self.panier:
            return
        if not demander_confirmation(self, "Vider le panier", "Retirer tous les produits du panier ?"):
            return
        self.panier.clear()
        self._rafraichir_panier()

    def ajouter_paiement(self) -> None:
        try:
            montant = da_to_centimes(self.montant_paiement.text())
            if montant <= 0:
                raise ValidationError("Le montant du paiement doit être supérieur à zéro.")
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        self.paiements.append(PaymentIn(self.mode_paiement.currentData(), montant))
        self.montant_paiement.clear()
        self._rafraichir_paiements()

    def _rafraichir_paiements(self) -> None:
        self.tableau_paiements.setRowCount(len(self.paiements))
        libelles = dict((code, libelle) for libelle, code in MODES_PAIEMENT)
        for index, paiement in enumerate(self.paiements):
            mode = QTableWidgetItem(libelles[paiement.method])
            mode.setData(Qt.UserRole, index)
            montant = QTableWidgetItem(format_da(paiement.amount_centimes))
            montant.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tableau_paiements.setItem(index, 0, mode)
            self.tableau_paiements.setItem(index, 1, montant)
        self._mettre_a_jour_boutons()
        self._mettre_a_jour_resume()

    def retirer_paiement(self) -> None:
        ligne = self.tableau_paiements.currentRow()
        if 0 <= ligne < len(self.paiements):
            del self.paiements[ligne]
            self._rafraichir_paiements()

    def _total(self) -> int:
        return sum(ligne["quantity"] * ligne["unit_price_centimes"] for ligne in self.panier)

    def _remise_centimes(self) -> int:
        return da_to_centimes(self.remise.text() or "0")

    def _mettre_a_jour_resume(self) -> None:
        total_brut = self._total()
        try:
            remise = self._remise_centimes()
            if remise < 0:
                raise ValidationError("La remise ne peut pas être négative.")
            total = total_brut - remise
            if total < 0:
                raise ValidationError("La remise dépasse le total du panier.")
        except MagasinError as erreur:
            self.resume.setText(str(erreur))
            self.bouton_valider.setEnabled(False)
            return

        tendered = sum(paiement.amount_centimes for paiement in self.paiements)
        change = max(0, tendered - total)
        cash_total = sum(p.amount_centimes for p in self.paiements if p.method == "especes")
        paid = tendered - change
        due = total - paid
        morceaux = [
            f"Total : {format_da(total)}",
            f"Reçu : {format_da(tendered)}",
            f"Rendu : {format_da(change)}",
            f"Reste à payer / crédit : {format_da(due)}",
        ]
        if change > cash_total:
            morceaux.append("Le rendu ne peut être supérieur au montant reçu en espèces.")
        if due > 0 and self.client.currentData() is None:
            morceaux.append("Un client identifié et autorisé est nécessaire pour vendre à crédit.")
        self.resume.setText("  ·  ".join(morceaux))
        self.bouton_valider.setEnabled(bool(self.panier))

    def _mettre_a_jour_boutons(self) -> None:
        self.bouton_retirer.setEnabled(self.tableau_panier.currentRow() >= 0)
        self.bouton_vider.setEnabled(bool(self.panier))
        self.bouton_retirer_paiement.setEnabled(self.tableau_paiements.currentRow() >= 0)

    def valider_vente(self) -> None:
        if not self.panier:
            montrer_erreur(self, ValidationError("Le panier est vide."))
            return
        try:
            remise = self._remise_centimes()
            if remise < 0:
                raise ValidationError("La remise ne peut pas être négative.")
            if remise > self._total():
                raise ValidationError("La remise dépasse le total du panier.")
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        resume = self.resume.text()
        if not demander_confirmation(self, "Valider la vente", f"Confirmer cette vente ?\n\n{resume}"):
            return

        lignes = [
            Line(
                product_id=ligne["product_id"],
                quantity=ligne["quantity"],
                unit_price_centimes=ligne["unit_price_centimes"],
                imeis=ligne["imeis"] or None,
            )
            for ligne in self.panier
        ]
        try:
            resultat = create_sale(
                self.conn,
                lignes,
                list(self.paiements),
                customer_id=self.client.currentData(),
                discount_centimes=remise,
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        self.panier.clear()
        self.paiements.clear()
        self.remise.setText("0")
        self._rafraichir_panier()
        self._rafraichir_paiements()
        self._mettre_a_jour_resume()

        message = (
            f"Vente {resultat.number} enregistrée.\n\n"
            f"Total : {format_da(resultat.total_centimes)}\n"
            f"Payé : {format_da(resultat.paid_centimes)}\n"
            f"Reste dû : {format_da(resultat.due_centimes)}\n"
            f"Rendu : {format_da(resultat.change_centimes)}"
        )
        QMessageBox.information(self, "Vente enregistrée", message)
        if resultat.warnings:
            QMessageBox.warning(self, "Avertissement de stock", "\n".join(resultat.warnings))

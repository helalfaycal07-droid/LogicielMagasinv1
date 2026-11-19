"""Interface de recherche et d'enregistrement des retours de vente."""

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.clock import format_dt
from magasin.errors import MagasinError, ValidationError
from magasin.money import format_da
from magasin.returns import (ReturnLine, create_return, get_sale_for_return,
                             list_sales_for_return, preview_return)

from .outils import demander_confirmation, montrer_erreur


class PageRetours(QWidget):
    """Permet un retour partiel ou total avec contrôle du remboursement et du stock."""

    PAGE_SIZE = 50

    def __init__(self, conn, actor_user_id: int | None = None):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id
        self.vente: dict | None = None
        self.page_recherche_ventes = 0
        self._page_suivante_disponible = False

        titre = QLabel("Retours, remboursements et avoirs")
        titre.setObjectName("titre")

        self.numero_vente = QLineEdit()
        self.numero_vente.setPlaceholderText("N° de vente, par exemple VTE-000001")
        self.numero_vente.returnPressed.connect(self.charger_vente)
        self.bouton_charger = QPushButton("Rechercher la vente")
        self.bouton_charger.clicked.connect(self.charger_vente)
        self.recherche_ventes = QLineEdit()
        self.recherche_ventes.setPlaceholderText("Numéro partiel ou nom du client")
        self.recherche_ventes.returnPressed.connect(self._rechercher_ventes)
        self.bouton_filtrer_ventes = QPushButton("Filtrer les ventes")
        self.bouton_filtrer_ventes.clicked.connect(self._rechercher_ventes)
        self.bouton_ventes_recentes = QPushButton("Ventes récentes")
        self.bouton_ventes_recentes.clicked.connect(self._afficher_ventes_recentes)
        self.ventes_recentes = QComboBox()
        self.ventes_recentes.setMinimumContentsLength(48)
        self.ventes_recentes.currentIndexChanged.connect(self._selectionner_vente)
        recherche = QHBoxLayout()
        recherche.addWidget(self.numero_vente, 1)
        recherche.addWidget(self.bouton_charger)

        recherche_ventes = QHBoxLayout()
        recherche_ventes.addWidget(QLabel("Retrouver une vente :"))
        recherche_ventes.addWidget(self.recherche_ventes, 1)
        recherche_ventes.addWidget(self.bouton_filtrer_ventes)
        recherche_ventes.addWidget(self.bouton_ventes_recentes)

        selection = QHBoxLayout()
        selection.addWidget(QLabel("Résultats :"))
        selection.addWidget(self.ventes_recentes, 1)

        pagination = QHBoxLayout()
        self.bouton_page_precedente = QPushButton("Précédent")
        self.bouton_page_precedente.clicked.connect(self._page_precedente)
        self.indicateur_page = QLabel("Page 1")
        self.bouton_page_suivante = QPushButton("Suivant")
        self.bouton_page_suivante.clicked.connect(self._page_suivante)
        pagination.addStretch()
        pagination.addWidget(self.bouton_page_precedente)
        pagination.addWidget(self.indicateur_page)
        pagination.addWidget(self.bouton_page_suivante)

        self.info_vente = QLabel("Recherchez une vente pour afficher les articles retournables.")
        self.info_vente.setObjectName("info")
        self.info_vente.setWordWrap(True)

        self.tableau = QTableWidget(0, 6)
        self.tableau.setHorizontalHeaderLabels([
            "Référence", "Produit", "Vendu", "Déjà retourné", "À retourner", "IMEI(s) à rendre",
        ])
        self.tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        self.tableau.horizontalHeader().setStretchLastSection(True)

        self.note_stock = QLabel(
            "Cochez « Remettre en stock » seulement pour un article vérifié et revendable. "
            "Un appareil IMEI exige la saisie de son IMEI."
        )
        self.note_stock.setObjectName("info")
        self.note_stock.setWordWrap(True)

        self.montant = QLabel("Montant du retour : 0,00 DA")
        self.montant.setObjectName("titre")
        self.reduction_dette = QLabel("Déduit de la dette : 0,00 DA")
        self.remboursement = QLabel("À rembourser : 0,00 DA")

        self.mode_remboursement = QComboBox()
        self.mode_remboursement.addItem("Espèces", "especes")
        self.mode_remboursement.addItem("Avoir client", "avoir")
        self.raison = QLineEdit()
        self.raison.setPlaceholderText("Motif du retour (obligatoire)")
        formulaire = QFormLayout()
        formulaire.addRow("Mode de remboursement", self.mode_remboursement)
        formulaire.addRow("Motif", self.raison)

        self.bouton_valider = QPushButton("Valider le retour")
        self.bouton_valider.setObjectName("principal")
        self.bouton_valider.clicked.connect(self.valider_retour)
        self.bouton_valider.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addLayout(recherche)
        layout.addLayout(recherche_ventes)
        layout.addLayout(selection)
        layout.addLayout(pagination)
        layout.addWidget(self.info_vente)
        layout.addWidget(self.tableau, 1)
        layout.addWidget(self.note_stock)
        layout.addWidget(self.montant)
        layout.addWidget(self.reduction_dette)
        layout.addWidget(self.remboursement)
        layout.addLayout(formulaire)
        layout.addWidget(self.bouton_valider)
        self.rafraichir_ventes_recentes()

    def rafraichir(self) -> None:
        self.rafraichir_ventes_recentes()
        if self.vente is not None:
            self.charger_vente()

    def rafraichir_ventes_recentes(self) -> None:
        current_number = (
            self.ventes_recentes.currentData()
            if hasattr(self, "ventes_recentes")
            else None
        )
        self.ventes_recentes.blockSignals(True)
        try:
            self.ventes_recentes.clear()
            sales = list_sales_for_return(
                self.conn,
                limit=self.PAGE_SIZE + 1,
                search=self.recherche_ventes.text(),
                offset=self.page_recherche_ventes * self.PAGE_SIZE,
            )
            self._page_suivante_disponible = len(sales) > self.PAGE_SIZE
            sales = sales[:self.PAGE_SIZE]
            placeholder = (
                "Sélectionner une vente récente…"
                if not self.recherche_ventes.text().strip()
                else "Aucune vente correspondante"
            )
            if sales:
                placeholder = "Sélectionner une vente…"
            self.ventes_recentes.addItem(placeholder, None)
            for sale in sales:
                customer = sale["customer_name"] or "Client comptoir"
                label = (
                    f"{sale['number']}  ·  {format_dt(sale['created_at'])}  ·  "
                    f"{customer}  ·  {format_da(sale['total_centimes'])}"
                )
                self.ventes_recentes.addItem(label, sale["number"])
            if current_number:
                index = self.ventes_recentes.findData(current_number)
                if index >= 0:
                    self.ventes_recentes.setCurrentIndex(index)
        finally:
            self.ventes_recentes.blockSignals(False)
        self.indicateur_page.setText(f"Page {self.page_recherche_ventes + 1}")
        self.bouton_page_precedente.setEnabled(self.page_recherche_ventes > 0)
        self.bouton_page_suivante.setEnabled(self._page_suivante_disponible)

    def _rechercher_ventes(self) -> None:
        self.page_recherche_ventes = 0
        self.rafraichir_ventes_recentes()

    def _afficher_ventes_recentes(self) -> None:
        self.recherche_ventes.clear()
        self.page_recherche_ventes = 0
        self.rafraichir_ventes_recentes()

    def _page_precedente(self) -> None:
        if self.page_recherche_ventes == 0:
            return
        self.page_recherche_ventes -= 1
        self.rafraichir_ventes_recentes()

    def _page_suivante(self) -> None:
        if not self._page_suivante_disponible:
            return
        self.page_recherche_ventes += 1
        self.rafraichir_ventes_recentes()

    def _selectionner_vente(self, index: int) -> None:
        sale_number = self.ventes_recentes.itemData(index)
        if not sale_number:
            return
        self.numero_vente.setText(sale_number)
        self.charger_vente()

    def charger_vente(self) -> None:
        try:
            data = get_sale_for_return(self.conn, self.numero_vente.text())
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        if data is None:
            self.vente = None
            self.ventes_recentes.blockSignals(True)
            self.ventes_recentes.setCurrentIndex(0)
            self.ventes_recentes.blockSignals(False)
            self.tableau.setRowCount(0)
            self.info_vente.setText("Vente introuvable. Vérifiez le numéro indiqué sur le ticket.")
            self.montant.setText("Montant du retour : 0,00 DA")
            self.reduction_dette.setText("Déduit de la dette : 0,00 DA")
            self.remboursement.setText("À rembourser : 0,00 DA")
            self.bouton_valider.setEnabled(False)
            return

        self.vente = data
        sale = data["sale"]
        index = self.ventes_recentes.findData(sale["number"])
        if index >= 0 and index != self.ventes_recentes.currentIndex():
            self.ventes_recentes.blockSignals(True)
            self.ventes_recentes.setCurrentIndex(index)
            self.ventes_recentes.blockSignals(False)
        details = [
            f"Vente {sale['number']} — {format_dt(sale['created_at'])}",
            f"Total initial : {format_da(sale['total_centimes'])}",
        ]
        if sale["customer_name"]:
            details.append(f"Client : {sale['customer_name']}")
        self.info_vente.setText("  ·  ".join(details))
        self.tableau.setRowCount(len(data["items"]))
        for row, item in enumerate(data["items"]):
            values = [
                item["reference"],
                item["product_name"],
                str(item["quantity"]),
                str(item["returned_quantity"]),
            ]
            for column, value in enumerate(values):
                self.tableau.setItem(row, column, QTableWidgetItem(value))

            quantity = QSpinBox()
            quantity.setRange(0, max(0, item["quantity"] - item["returned_quantity"]))
            quantity.valueChanged.connect(self._rafraichir_apercu)
            self.tableau.setCellWidget(row, 4, quantity)

            imeis = QLineEdit()
            imeis.setPlaceholderText("IMEI, un par appareil")
            imeis.setEnabled(bool(item["track_serial"]))
            self.tableau.setCellWidget(row, 5, imeis)

            resalable = QCheckBox("Remettre en stock")
            resalable.stateChanged.connect(self._rafraichir_apercu)
            self.tableau.setCellWidget(row, 5, self._cellule_stock(imeis, resalable))
        self.raison.clear()
        self._rafraichir_apercu()

    @staticmethod
    def _cellule_stock(imeis: QLineEdit, resalable: QCheckBox) -> QWidget:
        cellule = QWidget()
        layout = QVBoxLayout(cellule)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(imeis)
        layout.addWidget(resalable)
        return cellule

    def _lignes_selectionnees(self, *, verifier_imeis: bool = True) -> list[ReturnLine]:
        if self.vente is None:
            return []
        lignes = []
        for row, item in enumerate(self.vente["items"]):
            quantity = self.tableau.cellWidget(row, 4).value()
            if quantity <= 0:
                continue
            cell = self.tableau.cellWidget(row, 5)
            imei_field = cell.findChild(QLineEdit)
            resalable = cell.findChild(QCheckBox)
            imei_values = []
            if verifier_imeis and item["track_serial"]:
                imei_values = [
                    value.strip()
                    for value in imei_field.text().replace("\n", ",").split(",")
                    if value.strip()
                ]
            lignes.append(ReturnLine(
                item["id"], quantity, resalable.isChecked(), imei_values
            ))
        return lignes

    def _rafraichir_apercu(self, *_args) -> None:
        lignes = self._lignes_selectionnees(verifier_imeis=False)
        if self.vente is None or not lignes:
            self.montant.setText("Montant du retour : 0,00 DA")
            self.reduction_dette.setText("Déduit de la dette : 0,00 DA")
            self.remboursement.setText("À rembourser : 0,00 DA")
            self.bouton_valider.setEnabled(False)
            return
        try:
            data = preview_return(self.conn, self.vente["sale"]["id"], lignes)
        except MagasinError as erreur:
            self.montant.setText(f"Aperçu impossible : {erreur}")
            self.bouton_valider.setEnabled(False)
            return
        self.montant.setText(f"Montant du retour : {format_da(data['total_centimes'])}")
        self.reduction_dette.setText(
            f"Déduit de la dette : {format_da(data['debt_reduction_centimes'])}"
        )
        self.remboursement.setText(f"À rembourser : {format_da(data['refund_centimes'])}")
        self.bouton_valider.setEnabled(True)

    def valider_retour(self) -> None:
        if self.vente is None:
            return
        lines = self._lignes_selectionnees()
        if not lines:
            montrer_erreur(self, ValidationError("Sélectionnez une quantité à retourner."))
            return
        if not self.raison.text().strip():
            montrer_erreur(self, ValidationError("Une raison est obligatoire pour le retour."))
            return
        try:
            amounts = preview_return(self.conn, self.vente["sale"]["id"], lines)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        confirmation = demander_confirmation(
            self, "Confirmer le retour",
            f"Valeur retournée : {format_da(amounts['total_centimes'])}\n"
            f"Déduit de la dette : {format_da(amounts['debt_reduction_centimes'])}\n"
            f"À rembourser : {format_da(amounts['refund_centimes'])}\n\n"
            "Enregistrer cette opération ?",
        )
        if not confirmation:
            return
        try:
            result = create_return(
                self.conn,
                self.vente["sale"]["id"],
                lines,
                self.mode_remboursement.currentData(),
                self.raison.text(),
                actor_user_id=self.actor_user_id,
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        QMessageBox.information(
            self, "Retour enregistré",
            f"Retour {result['number']} enregistré.\n"
            f"Montant : {format_da(result['total_centimes'])}\n"
            f"Remboursé : {format_da(result['refund_centimes'])}",
        )
        self.rafraichir_ventes_recentes()
        self.charger_vente()

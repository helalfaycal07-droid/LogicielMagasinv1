"""Aperçu de la liaison des fiches fournisseurs avec les produits existants."""

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from magasin.supplier_links import SupplierLinkPreview


class ApercuRattachementFournisseursDialogue(QDialog):
    def __init__(self, parent, preview: SupplierLinkPreview):
        super().__init__(parent)
        self.setWindowTitle("Vérifier le rattachement des fournisseurs")
        self.resize(900, 580)

        summary = QLabel(
            f"{len(preview.linkable_rows)} à associer · "
            f"{preview.count('Déjà associé')} déjà associé(s) · "
            f"{preview.count('Doublon du fichier')} doublon(s) · "
            f"{preview.count('Référence inconnue')} référence(s) inconnue(s) · "
            f"{preview.count('Fournisseur inconnu')} fournisseur(s) inconnu(s)"
        )
        summary.setObjectName("titre")
        summary.setWordWrap(True)
        explanation = QLabel(
            "Seules les associations entre fiches fournisseurs existantes et produits "
            "existants seront enregistrées. Aucun stock, prix, vente ou historique de réception "
            "ne sera modifié."
        )
        explanation.setObjectName("info")
        explanation.setWordWrap(True)

        self.tableau = QTableWidget(len(preview.rows), 4)
        self.tableau.setHorizontalHeaderLabels(
            ["Ligne Excel", "Référence exacte", "Fournisseur", "Décision"]
        )
        self.tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        header = self.tableau.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for index, row in enumerate(preview.rows):
            decision = row.status if not row.issue else f"{row.status} — {row.issue}"
            for column, value in enumerate(
                (str(row.source_row), row.reference, row.supplier, decision)
            ):
                self.tableau.setItem(index, column, QTableWidgetItem(value))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            f"Associer {len(preview.linkable_rows)} fournisseur(s)"
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("principal")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(preview.linkable_rows)
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(explanation)
        layout.addWidget(self.tableau, 1)
        layout.addWidget(buttons)

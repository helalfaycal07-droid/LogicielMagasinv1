"""Aperçu de l'import Excel avant création des fiches et stocks."""

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from magasin.import_excel import StockImportPreview
from magasin.money import format_da


class ApercuImportExcelDialogue(QDialog):
    def __init__(self, parent, preview: StockImportPreview):
        super().__init__(parent)
        self.setWindowTitle("Vérifier l'import Excel")
        self.resize(1000, 620)

        summary = QLabel(
            f"{len(preview.importable_rows)} à importer · "
            f"{preview.count('Doublon du fichier')} doublon(s) exclu(s) · "
            f"{preview.count('Déjà dans le catalogue')} déjà présent(s) · "
            f"{preview.count('À vérifier')} ligne(s) à corriger"
        )
        summary.setObjectName("titre")
        summary.setWordWrap(True)
        explanation = QLabel(
            "Le stock restant devient le stock de départ. Les quantités vendues et l'historique "
            f"ne sont pas recréés. Les prix sont considérés TTC, avec la TVA par défaut "
            f"du logiciel ({preview.tax_rate_bp / 100:g} %). "
            "Les fournisseurs, dates, IMEI et notes ne sont pas importés."
        )
        explanation.setObjectName("info")
        explanation.setWordWrap(True)

        self.tableau = QTableWidget(len(preview.rows), 9)
        self.tableau.setHorizontalHeaderLabels([
            "Ligne Excel", "Référence", "Désignation", "Catégorie", "Prix achat",
            "Prix vente", "Stock de départ", "Stock minimum", "Décision",
        ])
        self.tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        header = self.tableau.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for index, row in enumerate(preview.rows):
            values = (
                str(row.source_row),
                row.reference,
                row.name,
                row.category or "—",
                format_da(row.cost_centimes) if row.cost_centimes is not None else "—",
                format_da(row.price_centimes) if row.price_centimes is not None else "—",
                str(row.stock) if row.stock is not None else "—",
                str(row.min_stock) if row.min_stock is not None else "—",
                row.status if not row.issue else f"{row.status} — {row.issue}",
            )
            for column, value in enumerate(values):
                self.tableau.setItem(index, column, QTableWidgetItem(value))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            f"Importer {len(preview.importable_rows)} produit(s)"
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("principal")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(preview.importable_rows))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(explanation)
        layout.addWidget(self.tableau, 1)
        layout.addWidget(buttons)

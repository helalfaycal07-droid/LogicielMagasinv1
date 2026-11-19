"""Consultation des devis, annulation et chargement dans la caisse."""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.clock import format_date, format_dt
from magasin.errors import MagasinError, ValidationError
from magasin.money import format_da
from magasin.quotes import cancel_quote, list_quotes

from .outils import demander_confirmation, montrer_erreur


class PageDevis(QWidget):
    """Affiche les devis conservés sans réserver le stock."""

    def __init__(self, conn, actor_user_id: int | None, charger_devis):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id
        self.charger_devis_callback = charger_devis
        self.devis: list[dict] = []

        titre = QLabel("Devis")
        titre.setObjectName("titre")
        aide = QLabel(
            "Les devis restent valables 30 jours et ne réservent pas le stock. "
            "Un devis ouvert peut être chargé dans la caisse ou annulé."
        )
        aide.setObjectName("info")
        aide.setWordWrap(True)

        self.tableau = QTableWidget(0, 7)
        self.tableau.setHorizontalHeaderLabels(
            ["N° devis", "Date", "Destinataire", "Total", "Valable jusqu'au", "État", "Vente liée"]
        )
        self.tableau.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tableau.setSelectionBehavior(QTableWidget.SelectRows)
        self.tableau.setSelectionMode(QTableWidget.SingleSelection)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        self.tableau.horizontalHeader().setStretchLastSection(True)
        self.tableau.itemSelectionChanged.connect(self._selection_changee)

        self.bouton_charger = QPushButton("Charger dans la caisse")
        self.bouton_charger.clicked.connect(self.charger)
        self.bouton_annuler = QPushButton("Annuler le devis")
        self.bouton_annuler.clicked.connect(self.annuler)
        actions = QHBoxLayout()
        actions.addWidget(self.bouton_charger)
        actions.addWidget(self.bouton_annuler)
        actions.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addWidget(aide)
        layout.addWidget(self.tableau, 1)
        layout.addLayout(actions)
        self.rafraichir()

    def rafraichir(self) -> None:
        selected_id = self._devis_selectionne()["id"] if self._devis_selectionne() else None
        self.devis = list_quotes(self.conn)
        self.tableau.setRowCount(len(self.devis))
        selected_row = -1
        for row, quote in enumerate(self.devis):
            if quote["id"] == selected_id:
                selected_row = row
            if quote["status"] == "converted":
                status = "Converti"
            elif quote["status"] == "cancelled":
                status = "Annulé"
            else:
                status = "Expiré" if quote["expired"] else "Ouvert"
            values = (
                quote["number"],
                format_dt(quote["created_at"]),
                quote["recipient_name"],
                format_da(quote["total_centimes"]),
                format_date(quote["valid_until"]),
                status,
                str(quote["converted_sale_id"] or "—"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau.setItem(row, column, item)
        if selected_row >= 0:
            self.tableau.selectRow(selected_row)
        else:
            self.tableau.clearSelection()
        self._selection_changee()

    def _devis_selectionne(self) -> dict | None:
        row = self.tableau.currentRow()
        return self.devis[row] if 0 <= row < len(self.devis) else None

    def _selection_changee(self) -> None:
        quote = self._devis_selectionne()
        eligible = bool(
            quote and quote["status"] == "open" and not quote["expired"]
        )
        self.bouton_charger.setEnabled(eligible)
        self.bouton_annuler.setEnabled(bool(quote and quote["status"] == "open"))

    def charger(self) -> None:
        quote = self._devis_selectionne()
        if quote is None or quote["status"] != "open" or quote["expired"]:
            montrer_erreur(self, ValidationError("Sélectionnez un devis ouvert et valide."))
            return
        self.charger_devis_callback(quote["id"])

    def annuler(self) -> None:
        quote = self._devis_selectionne()
        if quote is None or quote["status"] != "open":
            montrer_erreur(self, ValidationError("Sélectionnez un devis ouvert à annuler."))
            return
        if not demander_confirmation(
            self, "Annuler le devis", f"Annuler le devis {quote['number']} ?"
        ):
            return
        try:
            cancel_quote(
                self.conn, quote["id"], actor_user_id=self.actor_user_id
            )
        except (MagasinError, sqlite3.Error) as error:
            montrer_erreur(self, error)
            return
        self.rafraichir()
        QMessageBox.information(self, "Devis annulé", f"Le devis {quote['number']} est annulé.")


__all__ = ["PageDevis"]

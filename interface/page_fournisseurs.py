"""Répertoire des fournisseurs et suivi de leurs dettes."""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from magasin.errors import MagasinError
from magasin.money import format_da
from magasin.purchases import (create_supplier, list_suppliers, set_supplier_active,
                               update_supplier)

from .outils import demander_confirmation, montrer_erreur


class PageFournisseurs(QWidget):
    """Gère les coordonnées des fournisseurs sans supprimer leur historique comptable."""

    def __init__(self, conn, actor_user_id: int | None = None):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id
        self.fournisseurs: list[dict] = []
        self.fournisseur_selectionne: dict | None = None

        titre = QLabel("Fournisseurs")
        titre.setObjectName("titre")
        aide = QLabel(
            "Les réceptions, règlements et dettes restent liés à la fiche fournisseur. "
            "Une fiche archivée est conservée dans l'historique et peut être réactivée."
        )
        aide.setObjectName("info")
        aide.setWordWrap(True)

        self.tableau = QTableWidget(0, 8)
        self.tableau.setHorizontalHeaderLabels(
            ["Fournisseur", "Téléphone", "E-mail", "Réceptions",
             "Achats", "Payé", "Dette / crédit net", "État"]
        )
        self.tableau.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tableau.setSelectionBehavior(QTableWidget.SelectRows)
        self.tableau.setSelectionMode(QTableWidget.SingleSelection)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        self.tableau.horizontalHeader().setStretchLastSection(True)
        self.tableau.itemSelectionChanged.connect(self._selection_changee)

        self.nom = QLineEdit()
        self.telephone = QLineEdit()
        self.email = QLineEdit()
        self.adresse = QLineEdit()
        self.notes = QLineEdit()
        formulaire = QFormLayout()
        formulaire.addRow("Nom *", self.nom)
        formulaire.addRow("Téléphone", self.telephone)
        formulaire.addRow("E-mail", self.email)
        formulaire.addRow("Adresse", self.adresse)
        formulaire.addRow("Notes", self.notes)

        self.bouton_nouveau = QPushButton("Nouvelle fiche")
        self.bouton_nouveau.clicked.connect(self.nouvelle_fiche)
        self.bouton_enregistrer = QPushButton("Créer le fournisseur")
        self.bouton_enregistrer.setObjectName("principal")
        self.bouton_enregistrer.clicked.connect(self.enregistrer)
        self.bouton_archiver = QPushButton("Archiver")
        self.bouton_archiver.clicked.connect(self.changer_etat)
        actions = QHBoxLayout()
        actions.addWidget(self.bouton_nouveau)
        actions.addWidget(self.bouton_enregistrer)
        actions.addWidget(self.bouton_archiver)
        actions.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addWidget(aide)
        layout.addWidget(self.tableau, 1)
        layout.addLayout(formulaire)
        layout.addLayout(actions)
        self.rafraichir()

    def rafraichir(self) -> None:
        selected_id = (
            self.fournisseur_selectionne["id"] if self.fournisseur_selectionne else None
        )
        self.fournisseurs = list_suppliers(self.conn)
        self.tableau.setRowCount(len(self.fournisseurs))
        selected_row = -1
        for row, supplier in enumerate(self.fournisseurs):
            if supplier["id"] == selected_id:
                selected_row = row
            values = (
                supplier["name"],
                supplier["phone"] or "—",
                supplier["email"] or "—",
                str(supplier["receipt_count"]),
                format_da(supplier["total_centimes"]),
                format_da(supplier["paid_centimes"]),
                format_da(supplier["due_centimes"]),
                "Actif" if supplier["active"] else "Archivé",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau.setItem(row, column, item)
        if selected_row >= 0:
            self.tableau.selectRow(selected_row)
        else:
            self.tableau.clearSelection()
            self.fournisseur_selectionne = None
            self._selection_changee()

    def _selection_changee(self) -> None:
        row = self.tableau.currentRow()
        self.fournisseur_selectionne = (
            self.fournisseurs[row] if 0 <= row < len(self.fournisseurs) else None
        )
        supplier = self.fournisseur_selectionne
        if supplier is None:
            for field in (self.nom, self.telephone, self.email, self.adresse, self.notes):
                field.clear()
            self.bouton_enregistrer.setText("Créer le fournisseur")
            self.bouton_archiver.setEnabled(False)
            self.bouton_archiver.setText("Archiver")
            return
        self.nom.setText(supplier["name"])
        self.telephone.setText(supplier["phone"] or "")
        self.email.setText(supplier["email"] or "")
        self.adresse.setText(supplier["address"] or "")
        self.notes.setText(supplier["notes"] or "")
        self.bouton_enregistrer.setText("Enregistrer les modifications")
        self.bouton_archiver.setEnabled(True)
        self.bouton_archiver.setText("Archiver" if supplier["active"] else "Réactiver")

    def nouvelle_fiche(self) -> None:
        self.tableau.clearSelection()
        self.fournisseur_selectionne = None
        for field in (self.nom, self.telephone, self.email, self.adresse, self.notes):
            field.clear()
        self.bouton_enregistrer.setText("Créer le fournisseur")
        self.bouton_archiver.setEnabled(False)

    def enregistrer(self) -> None:
        values = {
            "phone": self.telephone.text(),
            "email": self.email.text(),
            "address": self.adresse.text(),
            "notes": self.notes.text(),
        }
        try:
            if self.fournisseur_selectionne is None:
                create_supplier(
                    self.conn, self.actor_user_id, self.nom.text(), **values
                )
                message = "La fiche fournisseur a été créée."
            else:
                update_supplier(
                    self.conn, self.actor_user_id, self.fournisseur_selectionne["id"],
                    self.nom.text(), **values
                )
                message = "Les modifications ont été enregistrées."
        except (MagasinError, sqlite3.Error) as error:
            montrer_erreur(self, error)
            return
        self.fournisseur_selectionne = None
        self.rafraichir()
        QMessageBox.information(self, "Fournisseur enregistré", message)

    def changer_etat(self) -> None:
        supplier = self.fournisseur_selectionne
        if supplier is None:
            return
        active = not bool(supplier["active"])
        if not active and not demander_confirmation(
            self,
            "Archiver le fournisseur",
            f"Archiver « {supplier['name']} » ?\n"
            "Ses réceptions, règlements et dettes resteront consultables.",
        ):
            return
        try:
            set_supplier_active(
                self.conn, self.actor_user_id, supplier["id"], active
            )
        except (MagasinError, sqlite3.Error) as error:
            montrer_erreur(self, error)
            return
        self.rafraichir()


__all__ = ["PageFournisseurs"]

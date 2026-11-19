"""Écran Clients et crédit : fiches, factures à crédit et versements."""

from html import escape
import sqlite3
from xmlrpc import client

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHeaderView, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.clock import format_dt
from magasin.credit import record_payment
from magasin.customers import (create_customer, customer_debt, delete_customer, get_customer,
                               normalize_phone)
from magasin.db import log_audit, transaction
from magasin.errors import MagasinError, ValidationError
from magasin.loyalty import available_points
from magasin.money import da_to_centimes, format_da
from magasin.sales import PAYMENT_METHODS

from .outils import demander_confirmation, montrer_erreur, ouvrir_dialogue

MODES_PAIEMENT = (
    ("Espèces", "especes"),
    ("Carte", "carte"),
    ("CCP", "ccp"),
    ("Virement", "virement"),
    ("Chèque", "cheque"),
    ("Avoir", "avoir"),
)

STYLE_SOMBRE = """
QWidget { background: #111827; color: #e5e7eb; }
QLineEdit, QComboBox {
    background: #1f2937; color: #f9fafb; border: 1px solid #475569;
    border-radius: 5px; padding: 7px;
}
QPushButton {
    background: #273449; color: #f8fafc; border: 1px solid #475569;
    border-radius: 5px; padding: 8px 12px;
}
QPushButton:hover { background: #35465e; }
QPushButton#principal { background: #e8590c; color: white; border: none; font-weight: bold; }
QPushButton#principal:hover { background: #c2410c; }
QPushButton:disabled { color: #8491a3; background: #202a39; }
QLabel#titre { color: #f8fafc; font-size: 18pt; font-weight: bold; }
QLabel#info { color: #cbd5e1; }
QLabel#dette { color: #86efac; font-size: 16pt; font-weight: bold; }
QTableWidget {
    background: #172131; alternate-background-color: #1d293b; color: #e5e7eb;
    gridline-color: #334155; selection-background-color: #9a3412;
    selection-color: white; border: 1px solid #334155;
}
QHeaderView::section {
    background: #263449; color: #f8fafc; padding: 7px; border: none; font-weight: bold;
}
QDialog { background: #111827; }
QDialogButtonBox QPushButton { min-width: 90px; }
"""


class ClientDialogue(QDialog):
    """Saisie simple des coordonnées d'un client."""

    def __init__(self, parent=None, client=None):
        super().__init__(parent)
        self.setWindowTitle("Modifier le client" if client else "Nouveau client")
        self.setMinimumWidth(420)
        self.valeurs = None

        self.nom = QLineEdit(client["name"] if client else "")
        self.telephone = QLineEdit(client["phone"] or "" if client else "")
        self.adresse = QLineEdit(client["address"] or "" if client else "")
        self.garant = QLineEdit(client["guarantor"] or "" if client else "")
        self.credit_autorise = QCheckBox("Autoriser le crédit")
        self.credit_autorise.setChecked(bool(client["credit_allowed"]) if client else False)
        formulaire = QFormLayout()
        formulaire.addRow("Nom *", self.nom)
        formulaire.addRow("Téléphone", self.telephone)
        formulaire.addRow("Adresse", self.adresse)
        formulaire.addRow("Nom du garant", self.garant)
        formulaire.addRow(self.credit_autorise)

        boutons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        boutons.button(QDialogButtonBox.Save).setText("Enregistrer")
        boutons.button(QDialogButtonBox.Save).setObjectName("principal")
        boutons.button(QDialogButtonBox.Cancel).setText("Annuler")
        boutons.accepted.connect(self._valider)
        boutons.rejected.connect(self.reject)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addLayout(formulaire)
        mise_en_page.addWidget(boutons)
        self.setStyleSheet(STYLE_SOMBRE)

    def _valider(self) -> None:
        nom = self.nom.text().strip()
        if not nom:
            montrer_erreur(self, ValidationError("Le client a besoin d'un nom."))
            return
        try:
            telephone = normalize_phone(self.telephone.text())
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        self.valeurs = {
            "name": nom,
            "phone": telephone,
            "address": self.adresse.text().strip() or None,
            "guarantor": self.garant.text().strip() or None,
            "credit_allowed": self.credit_autorise.isChecked(),      
        }
        self.accept()


class PageClients(QWidget):
    """Gestion des coordonnées et suivi des créances clients."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.client_id = None

        titre = QLabel("Clients et crédit")
        titre.setObjectName("titre")

        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Rechercher par nom ou téléphone...")
        self.recherche.textChanged.connect(self._afficher_clients)
        self.bouton_ajouter = QPushButton("Nouveau client")
        self.bouton_ajouter.setObjectName("principal")
        self.bouton_ajouter.clicked.connect(self.ajouter_client)
        self.bouton_modifier = QPushButton("Modifier les coordonnées")
        self.bouton_modifier.clicked.connect(self.modifier_client)
        self.bouton_supprimer = QPushButton("Supprimer le client")
        self.bouton_supprimer.setEnabled(False)
        self.bouton_supprimer.clicked.connect(self.supprimer_client)
        self.bouton_etiquette = QPushButton("Imprimer l'étiquette dossier")
        self.bouton_etiquette.setEnabled(False)
        self.bouton_etiquette.clicked.connect(self.imprimer_etiquette)

        actions = QHBoxLayout()
        actions.addWidget(self.bouton_ajouter)
        actions.addWidget(self.bouton_modifier)
        actions.addWidget(self.bouton_supprimer)
        actions.addWidget(self.bouton_etiquette)

        self.tableau_clients = self._creer_tableau(
            ["Nom", "Téléphone", "Dette actuelle", "Points fidélité"],
            [True, False, False, False],
        )
        self.tableau_clients.itemSelectionChanged.connect(self._selection_client)
        self.tableau_clients.doubleClicked.connect(self.modifier_client)

        colonne_clients = QWidget()
        layout_clients = QVBoxLayout(colonne_clients)
        layout_clients.setContentsMargins(0, 0, 0, 0)
        layout_clients.addWidget(self.recherche)
        layout_clients.addLayout(actions)
        layout_clients.addWidget(self.tableau_clients, 1)

        self.client_titre = QLabel("Sélectionnez un client")
        self.client_titre.setObjectName("titre")
        self.dette = QLabel("Dette : —")
        self.dette.setObjectName("dette")
        self.points_fidelite = QLabel("Fidélité : —")
        self.points_fidelite.setObjectName("info")
        self.coordonnees = QLabel("")
        self.coordonnees.setObjectName("info")
        self.coordonnees.setWordWrap(True)

        self.tableau_factures = self._creer_tableau(
    ["Facture", "Date", "Montant initial", "Payé à la vente", "Reste dû", "Garant"],
    [False, False, False, False, False, False],
)
        
        self.tableau_versements = self._creer_tableau(
            ["Reçu", "Date", "Mode", "Montant", "Note"],
            [False, False, False, False, True],
        )

        titres_historiques = QHBoxLayout()
        titres_historiques.addWidget(QLabel("Factures à crédit"))
        titres_historiques.addWidget(QLabel("Versements reçus"))
        tables_historiques = QHBoxLayout()
        tables_historiques.addWidget(self.tableau_factures, 1)
        tables_historiques.addWidget(self.tableau_versements, 1)

        self.montant_versement = QLineEdit()
        self.montant_versement.setPlaceholderText("Montant en DA")
        self.mode_versement = QComboBox()
        for libelle, code in MODES_PAIEMENT:
            self.mode_versement.addItem(libelle, code)
        self.note_versement = QLineEdit()
        self.note_versement.setPlaceholderText("Note facultative")
        self.bouton_versement = QPushButton("Enregistrer le versement")
        self.bouton_versement.setObjectName("principal")
        self.bouton_versement.clicked.connect(self.enregistrer_versement)

        ligne_versement = QHBoxLayout()
        ligne_versement.addWidget(QLabel("Nouveau versement"))
        ligne_versement.addWidget(self.montant_versement)
        ligne_versement.addWidget(self.mode_versement)
        ligne_versement.addWidget(self.note_versement, 1)
        ligne_versement.addWidget(self.bouton_versement)

        panneau_suivi = QWidget()
        layout_suivi = QVBoxLayout(panneau_suivi)
        layout_suivi.setContentsMargins(0, 0, 0, 0)
        layout_suivi.addWidget(self.client_titre)
        layout_suivi.addWidget(self.coordonnees)
        layout_suivi.addWidget(self.dette)
        layout_suivi.addWidget(self.points_fidelite)
        layout_suivi.addLayout(titres_historiques)
        layout_suivi.addLayout(tables_historiques, 1)
        layout_suivi.addLayout(ligne_versement)

        separateur = QSplitter(Qt.Horizontal)
        separateur.addWidget(colonne_clients)
        separateur.addWidget(panneau_suivi)
        separateur.setStretchFactor(0, 2)
        separateur.setStretchFactor(1, 5)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(separateur, 1)
        self.setStyleSheet(STYLE_SOMBRE)
        self.rafraichir()

    @staticmethod
    def _creer_tableau(entetes: list[str], colonnes_etirees: list[bool]) -> QTableWidget:
        tableau = QTableWidget(0, len(entetes))
        tableau.setHorizontalHeaderLabels(entetes)
        tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        tableau.setSelectionMode(QAbstractItemView.SingleSelection)
        tableau.setAlternatingRowColors(True)
        tableau.verticalHeader().setVisible(False)
        tableau.setSortingEnabled(False)
        for colonne, etire in enumerate(colonnes_etirees):
            tableau.horizontalHeader().setSectionResizeMode(
                colonne, QHeaderView.Stretch if etire else QHeaderView.ResizeToContents
            )
        return tableau

    def rafraichir(self) -> None:
        """Recharge les clients et conserve la sélection si elle existe encore."""
        self._afficher_clients()
        if self.client_id is not None:
            ligne = self._ligne_client(self.client_id)
            if ligne >= 0:
                self.tableau_clients.selectRow(ligne)
                self._charger_suivi(self.client_id)
                return
        self.client_id = None
        self._vider_suivi()

    def _afficher_clients(self) -> None:
        recherche = self.recherche.text().strip()
        sql = "SELECT id, name, phone FROM customers WHERE status <> 'archive'"
        parametres = []
        if recherche:
            sql += " AND (name LIKE ? COLLATE NOCASE OR phone LIKE ?)"
            parametres.extend((f"%{recherche}%", f"%{recherche}%"))
        sql += " ORDER BY name COLLATE NOCASE, id"
        clients = self.conn.execute(sql, parametres).fetchall()
        self.tableau_clients.setRowCount(len(clients))
        for ligne, client in enumerate(clients):
            valeurs = (
                client["name"],
                client["phone"] or "",
                format_da(customer_debt(self.conn, client["id"])),
                str(available_points(self.conn, client["id"])),
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                cellule.setData(Qt.UserRole, client["id"])
                if colonne in (2, 3):
                    cellule.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau_clients.setItem(ligne, colonne, cellule)
        if self.client_id is not None:
            ligne = self._ligne_client(self.client_id)
            if ligne < 0:
                self.client_id = None
                self._vider_suivi()
            else:
                self.tableau_clients.selectRow(ligne)

    def _ligne_client(self, client_id: int) -> int:
        for ligne in range(self.tableau_clients.rowCount()):
            cellule = self.tableau_clients.item(ligne, 0)
            if cellule is not None and cellule.data(Qt.UserRole) == client_id:
                return ligne
        return -1

    def _selection_client(self) -> None:
        ligne = self.tableau_clients.currentRow()
        if ligne < 0:
            return
        cellule = self.tableau_clients.item(ligne, 0)
        if cellule is None:
            return
        self.client_id = cellule.data(Qt.UserRole)
        if self.client_id is not None:
            self._charger_suivi(self.client_id)

    def _charger_suivi(self, client_id: int) -> None:
        client = get_customer(self.conn, client_id)
        if client is None:
            self.client_id = None
            self._vider_suivi()
            return

        
        self.dette.setText(f"Dette actuelle : {format_da(customer_debt(self.conn, client_id))}")
        self.points_fidelite.setText(
            f"Points fidélité disponibles : {available_points(self.conn, client_id)}"
        )
        self.client_titre.setText(client["name"])
        coordonnees = [client["phone"] or "Téléphone non renseigné"]

        if client["address"]:
             coordonnees.append(client["address"])

        if client["guarantor"]:
             coordonnees.append(f"Garant : {client['guarantor']}")
        self.coordonnees.setText(" · ".join(coordonnees))
        factures = self.conn.execute(
            """
            SELECT s.id, s.number, s.created_at, s.total_centimes,
       p.guarantor AS plan_guarantor,
                   COALESCE((SELECT SUM(ri.amount_centimes)
                             FROM sale_return_items ri
                             JOIN sale_items si ON si.id = ri.sale_item_id
                             WHERE si.sale_id = s.id), 0) AS returned_centimes,
                   COALESCE((SELECT SUM(sp.amount_centimes) FROM sale_payments sp
                             WHERE sp.sale_id = s.id), 0) AS paid_at_sale,
                   COALESCE((SELECT SUM(pa.amount_centimes) FROM payment_allocations pa
                             WHERE pa.sale_id = s.id), 0) AS paid_since
            FROM sales s
            LEFT JOIN payment_plans p ON p.sale_id = s.id
            WHERE s.customer_id = ? AND s.status IN ('validee', 'retournee')
              AND s.total_centimes - COALESCE((SELECT SUM(ri.amount_centimes)
                    FROM sale_return_items ri JOIN sale_items si ON si.id = ri.sale_item_id
                    WHERE si.sale_id = s.id), 0) > COALESCE(
                    (SELECT SUM(sp.amount_centimes) FROM sale_payments sp WHERE sp.sale_id = s.id), 0)
                    + COALESCE((SELECT SUM(pa.amount_centimes) FROM payment_allocations pa
                                WHERE pa.sale_id = s.id), 0)
            ORDER BY s.created_at DESC, s.id DESC
            """,
            (client_id,),
        ).fetchall()
        self.tableau_factures.setRowCount(len(factures))
        for ligne, facture in enumerate(factures):
            reste = max(
                0, facture["total_centimes"] - facture["returned_centimes"]
                - facture["paid_at_sale"] - facture["paid_since"]
            )
            valeurs = (
                facture["number"],
                format_dt(facture["created_at"]),
                format_da(facture["total_centimes"]),
                format_da(facture["paid_at_sale"]),
                format_da(reste),
                facture["plan_guarantor"] or "",
)
            
            for colonne, valeur in enumerate(valeurs):
                self.tableau_factures.setItem(ligne, colonne, QTableWidgetItem(valeur))

        versements = self.conn.execute(
            "SELECT number, created_at, method, amount_centimes, note "
            "FROM customer_payments WHERE customer_id = ? ORDER BY created_at DESC, id DESC",
            (client_id,),
        ).fetchall()
        libelles = dict(MODES_PAIEMENT)
        self.tableau_versements.setRowCount(len(versements))
        for ligne, versement in enumerate(versements):
            valeurs = (
                versement["number"],
                format_dt(versement["created_at"]),
                libelles.get(versement["method"], versement["method"]),
                format_da(versement["amount_centimes"]),
                versement["note"] or "",
            )
            for colonne, valeur in enumerate(valeurs):
                self.tableau_versements.setItem(ligne, colonne, QTableWidgetItem(valeur))

        self.bouton_modifier.setEnabled(True)
        self.bouton_supprimer.setEnabled(True)
        self.bouton_etiquette.setEnabled(True)
        self.bouton_versement.setEnabled(customer_debt(self.conn, client_id) > 0)
        self.montant_versement.setEnabled(customer_debt(self.conn, client_id) > 0)
        self.mode_versement.setEnabled(customer_debt(self.conn, client_id) > 0)
        self.note_versement.setEnabled(customer_debt(self.conn, client_id) > 0)

    def _vider_suivi(self) -> None:
        self.client_titre.setText("Sélectionnez un client")
        self.coordonnees.clear()
        self.dette.setText("Dette : —")
        self.points_fidelite.setText("Fidélité : —")
        self.tableau_factures.setRowCount(0)
        self.tableau_versements.setRowCount(0)
        self.bouton_modifier.setEnabled(False)
        self.bouton_supprimer.setEnabled(False)
        self.bouton_etiquette.setEnabled(False)
        self.bouton_versement.setEnabled(False)
        self.montant_versement.setEnabled(False)
        self.mode_versement.setEnabled(False)
        self.note_versement.setEnabled(False)

    @staticmethod
    def _construire_etiquette_html(client) -> str:
        telephone = escape(str(client["phone"] or "—"))
        nom = escape(str(client["name"]))
        dossier = escape(str(client["dossier_number"]))
        return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><style>
@page {{ size: 80mm 50mm; margin: 0; }}
html, body {{ width: 80mm; height: 50mm; margin: 0; padding: 0; }}
body {{ font-family: Arial, sans-serif; color: #17212b; }}
.etiquette {{ box-sizing: border-box; width: 80mm; height: 50mm; padding: 4mm;
              display: flex; flex-direction: column; justify-content: center; }}
.nom {{ font-size: 15pt; font-weight: bold; overflow-wrap: anywhere; }}
.dossier {{ margin-top: 3mm; font-size: 18pt; font-weight: bold; color: #087f95; }}
.telephone {{ margin-top: 2mm; font-size: 11pt; }}
</style></head><body><main class="etiquette">
<div class="nom">{nom}</div><div class="dossier">{dossier}</div>
<div class="telephone">Tél. : {telephone}</div>
</main></body></html>"""

    def imprimer_etiquette(self) -> None:
        if self.client_id is None:
            return
        client = get_customer(self.conn, self.client_id)
        if client is None:
            montrer_erreur(self, ValidationError("Client introuvable."))
            self.rafraichir()
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        page_size = QPageSize(
            QSizeF(80, 50),
            QPageSize.Unit.Millimeter,
            "Étiquette 80 x 50 mm",
            QPageSize.SizeMatchPolicy.ExactMatch,
        )
        layout = QPageLayout(
            page_size,
            QPageLayout.Orientation.Landscape,
            QMarginsF(2, 2, 2, 2),
            QPageLayout.Unit.Millimeter,
        )
        if not printer.setPageLayout(layout):
            QMessageBox.warning(
                self,
                "Impression impossible",
                "L'imprimante sélectionnée ne prend pas en charge le format 80 × 50 mm.",
            )
            return
        dialogue = QPrintDialog(printer, self)
        dialogue.setWindowTitle("Imprimer l'étiquette dossier")
        if dialogue.exec() != QDialog.DialogCode.Accepted:
            return
        document = QTextDocument(self)
        document.setHtml(self._construire_etiquette_html(client))
        document.setPageSize(
            printer.pageLayout().paintRect(QPageLayout.Unit.Point).size()
        )
        document.print_(printer)

    def ajouter_client(self) -> None:
        dialogue = ClientDialogue(self)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        try:
            client_id = create_customer(
                self.conn,
                dialogue.valeurs["name"],
                dialogue.valeurs["phone"],
                address=dialogue.valeurs["address"],
                guarantor=dialogue.valeurs["guarantor"],
                credit_allowed=dialogue.valeurs["credit_allowed"],  
            )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        ligne = self._ligne_client(client_id)
        if ligne >= 0:
            self.tableau_clients.selectRow(ligne)

    def modifier_client(self) -> None:
        if self.client_id is None:
            return
        client = get_customer(self.conn, self.client_id)
        if client is None:
            self.rafraichir()
            return
        dialogue = ClientDialogue(self, client)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        try:
            with transaction(self.conn):
                self.conn.execute(
                    "UPDATE customers SET name = ?, phone = ?, address = ?, guarantor = ?, "
                "credit_allowed = ? WHERE id = ?",
                (
                    dialogue.valeurs["name"],
                    dialogue.valeurs["phone"],
                    dialogue.valeurs["address"],
                    dialogue.valeurs["guarantor"],
                    int(dialogue.valeurs["credit_allowed"]),
                    self.client_id,
                ), 
            ) 
                log_audit(
                    self.conn, "client_modifie", "customers", self.client_id,
                    "Coordonnées et autorisation de crédit modifiées",
                )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

    def supprimer_client(self) -> None:
        if self.client_id is None:
            return
        client = get_customer(self.conn, self.client_id)
        if client is None:
            self.rafraichir()
            return
        if not demander_confirmation(
            self,
            "Supprimer le client",
            f"Supprimer la fiche de « {client['name']} » ?\n\n"
            "Si le client possède un historique (ventes, paiements ou crédit), "
            "sa fiche sera archivée afin de préserver les données comptables.",
        ):
            return
        try:
            archive = delete_customer(self.conn, self.client_id)
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return

        self.client_id = None
        self.rafraichir()
        action = "archivée pour préserver son historique" if archive else "supprimée"
        QMessageBox.information(
            self, "Client supprimé", f"La fiche de « {client['name']} » a été {action}."
        )

    def enregistrer_versement(self) -> None:
        if self.client_id is None:
            return
        try:
            montant = da_to_centimes(self.montant_versement.text())
            if montant <= 0:
                raise ValidationError("Le versement doit être supérieur à 0.")
            mode = self.mode_versement.currentData()
            if mode not in PAYMENT_METHODS:
                raise ValidationError("Mode de paiement inconnu.")
            record_payment(
                self.conn,
                self.client_id,
                montant,
                method=mode,
                note=self.note_versement.text().strip() or None,
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        self.montant_versement.clear()
        self.note_versement.clear()
        self.rafraichir()


__all__ = ["PageClients"]

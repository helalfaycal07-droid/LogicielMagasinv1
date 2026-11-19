"""Consultation des rapports essentiels du magasin."""

import csv

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QComboBox, QDateEdit, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.clock import format_dt, today
from magasin.errors import MagasinError
from magasin.money import format_da
from magasin.reports import profit_report_data, report_data

from .outils import montrer_erreur


MODES_PAIEMENT = {
    "especes": "Espèces",
    "carte": "Carte",
    "ccp": "CCP",
    "virement": "Virement",
    "cheque": "Chèque",
    "avoir": "Avoir",
}


class PageRapports(QWidget):
    """Rapports filtrés par date et export CSV de l'onglet consulté."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn

        titre = QLabel("Rapports")
        titre.setObjectName("titre")

        date_fin = today()
        date_debut = date_fin.replace(day=1)
        self.date_debut = QDateEdit(QDate(date_debut.year, date_debut.month, date_debut.day))
        self.date_fin = QDateEdit(QDate(date_fin.year, date_fin.month, date_fin.day))
        for champ in (self.date_debut, self.date_fin):
            champ.setCalendarPopup(True)
            champ.setDisplayFormat("dd/MM/yyyy")
        self.regroupement_benefice = QComboBox()
        self.regroupement_benefice.addItem("Par mois", "month")
        self.regroupement_benefice.addItem("Par année", "year")
        self.regroupement_benefice.currentIndexChanged.connect(
            self._changer_regroupement
        )

        self.bouton_actualiser = QPushButton("Actualiser")
        self.bouton_actualiser.setObjectName("principal")
        self.bouton_actualiser.clicked.connect(self.rafraichir)
        self.bouton_exporter = QPushButton("Exporter l'onglet en CSV")
        self.bouton_exporter.clicked.connect(self.exporter_csv)

        filtres = QFormLayout()
        filtres.addRow("Du", self.date_debut)
        filtres.addRow("Au", self.date_fin)
        filtres.addRow("Bénéfice", self.regroupement_benefice)
        actions = QHBoxLayout()
        actions.addLayout(filtres)
        actions.addWidget(self.bouton_actualiser)
        actions.addWidget(self.bouton_exporter)
        actions.addStretch()

        self.resume = QLabel("")
        self.resume.setObjectName("info")
        self.resume.setWordWrap(True)

        self.onglets = QTabWidget()
        self.tableau_produits = self._creer_tableau(
            ["Référence", "Produit", "Quantité vendue", "Chiffre d'affaires"]
        )
        self.tableau_paiements = self._creer_tableau(["Mode de paiement", "Montant net encaissé"])
        self.tableau_retours = self._creer_tableau(
            ["N° retour", "Date", "N° vente", "Référence", "Produit", "Quantité",
             "Montant retourné", "Mode de remboursement", "Motif"]
        )
        self.tableau_stock = self._creer_tableau(
            ["Référence", "Produit", "Stock actuel", "Coût unitaire", "Valeur au coût"]
        )
        self.tableau_clotures = self._creer_tableau(
            ["Date", "Caisse", "Solde attendu", "Montant compté", "Écart", "Raison"]
        )
        self.tableau_benefice = self._creer_tableau(
            ["Période", "Ventes", "Retours", "Chiffre d'affaires net",
             "Coût net des articles", "Bénéfice brut", "Articles au coût inconnu"]
        )
        for title, table in (
            ("Ventes par produit", self.tableau_produits),
            ("Paiements", self.tableau_paiements),
            ("Retours", self.tableau_retours),
            ("Stock actuel", self.tableau_stock),
            ("Clôtures de caisse", self.tableau_clotures),
            ("Bénéfice", self.tableau_benefice),
        ):
            self.onglets.addTab(table, title)

        layout = QVBoxLayout(self)
        layout.addWidget(titre)
        layout.addLayout(actions)
        layout.addWidget(self.resume)
        layout.addWidget(self.onglets, 1)
        self.rafraichir()

    @staticmethod
    def _creer_tableau(entetes: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(entetes))
        table.setHorizontalHeaderLabels(entetes)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _remplir(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index > 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row_index, column_index, item)

    def rafraichir(self) -> None:
        debut = self.date_debut.date().toString(Qt.ISODate)
        fin = self.date_fin.date().toString(Qt.ISODate)
        try:
            data = report_data(self.conn, debut, fin)
            profit = profit_report_data(
                self.conn, debut, fin, self.regroupement_benefice.currentData()
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        self.resume.setText(
            f"Période {self.date_debut.text()} – {self.date_fin.text()} : "
            f"{data['sales_count']} vente(s), "
            f"{format_da(data['sales_total_centimes'])} de chiffre d'affaires brut.  "
            f"{data['returns_count']} retour(s), "
            f"{format_da(data['returns_total_centimes'])} retournés, "
            f"chiffre d'affaires net : {format_da(data['net_sales_total_centimes'])}.  "
            f"Stock actuel : {format_da(data['inventory_value_centimes'])} au coût, "
            f"{data['low_stock_count']} produit(s) en stock bas, "
            f"{data['negative_stock_count']} en stock négatif."
        )
        if profit["profit_complete"]:
            benefit_summary = (
                f"Bénéfice brut : {format_da(profit['profit_centimes'])} "
                "(avant charges et impôts)."
            )
        else:
            benefit_summary = (
                f"Bénéfice brut incomplet : coût d'achat inconnu pour "
                f"{profit['unknown_cost_quantity']} article(s) de ventes historiques."
            )
        self.resume.setText(f"{self.resume.text()}  {benefit_summary}")
        self._remplir(self.tableau_produits, [
            [row["reference"], row["name"], str(row["quantity"]),
             format_da(row["total_centimes"])]
            for row in data["products_sold"]
        ])
        self._remplir(self.tableau_paiements, [
            [MODES_PAIEMENT.get(row["method"], row["method"]),
             format_da(row["total_centimes"])]
            for row in data["payments"]
        ])
        self._remplir(self.tableau_retours, [
            [row["number"], format_dt(row["created_at"]), row["sale_number"],
             row["reference"], row["product_name"], str(row["quantity"]),
             format_da(row["amount_centimes"]),
             MODES_PAIEMENT.get(row["refund_method"], row["refund_method"]), row["reason"]]
            for row in data["returns"]
        ])
        self._remplir(self.tableau_stock, [
            [row["reference"], row["name"], str(row["stock"]),
             format_da(row["cost_centimes"]), format_da(row["valeur_cout_centimes"])]
            for row in data["inventory"]
        ])
        self._remplir(self.tableau_clotures, [
            [format_dt(row["closed_at"]), row["register_name"],
             format_da(row["expected_centimes"]), format_da(row["counted_centimes"]),
             format_da(row["difference_centimes"]), row["difference_reason"] or "—"]
            for row in data["cash_closures"]
        ])
        self._remplir(self.tableau_benefice, [
            [
                row["period"],
                format_da(row["revenue_centimes"]),
                format_da(row["returns_centimes"]),
                format_da(row["net_revenue_centimes"]),
                format_da(row["cost_centimes"]),
                format_da(row["profit_centimes"])
                if row["profit_centimes"] is not None else "Coût manquant",
                str(row["unknown_cost_quantity"]),
            ]
            for row in profit["periods"]
        ])

    def _changer_regroupement(self, _index: int) -> None:
        fin = self.date_fin.date()
        if self.regroupement_benefice.currentData() == "year":
            self.date_debut.setDate(QDate(fin.year(), 1, 1))
            self.date_fin.setDate(QDate(fin.year(), 12, 31))
        else:
            self.date_debut.setDate(QDate(fin.year(), fin.month(), 1))
            self.date_fin.setDate(QDate(fin.year(), fin.month(), fin.daysInMonth()))
        self.rafraichir()

    def exporter_csv(self) -> None:
        table = self.onglets.currentWidget()
        titre = self.onglets.tabText(self.onglets.currentIndex())
        chemin, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport", f"{titre.lower().replace(' ', '_')}.csv",
            "Fichier CSV (*.csv)",
        )
        if not chemin:
            return
        if not chemin.lower().endswith(".csv"):
            chemin += ".csv"
        try:
            with open(chemin, "w", newline="", encoding="utf-8-sig") as fichier:
                writer = csv.writer(fichier, delimiter=";")
                writer.writerow(["Rapport", titre])
                writer.writerow(["Période", self.date_debut.text(), "au", self.date_fin.text()])
                writer.writerow([
                    table.horizontalHeaderItem(column).text()
                    for column in range(table.columnCount())
                ])
                for row in range(table.rowCount()):
                    writer.writerow([
                        table.item(row, column).text() if table.item(row, column) else ""
                        for column in range(table.columnCount())
                    ])
        except OSError as erreur:
            QMessageBox.critical(self, "Export impossible", str(erreur))
            return
        QMessageBox.information(self, "Export terminé", f"Rapport exporté :\n{chemin}")

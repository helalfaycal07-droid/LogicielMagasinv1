"""Écran de vente rapide : recherche, panier, paiements et validation d'une vente."""

from html import escape
import sqlite3

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QInputDialog)

from magasin.clock import format_date, format_dt, now_iso, today
from magasin.errors import MagasinError, ValidationError
from magasin.loyalty import (
    CENTIMES_PER_POINT_REDEEMED,
    available_points,
    points_earned_for_sale,
)
from magasin.money import centimes_to_text, da_to_centimes, format_da, split_ttc
from magasin.products import find_product
from magasin.quotes import QuoteLine, create_quote, get_quote
from magasin.sequences import next_number
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import clean_imei, validate_imei
from magasin.users import can, has_users

from .outils import demander_confirmation, montrer_erreur, ouvrir_dialogue

MODES_PAIEMENT = (
    ("Espèces", "especes"),
    ("Carte", "carte"),
    ("CCP", "ccp"),
    ("Virement", "virement"),
    ("Chèque", "cheque"),
    ("Avoir", "avoir"),
)


class ProformaDestinataireDialogue(QDialog):
    """Saisie libre du destinataire d'une proforma, sans créer de fiche client."""

    def __init__(self, parent=None, *, nom: str = "", telephone: str = "", adresse: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Destinataire de la facture proforma")
        self.setMinimumWidth(420)
        self.valeurs: dict[str, str] | None = None

        self.nom = QLineEdit(nom)
        self.nom.setPlaceholderText("Nom du client ou de l'entreprise")
        self.telephone = QLineEdit(telephone)
        self.telephone.setPlaceholderText("Téléphone (facultatif)")
        self.adresse = QLineEdit(adresse)
        self.adresse.setPlaceholderText("Adresse (facultative)")

        formulaire = QFormLayout()
        formulaire.addRow("Client / destinataire *", self.nom)
        formulaire.addRow("Téléphone", self.telephone)
        formulaire.addRow("Adresse", self.adresse)

        boutons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        boutons.button(QDialogButtonBox.Save).setText("Continuer vers l'impression")
        boutons.button(QDialogButtonBox.Cancel).setText("Annuler")
        boutons.accepted.connect(self._valider)
        boutons.rejected.connect(self.reject)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(QLabel("Ces coordonnées seront utilisées uniquement sur cette proforma."))
        mise_en_page.addLayout(formulaire)
        mise_en_page.addWidget(boutons)

    def _valider(self) -> None:
        nom = self.nom.text().strip()
        if not nom:
            QMessageBox.warning(self, "Destinataire manquant", "Saisissez le nom du client ou destinataire.")
            return
        self.valeurs = {
            "name": nom,
            "phone": self.telephone.text().strip(),
            "address": self.adresse.text().strip(),
        }
        self.accept()


class PageVenteRapide(QWidget):
    """Interface de vente au comptoir, sans dupliquer les règles métier de magasin.sales."""

    def __init__(self, conn, actor_user_id: int | None = None):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id
        self.panier: list[dict] = []
        self.paiements: list[PaymentIn] = []
        self._dernier_ticket: tuple[str, int] | None = None
        self._derniere_vente_id: int | None = None
        self._quote_id: int | None = None
        self._quote_number: str | None = None
        self._quote_valid_until: str | None = None
        self._quote_created_at: str | None = None
        self._quote_recipient: dict[str, str] | None = None
        self._quote_tax_rates: dict[int, int] = {}

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
        self.bouton_enregistrer_devis = QPushButton("Enregistrer un devis")
        self.bouton_enregistrer_devis.clicked.connect(self.enregistrer_devis)
        actions_panier = QHBoxLayout()
        actions_panier.addWidget(self.bouton_retirer)
        actions_panier.addWidget(self.bouton_vider)
        actions_panier.addWidget(self.bouton_enregistrer_devis)
        actions_panier.addStretch()

        self.client = QComboBox()
        self._charger_clients()
        self.client.currentIndexChanged.connect(self._changer_client)
        self.remise = QLineEdit("0")
        self.remise.setPlaceholderText("0,00")
        self.remise.setMaximumWidth(150)
        if has_users(self.conn):
            user = self.conn.execute(
                "SELECT role FROM users WHERE id = ? AND active = 1", (self.actor_user_id,)
            ).fetchone()
            self.remise.setEnabled(can(dict(user) if user else None, "approve_discount"))
        self._remise_autorisee = self.remise.isEnabled()
        self.remise.textChanged.connect(lambda: self._mettre_a_jour_resume())
        self.points_fidelite = QSpinBox()
        self.points_fidelite.setRange(0, 1_000_000)
        self.points_fidelite.setMaximumWidth(130)
        self.points_fidelite.valueChanged.connect(lambda: self._mettre_a_jour_resume())
        self.solde_points = QLabel("0 point")

        formulaire = QFormLayout()
        formulaire.addRow("Client (facultatif)", self.client)
        formulaire.addRow("Remise totale (DA)", self.remise)
        formulaire.addRow("Points à utiliser (1 point = 1 DA)", self.points_fidelite)
        formulaire.addRow("Solde fidélité disponible", self.solde_points)

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
        self.btn_imprimer = QPushButton("Imprimer le ticket")
        self.btn_imprimer.setEnabled(False)
        self.btn_imprimer.clicked.connect(self.imprimer_ticket)
        self.bouton_imprimer = self.btn_imprimer
        self.btn_imprimer_facture = QPushButton("Imprimer Facture A4")
        self.btn_imprimer_facture.setEnabled(False)
        self.btn_imprimer_facture.clicked.connect(self.imprimer_facture_a4)
        self.btn_imprimer_proforma = QPushButton("Imprimer Facture proforma")
        self.btn_imprimer_proforma.setEnabled(False)
        self.btn_imprimer_proforma.clicked.connect(self.imprimer_proforma)
        self.btn_bon_livraison = QPushButton("Imprimer Bon de livraison")
        self.btn_bon_livraison.setEnabled(False)
        self.btn_bon_livraison.clicked.connect(self.imprimer_bon_livraison)

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
        mise_en_page.addWidget(self.btn_imprimer_proforma, alignment=Qt.AlignRight)
        mise_en_page.addWidget(self.btn_imprimer, alignment=Qt.AlignRight)
        mise_en_page.addWidget(self.btn_imprimer_facture, alignment=Qt.AlignRight)
        mise_en_page.addWidget(self.btn_bon_livraison, alignment=Qt.AlignRight)

        self._rafraichir_panier()
        self._rafraichir_paiements()
        self._mettre_a_jour_resume()

    def _charger_clients(self) -> None:
        selected_customer_id = self.client.currentData()
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
        if selected_customer_id is not None:
            index = self.client.findData(selected_customer_id)
            if index >= 0:
                self.client.setCurrentIndex(index)

    def _changer_client(self) -> None:
        self.points_fidelite.setValue(0)
        self._mettre_a_jour_resume()

    def rafraichir(self) -> None:
        """À appeler quand la page est rouverte, notamment après une vente."""
        self._charger_clients()
        self._mettre_a_jour_resume()

    def ajouter_produit(self) -> None:
        if self._quote_id is not None:
            montrer_erreur(
                self, ValidationError("Les lignes d'un devis chargé ne peuvent pas être modifiées.")
            )
            return
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
        if self._quote_id is not None:
            return
        ligne = self.tableau_panier.currentRow()
        if 0 <= ligne < len(self.panier):
            del self.panier[ligne]
            self._rafraichir_panier()

    def vider_panier(self) -> None:
        if self._quote_id is not None:
            return
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
            total_apres_remise = total_brut - remise
            if total_apres_remise < 0:
                raise ValidationError("La remise dépasse le total du panier.")
            customer_id = self.client.currentData()
            points_disponibles = (
                available_points(self.conn, customer_id)
                if customer_id is not None else 0
            )
            points_max = min(
                points_disponibles,
                total_apres_remise // CENTIMES_PER_POINT_REDEEMED,
            )
            self.points_fidelite.setMaximum(points_max)
            self.solde_points.setText(
                f"{points_disponibles} point(s)"
            )
            points_utilises = self.points_fidelite.value()
            reduction_fidelite = points_utilises * CENTIMES_PER_POINT_REDEEMED
            total = total_apres_remise - reduction_fidelite
            if total < 0:
                raise ValidationError("Les remises et les points dépassent le total.")
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
        if remise:
            morceaux.append(f"Remise : {format_da(remise)}")
        if points_utilises:
            morceaux.append(
                f"Fidélité : {points_utilises} point(s) "
                f"(-{format_da(reduction_fidelite)})"
            )
        points_gagnes = (
            points_earned_for_sale(total) if customer_id is not None else 0
        )
        if points_gagnes:
            morceaux.append(f"Points gagnés : {points_gagnes}")
        if change > cash_total:
            morceaux.append("Le rendu ne peut être supérieur au montant reçu en espèces.")
        if due > 0 and self.client.currentData() is None:
            morceaux.append("Un client identifié et autorisé est nécessaire pour vendre à crédit.")
        self.resume.setText("  ·  ".join(morceaux))
        self.bouton_valider.setEnabled(bool(self.panier))

    def _mettre_a_jour_boutons(self) -> None:
        quote_loaded = self._quote_id is not None
        self.bouton_retirer.setEnabled(
            not quote_loaded and self.tableau_panier.currentRow() >= 0
        )
        self.bouton_vider.setEnabled(not quote_loaded and bool(self.panier))
        self.bouton_ajouter.setEnabled(not quote_loaded)
        self.recherche.setEnabled(not quote_loaded)
        self.quantite.setEnabled(not quote_loaded)
        self.client.setEnabled(not quote_loaded)
        self.remise.setEnabled(not quote_loaded and self._remise_autorisee)
        self.points_fidelite.setEnabled(not quote_loaded)
        self.bouton_enregistrer_devis.setEnabled(bool(self.panier) and not quote_loaded)
        self.bouton_retirer_paiement.setEnabled(self.tableau_paiements.currentRow() >= 0)
        self.btn_imprimer_proforma.setEnabled(bool(self.panier))
        self.btn_imprimer_proforma.setText(
            "Imprimer le devis" if quote_loaded else "Imprimer Facture proforma"
        )

    def _construire_ticket_html(self, resultat, lignes, remise: int, created_at: str) -> tuple[str, int]:
        lignes_html = []
        hauteur_lignes = 0
        for ligne in lignes:
            nom = escape(str(ligne["name"]))
            quantite = ligne["quantity"]
            prix_unitaire = ligne["unit_price_centimes"]
            total_ligne = quantite * prix_unitaire
            lignes_html.append(
                f"<tr><td class='nom'>{nom}</td><td>{quantite}</td>"
                f"<td>{format_da(prix_unitaire)}</td><td>{format_da(total_ligne)}</td></tr>"
            )
            hauteur_lignes += max(1, (len(str(ligne["name"])) + 21) // 22)

        ajustement_html = ""
        if remise:
            ajustement_html += (
                f"<tr><td>Remise</td><td class='montant'>-{format_da(remise)}</td></tr>"
            )
        if resultat.loyalty_points_redeemed:
            reduction = (
                resultat.loyalty_points_redeemed * CENTIMES_PER_POINT_REDEEMED
            )
            ajustement_html += (
                f"<tr><td>Fidélité ({resultat.loyalty_points_redeemed} points)</td>"
                f"<td class='montant'>-{format_da(reduction)}</td></tr>"
            )
        if resultat.change_centimes:
            ajustement_html += (
                f"<tr><td>Monnaie rendue</td>"
                f"<td class='montant'>{format_da(resultat.change_centimes)}</td></tr>"
            )
        elif resultat.due_centimes:
            ajustement_html += (
                f"<tr><td>Crédit</td><td class='montant'>{format_da(resultat.due_centimes)}</td></tr>"
            )

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8">
<style>
@page {{ margin: 0; }}
html, body {{ width: 100%; margin: 0; padding: 0; color: #111; background: #fff; }}
body {{ font-family: Arial, sans-serif; font-size: 9pt; }}
.ticket {{
    box-sizing: border-box;
    width: 100%;
    max-width: 80mm;
    margin: 0 auto;
    padding: 5mm;
    background: #fff;
}}
h1 {{ margin: 0 0 2mm; font-size: 14pt; line-height: 1.25; text-align: center; }}
.numero, .date {{ margin: 1.5mm 0; text-align: center; }}
.separateur {{ margin: 3mm 0; border-top: 1px dashed #555; }}
table {{ width: 100%; margin-top: 2mm; border-collapse: collapse; table-layout: fixed; }}
th, td {{
    padding: 1.5mm 0.8mm;
    border-bottom: 1px dashed #777;
    text-align: right;
    vertical-align: top;
    overflow-wrap: anywhere;
}}
th {{ font-size: 8pt; font-weight: bold; }}
th:first-child, td.nom {{ text-align: left; }}
.totaux td {{ padding: 1mm 0.8mm; border: 0; }}
.montant {{ text-align: right; white-space: normal; }}
.total td {{ padding-top: 2mm; border-top: 1px dashed #111; font-size: 10pt; font-weight: bold; }}
.pied {{ margin: 4mm 0 0; text-align: center; font-size: 8pt; }}
</style></head>
<body>
<div class="ticket">
<h1 dir="rtl">تجهيز دارك</h1>
<p class="numero">Tadjhiz Darek — Médéa</p>
<p class="numero">Ticket : {escape(str(resultat.number))}</p>
<p class="date">{escape(format_dt(created_at))}</p>
<div class="separateur"></div>
<table>
<colgroup>
<col style="width: 46%">
<col style="width: 12%">
<col style="width: 21%">
<col style="width: 21%">
</colgroup>
<thead><tr><th>Article</th><th>Qté</th><th>Prix unit.</th><th>Total</th></tr></thead>
<tbody>{''.join(lignes_html)}</tbody>
</table>
<table class="totaux">
<tbody>
<tr class="total"><td>Total</td><td class="montant">{format_da(resultat.total_centimes)}</td></tr>
{"<tr><td>Points gagnés</td><td class='montant'>" + str(resultat.loyalty_points_earned) + "</td></tr>" if resultat.loyalty_points_earned else ""}
<tr><td>Montant reçu</td><td class="montant">{format_da(resultat.tendered_centimes)}</td></tr>
{ajustement_html}
</tbody></table>
<div class="separateur"></div>
<p class="pied">Merci pour votre visite</p>
</div>
</body></html>"""
        hauteur_mm = max(
            80, 55 + hauteur_lignes * 8 + len(lignes) * 4
            + bool(remise) * 6 + bool(resultat.loyalty_points_redeemed) * 7
            + bool(resultat.loyalty_points_earned) * 5,
        )
        return html, hauteur_mm

    def imprimer_ticket(self) -> None:
        if self._dernier_ticket is None:
            return

        contenu, hauteur_mm = self._dernier_ticket
        imprimante = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialogue = QPrintDialog(imprimante, self)
        if dialogue.exec() != QDialog.DialogCode.Accepted:
            return

        info_imprimante = QPrinterInfo.printerInfo(imprimante.printerName())
        formats = info_imprimante.supportedPageSizes() if not info_imprimante.isNull() else []
        largeurs_rouleau = []
        for format_papier in formats:
            dimensions = format_papier.size(QPageSize.Unit.Millimeter)
            largeur = min(dimensions.width(), dimensions.height())
            longueur = max(dimensions.width(), dimensions.height())
            if 50 <= largeur <= 85 and longueur >= 150 and longueur / largeur >= 2.5:
                largeurs_rouleau.append(largeur)
        largeur_rouleau = (
            min(largeurs_rouleau, key=lambda largeur: (min(abs(largeur - 58), abs(largeur - 80)),
                                                       abs(largeur - 80)))
            if largeurs_rouleau else None
        )

        if largeur_rouleau is not None:
            format_page = QPageSize(
                QSizeF(largeur_rouleau, hauteur_mm),
                QPageSize.Unit.Millimeter,
                f"Ticket thermique {largeur_rouleau:g} mm",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
            marges = QMarginsF(2, 2, 2, 2)
        else:
            format_page = imprimante.pageLayout().pageSize()
            marges = QMarginsF(10, 10, 10, 10)

        mise_en_page = QPageLayout(
            format_page,
            QPageLayout.Orientation.Portrait if largeur_rouleau is not None
            else imprimante.pageLayout().orientation(),
            marges,
            QPageLayout.Unit.Millimeter,
        )
        if not imprimante.setPageLayout(mise_en_page):
            QMessageBox.warning(
                self,
                "Impression impossible",
                "Le périphérique sélectionné ne prend pas en charge la mise en page du ticket.",
            )
            return

        document = QTextDocument(self)
        document.setHtml(contenu)
        document.setPageSize(imprimante.pageLayout().paintRect(QPageLayout.Unit.Point).size())
        document.print_(imprimante)

    def _construire_facture_a4_html(self, vente_id: int) -> str:
        vente = self.conn.execute(
            "SELECT s.*, c.code AS client_code, c.dossier_number AS client_dossier, "
            "c.name AS client_name, c.phone AS client_phone, c.address AS client_address "
            "FROM sales s "
            "LEFT JOIN customers c ON c.id = s.customer_id WHERE s.id = ?",
            (vente_id,),
        ).fetchone()
        if vente is None:
            raise ValidationError("La vente à facturer est introuvable.")

        lignes = self.conn.execute(
            "SELECT si.quantity, si.unit_price_centimes, si.total_centimes, "
            "p.reference, p.name, p.tax_rate_bp "
            "FROM sale_items si JOIN products p ON p.id = si.product_id "
            "WHERE si.sale_id = ? ORDER BY si.id",
            (vente_id,),
        ).fetchall()
        if not lignes:
            raise ValidationError("La vente ne contient aucun article.")

        total_brut = sum(ligne["total_centimes"] for ligne in lignes)
        montant_facture = vente["total_centimes"]
        montants_lignes = []
        restants = []
        if total_brut:
            for index, ligne in enumerate(lignes):
                montant, reste = divmod(
                    montant_facture * ligne["total_centimes"], total_brut
                )
                montants_lignes.append(montant)
                restants.append((reste, index))
            centimes_restants = montant_facture - sum(montants_lignes)
            for _reste, index in sorted(restants, reverse=True)[:centimes_restants]:
                montants_lignes[index] += 1
        else:
            montants_lignes = [0] * len(lignes)

        lignes_html = []
        total_ht = 0
        total_tva = 0
        for ligne, montant_ttc in zip(lignes, montants_lignes):
            taux_tva = ligne["tax_rate_bp"]
            quantite = ligne["quantity"]
            prix_unitaire_ttc = (montant_ttc + quantite // 2) // quantite
            prix_unitaire_ht, _tva_unitaire = split_ttc(
                prix_unitaire_ttc, taux_tva
            )
            montant_ht, montant_tva = split_ttc(montant_ttc, taux_tva)
            total_ht += montant_ht
            total_tva += montant_tva
            lignes_html.append(
                "<tr>"
                f"<td class='designation'>{escape(str(ligne['name']))}"
                f"<br><span class='reference'>{escape(str(ligne['reference']))}</span></td>"
                f"<td class='centre'>{quantite}</td>"
                "<td class='centre'>pcs</td>"
                f"<td>{format_da(prix_unitaire_ht)}</td>"
                f"<td class='centre'>{taux_tva / 100:g} %</td>"
                f"<td>{format_da(montant_tva)}</td>"
                f"<td class='important'>{format_da(montant_ttc)}</td>"
                "</tr>"
            )

        reduction_fidelite = (
            vente["loyalty_points_redeemed"] * CENTIMES_PER_POINT_REDEEMED
        )
        remise = total_brut - vente["total_centimes"] - reduction_fidelite
        paiements = self.conn.execute(
            "SELECT method, amount_centimes FROM sale_payments WHERE sale_id = ? ORDER BY rowid",
            (vente_id,),
        ).fetchall()
        libelles_paiement = dict((code, libelle) for libelle, code in MODES_PAIEMENT)
        modes_paiement = ", ".join(
            libelles_paiement.get(paiement["method"], paiement["method"])
            for paiement in paiements
        ) or "À crédit"
        montant_paye = sum(paiement["amount_centimes"] for paiement in paiements)
        reste_du = max(0, vente["total_centimes"] - montant_paye)

        client_name = escape(str(vente["client_name"] or "Client de passage"))
        client_phone = escape(str(vente["client_phone"] or ""))
        client_address = escape(str(vente["client_address"] or "")).replace("\n", "<br>")
        coordonnees = "<br>".join(
            valeur for valeur in (client_phone, client_address) if valeur
        )
        numero_client = vente["client_code"] or vente["client_dossier"] or "Client de passage"
        numero_client = escape(str(numero_client))
        date_vente = escape(format_dt(vente["created_at"]))
        reference_facture = escape(str(vente["number"]))
        modalites_html = escape(modes_paiement)
        return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8">
<style>
@page {{ size: A4 portrait; margin: 10mm; }}
html, body {{ margin: 0; padding: 0; color: #303943; background: #fff; }}
body {{ font-family: Arial, sans-serif; font-size: 8pt; }}
table {{ width: 100%; border-collapse: collapse; }}
.facture {{ width: 100%; }}
.entete td {{ padding: 0 0 5mm; vertical-align: middle; }}
.titre {{ color: #00a7c4; font-size: 23pt; font-weight: bold; }}
.logo {{ width: 19mm; height: 19mm; background: #f5b900; color: #fff; text-align: center;
         vertical-align: middle; font-size: 11pt; font-weight: bold; }}
.entreprise {{ margin-top: 1mm; color: #20252b; font-size: 12pt; font-weight: bold; }}
.coord-entreprise {{ color: #59636e; line-height: 1.4; }}
.section-info {{ margin: 5mm 0; }}
.section-info td {{ width: 50%; padding: 1mm 0; vertical-align: top; }}
.meta {{ width: 100%; }}
.meta td {{ padding: 1mm; vertical-align: top; }}
.meta td:first-child {{ width: 47%; color: #66717b; }}
.destinataire {{ padding-left: 5mm !important; }}
.destinataire-titre {{ color: #59636e; font-weight: bold; }}
.note {{ margin: 4mm 0; padding: 3mm; background: #f5f8fa; border-left: 2px solid #00a7c4; }}
.articles {{ margin-top: 5mm; table-layout: fixed; border: 1px solid #aebdc5; }}
.articles th {{ padding: 2mm 1mm; color: #087f95; background: #e8f7fa; text-align: left;
                border: 1px solid #aebdc5; }}
.articles td {{ padding: 2mm 1mm; text-align: right; vertical-align: top;
                border: 1px solid #aebdc5; }}
.articles th:first-child, .articles td.designation {{ text-align: left; }}
.articles .centre {{ text-align: center; }}
.reference {{ color: #69747d; font-size: 7pt; }}
.important {{ font-weight: bold; }}
.totaux {{ width: 52%; margin: 4mm 0 0 auto; }}
.totaux td {{ padding: 1.5mm; text-align: right; }}
.totaux .valeur {{ color: #00a7c4; font-weight: bold; }}
.totaux .net td {{ padding-top: 2mm; border-top: 1px solid #bbc5cb; color: #111; font-size: 10pt; font-weight: bold; }}
.pied {{ margin-top: 7mm; border-top: 1px solid #00a7c4; }}
.pied td {{ width: 33.33%; padding: 3mm 2mm 0 0; vertical-align: top; color: #59636e; line-height: 1.4; }}
.pied strong {{ color: #303943; }}
.mentions {{ margin-top: 5mm; color: #69747d; font-size: 7pt; }}
</style></head>
<body><main class="facture">
<table class="entete"><tr>
<td><div class="titre">FACTURE</div><div class="entreprise">Helal Cristor Store</div>
<div class="coord-entreprise">Bazar El Mokhbat, Thniet El Hadjar<br>Médéa, Algérie</div></td>
<td style="width: 22%; text-align: right;"><table class="logo"><tr><td>Logo</td></tr></table></td>
</tr></table>
<table class="section-info"><tr>
<td><table class="meta">
<tr><td>Date de la facture</td><td>{date_vente}</td></tr>
<tr><td>Référence de la facture</td><td>{reference_facture}</td></tr>
<tr><td>Numéro de client</td><td>{numero_client}</td></tr>
<tr><td>Reste dû</td><td>{format_da(reste_du)}</td></tr>
<tr><td>Modalité de paiement</td><td>{modalites_html}</td></tr>
<tr><td>Émis par</td><td>Gestion de magasin</td></tr>
<tr><td>Contact client</td><td>{client_phone or "—"}</td></tr>
<tr><td>Date de la vente</td><td>{date_vente}</td></tr>
</table></td>
<td class="destinataire"><div class="destinataire-titre">Destinataire</div>
<strong>{client_name}</strong><br>{coordonnees or "—"}</td>
</tr></table>
<div class="note"><strong>Informations complémentaires</strong><br>Merci de votre confiance.</div>
<table class="articles">
<colgroup>
<col style="width: 34%"><col style="width: 8%"><col style="width: 8%">
<col style="width: 15%"><col style="width: 10%"><col style="width: 12%"><col style="width: 13%">
</colgroup>
<thead><tr><th>Description</th><th>Qté</th><th>Unité</th><th>Prix unitaire HT</th>
<th>% TVA</th><th>Total TVA</th><th>Total TTC</th></tr></thead>
<tbody>{''.join(lignes_html)}</tbody>
</table>
<table class="totaux"><tbody>
<tr><td>Total HT</td><td class="valeur">{format_da(total_ht)}</td></tr>
<tr><td>Total TVA</td><td class="valeur">{format_da(total_tva)}</td></tr>
<tr><td>Total TTC avant remise</td><td>{format_da(total_brut)}</td></tr>
{"<tr><td>Remise</td><td class='valeur'>-" + format_da(remise) + "</td></tr>" if remise else ""}
{"<tr><td>Fidélité (" + str(vente['loyalty_points_redeemed']) + " points)</td><td class='valeur'>-" + format_da(reduction_fidelite) + "</td></tr>" if reduction_fidelite else ""}
<tr><td>Total TTC</td><td class="valeur">{format_da(vente["total_centimes"])}</td></tr>
<tr><td>Montant payé</td><td>{format_da(montant_paye)}</td></tr>
<tr><td>Reste dû</td><td>{format_da(reste_du)}</td></tr>
</tbody></table>
<table class="pied"><tr>
<td><strong>Siège social</strong><br>Bazar El Mokhbat, Thniet El Hadjar<br>Médéa, Algérie<br>
RC : 26/00-175185A13<br>NIF : 17226010281710702600</td>
<td><strong>Coordonnées</strong><br>Helal Cristor Store<br>
E-mail : <a href="mailto:faycal-7205@hotmail.fr">faycal-7205@hotmail.fr</a></td>
<td><strong>Détails bancaires</strong><br>Compte : 007999990019243799/08</td>
</tr></table>
<p class="mentions">Document commercial établi à partir des informations de vente enregistrées.</p>
</main></body></html>"""

    def _construire_proforma_html(
        self,
        numero: str,
        destinataire: dict[str, str] | None = None,
        *,
        is_quote: bool = False,
        valid_until: str | None = None,
        document_at: str | None = None,
        tax_rate_overrides: dict[int, int] | None = None,
    ) -> str:
        if not self.panier:
            raise ValidationError("Le panier est vide.")
        remise = self._remise_centimes()
        total_brut = self._total()
        if remise < 0 or remise > total_brut:
            raise ValidationError("La remise est invalide pour cette proforma.")

        identifiants = [ligne["product_id"] for ligne in self.panier]
        placeholders = ",".join("?" for _ in identifiants)
        produits = self.conn.execute(
            f"SELECT id, tax_rate_bp FROM products WHERE id IN ({placeholders})",
            identifiants,
        ).fetchall()
        taux_par_produit = {produit["id"]: produit["tax_rate_bp"] for produit in produits}
        if len(taux_par_produit) != len(set(identifiants)):
            raise ValidationError("Un produit du panier n'est plus disponible.")
        if tax_rate_overrides is not None:
            taux_par_produit.update(tax_rate_overrides)

        total_net = total_brut - remise
        montants_lignes = []
        restants = []
        if total_brut:
            for index, ligne in enumerate(self.panier):
                montant, reste = divmod(
                    total_net * ligne["quantity"] * ligne["unit_price_centimes"], total_brut
                )
                montants_lignes.append(montant)
                restants.append((reste, index))
            centimes_restants = total_net - sum(montants_lignes)
            for _reste, index in sorted(restants, reverse=True)[:centimes_restants]:
                montants_lignes[index] += 1
        else:
            montants_lignes = [0] * len(self.panier)

        lignes_html = []
        total_ht = 0
        total_tva = 0
        for ligne, montant_ttc in zip(self.panier, montants_lignes):
            quantite = ligne["quantity"]
            taux_tva = taux_par_produit[ligne["product_id"]]
            montant_ht, montant_tva = split_ttc(montant_ttc, taux_tva)
            prix_unitaire_ht, _ = split_ttc(
                (montant_ttc + quantite // 2) // quantite, taux_tva
            )
            total_ht += montant_ht
            total_tva += montant_tva
            lignes_html.append(
                "<tr>"
                f"<td>{escape(str(ligne['name']))}<br><span class='reference'>"
                f"{escape(str(ligne['reference']))}</span></td>"
                f"<td class='centre'>{quantite}</td><td class='centre'>pcs</td>"
                f"<td>{format_da(prix_unitaire_ht)}</td>"
                f"<td class='centre'>{taux_tva / 100:g} %</td>"
                f"<td>{format_da(montant_tva)}</td><td>{format_da(montant_ttc)}</td>"
                "</tr>"
            )

        if destinataire is None:
            client_id = self.client.currentData()
            client = self.conn.execute(
                "SELECT name, phone, address FROM customers WHERE id = ?", (client_id,)
            ).fetchone() if client_id is not None else None
            destinataire = {
                "name": client["name"] if client else "Client à renseigner",
                "phone": client["phone"] or "" if client else "",
                "address": client["address"] or "" if client else "",
            }
        nom_client = escape(destinataire["name"])
        coordonnees_client = "<br>".join(
            escape(valeur) for valeur in (destinataire["phone"], destinataire["address"]) if valeur
        )
        date_document = escape(format_dt(document_at or now_iso()))
        document_title = "DEVIS" if is_quote else "FACTURE PROFORMA"
        number_label = "Devis n°" if is_quote else "Proforma n°"
        validity = (
            f"<br><strong>Valable jusqu'au :</strong> {escape(format_date(valid_until))}"
            if is_quote and valid_until else ""
        )
        note_document = (
            f"Ce devis est valable jusqu'au {escape(format_date(valid_until))}. "
            "Il ne réserve pas le stock et ne constitue pas une facture."
            if is_quote and valid_until
            else "Cette proforma ne constitue pas une facture et n'enregistre ni vente ni mouvement de stock."
        )
        return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><style>
@page {{ size: A4 portrait; margin: 10mm; }}
body {{ font-family: Arial, sans-serif; font-size: 8pt; color: #303943; }}
table {{ width: 100%; border-collapse: collapse; }}
.titre {{ color: #00a7c4; font-size: 23pt; font-weight: bold; }}
.entreprise {{ margin-top: 1mm; font-size: 12pt; font-weight: bold; }}
.coord {{ color: #59636e; line-height: 1.4; }}
.entete td {{ padding-bottom: 5mm; vertical-align: middle; }}
.logo {{ width: 19mm; height: 19mm; background: #f5b900; color: white; text-align: center; }}
.meta {{ margin: 4mm 0; }}
.meta td {{ padding: 1.5mm; vertical-align: top; }}
.articles {{ margin-top: 5mm; table-layout: fixed; border: 1px solid #aebdc5; }}
.articles th {{ padding: 2mm 1mm; color: #087f95; background: #e8f7fa; border: 1px solid #aebdc5; }}
.articles td {{ padding: 2mm 1mm; text-align: right; border: 1px solid #aebdc5; }}
.articles th:first-child, .articles td:first-child {{ text-align: left; }}
.centre {{ text-align: center !important; }}
.reference {{ color: #69747d; font-size: 7pt; }}
.totaux {{ width: 52%; margin: 4mm 0 0 auto; }}
.totaux td {{ padding: 1.5mm; text-align: right; }}
.pied {{ margin-top: 7mm; border-top: 1px solid #00a7c4; }}
.pied td {{ width: 33.33%; padding: 3mm 2mm 0 0; vertical-align: top; line-height: 1.4; }}
.note {{ margin-top: 7mm; padding: 3mm; background: #f5f8fa; border-left: 2px solid #00a7c4; }}
</style></head><body>
<table class="entete"><tr><td><div class="titre">{document_title}</div>
<div class="entreprise">Helal Cristor Store</div>
<div class="coord">Bazar El Mokhbat, Thniet El Hadjar<br>Médéa, Algérie</div></td>
<td style="width:22%;text-align:right"><table class="logo"><tr><td>Logo</td></tr></table></td></tr></table>
<table class="meta"><tr><td><strong>{number_label}</strong> {escape(numero)}</td>
<td><strong>Date :</strong> {date_document}{validity}</td>
<td><strong>Client :</strong> {nom_client}<br>{coordonnees_client}</td></tr></table>
<table class="articles"><colgroup><col style="width:34%"><col style="width:8%">
<col style="width:8%"><col style="width:15%"><col style="width:10%">
<col style="width:12%"><col style="width:13%"></colgroup>
<thead><tr><th>Description</th><th>Qté</th><th>Unité</th><th>Prix unitaire HT</th>
<th>% TVA</th><th>Total TVA</th><th>Total TTC</th></tr></thead>
<tbody>{''.join(lignes_html)}</tbody></table>
<table class="totaux"><tr><td>Total HT</td><td>{format_da(total_ht)}</td></tr>
<tr><td>Total TVA</td><td>{format_da(total_tva)}</td></tr>
<tr><td>Total TTC avant remise</td><td>{format_da(total_brut)}</td></tr>
{"<tr><td>Remise</td><td>-" + format_da(remise) + "</td></tr>" if remise else ""}
<tr><td><strong>Total proposé</strong></td><td><strong>{format_da(total_net)}</strong></td></tr></table>
<table class="pied"><tr>
<td><strong>Siège social</strong><br>Bazar El Mokhbat, Thniet El Hadjar<br>Médéa, Algérie<br>
RC : 26/00-175185A13<br>NIF : 17226010281710702600</td>
<td><strong>Coordonnées</strong><br>Helal Cristor Store<br>
E-mail : faycal-7205@hotmail.fr</td>
<td><strong>Détails bancaires</strong><br>Compte : 007999990019243799/08</td>
</tr></table>
<div class="note"><strong>Offre provisoire sans engagement.</strong>
{note_document}</div>
</body></html>"""

    def _construire_bon_livraison_html(self, vente_id: int) -> str:
        vente = self.conn.execute(
            "SELECT s.number, s.status, s.created_at, c.name AS client_name, "
            "c.phone AS client_phone, c.address AS client_address "
            "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id WHERE s.id = ?",
            (vente_id,),
        ).fetchone()
        if vente is None or vente["status"] != "validee":
            raise ValidationError("Le bon de livraison exige une vente validée.")

        lignes = self.conn.execute(
            "SELECT si.product_id, si.quantity, p.reference, p.name, p.track_serial "
            "FROM sale_items si JOIN products p ON p.id = si.product_id "
            "WHERE si.sale_id = ? ORDER BY si.id",
            (vente_id,),
        ).fetchall()
        if not lignes:
            raise ValidationError("La vente ne contient aucun article à livrer.")
        imeis_par_produit: dict[int, list[str]] = {}
        for appareil in self.conn.execute(
            "SELECT product_id, imei1, imei2 FROM product_serials "
            "WHERE sale_id = ? ORDER BY id",
            (vente_id,),
        ):
            details = f"IMEI1 : {appareil['imei1']}"
            if appareil["imei2"]:
                details += f" / IMEI2 : {appareil['imei2']}"
            imeis_par_produit.setdefault(appareil["product_id"], []).append(details)

        lignes_html = []
        for ligne in lignes:
            details_imei = ""
            if ligne["track_serial"]:
                appareils = imeis_par_produit.get(ligne["product_id"], [])
                if len(appareils) < ligne["quantity"]:
                    raise ValidationError(
                        f"Les IMEI de « {ligne['name']} » sont incomplets pour ce bon de livraison."
                    )
                details_imei = "<br><span class='imei'>" + "<br>".join(
                    escape(imei) for imei in appareils[:ligne["quantity"]]
                ) + "</span>"
                del appareils[:ligne["quantity"]]
            lignes_html.append(
                "<tr>"
                f"<td>{escape(str(ligne['name']))}<br><span class='reference'>"
                f"{escape(str(ligne['reference']))}</span>{details_imei}</td>"
                f"<td class='centre'>{ligne['quantity']}</td>"
                "<td class='centre'>pcs</td>"
                "<td class='centre'>☐</td>"
                "</tr>"
            )

        nom_client = escape(str(vente["client_name"] or "Client de passage"))
        coordonnees = "<br>".join(
            escape(str(valeur))
            for valeur in (vente["client_phone"], vente["client_address"]) if valeur
        )
        return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><style>
@page {{ size: A4 portrait; margin: 12mm; }}
body {{ font-family: Arial, sans-serif; font-size: 9pt; color: #303943; }}
table {{ width: 100%; border-collapse: collapse; }}
.titre {{ color: #00a7c4; font-size: 23pt; font-weight: bold; }}
.entreprise {{ margin-top: 1mm; font-size: 12pt; font-weight: bold; }}
.coord {{ color: #59636e; line-height: 1.4; }}
.entete td {{ padding-bottom: 5mm; vertical-align: middle; }}
.logo {{ width: 19mm; height: 19mm; background: #f5b900; color: white; text-align: center; }}
.meta {{ margin: 5mm 0; }}
.meta td {{ padding: 2mm; vertical-align: top; }}
.articles {{ margin-top: 5mm; border: 1px solid #aebdc5; }}
.articles th {{ padding: 2.5mm; color: #087f95; background: #e8f7fa; border: 1px solid #aebdc5; }}
.articles td {{ padding: 3mm 2mm; vertical-align: top; border: 1px solid #aebdc5; }}
.articles th:first-child, .articles td:first-child {{ text-align: left; }}
.centre {{ text-align: center; }}
.reference, .imei {{ color: #59636e; font-size: 8pt; }}
.signatures {{ margin-top: 20mm; }}
.signatures td {{ width: 50%; height: 22mm; vertical-align: top; text-align: center; border-top: 1px solid #75818a; }}
.pied {{ margin-top: 8mm; border-top: 1px solid #00a7c4; }}
.pied td {{ width: 33.33%; padding: 3mm 2mm 0 0; vertical-align: top; line-height: 1.4; }}
</style></head><body>
<table class="entete"><tr><td><div class="titre">BON DE LIVRAISON</div>
<div class="entreprise">Helal Cristor Store</div>
<div class="coord">Bazar El Mokhbat, Thniet El Hadjar<br>Médéa, Algérie</div></td>
<td style="width:22%;text-align:right"><table class="logo"><tr><td>Logo</td></tr></table></td></tr></table>
<table class="meta"><tr><td><strong>Bon n° :</strong> BL-{escape(str(vente['number']))}<br>
<strong>Date :</strong> {escape(format_dt(vente['created_at']))}</td>
<td><strong>Destinataire :</strong><br>{nom_client}<br>{coordonnees}</td></tr></table>
<table class="articles"><colgroup><col style="width:60%"><col style="width:13%">
<col style="width:13%"><col style="width:14%"></colgroup>
<thead><tr><th>Articles livrés</th><th>Quantité</th><th>Unité</th><th>Reçu</th></tr></thead>
<tbody>{''.join(lignes_html)}</tbody></table>
<table class="signatures"><tr><td>Signature et cachet du vendeur</td>
<td>Nom, date et signature du client</td></tr></table>
<table class="pied"><tr><td><strong>Siège social</strong><br>Bazar El Mokhbat, Thniet El Hadjar<br>
Médéa, Algérie<br>RC : 26/00-175185A13<br>NIF : 17226010281710702600</td>
<td><strong>Coordonnées</strong><br>Helal Cristor Store<br>E-mail : faycal-7205@hotmail.fr</td>
<td><strong>Détails bancaires</strong><br>Compte : 007999990019243799/08</td>
</tr></table></body></html>"""

    def _imprimer_document_a4(self, contenu: str, titre: str) -> None:
        imprimante = QPrinter(QPrinter.PrinterMode.HighResolution)
        mise_en_page = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(15, 15, 15, 15),
            QPageLayout.Unit.Millimeter,
        )
        if not imprimante.setPageLayout(mise_en_page):
            QMessageBox.warning(
                self, "Impression impossible", "Le format A4 n'est pas disponible."
            )
            return

        dialogue = QPrintDialog(imprimante, self)
        dialogue.setWindowTitle(titre)
        if dialogue.exec() != QDialog.DialogCode.Accepted:
            return

        document = QTextDocument(self)
        document.setHtml(contenu)
        document.setPageSize(
            imprimante.pageLayout().paintRect(QPageLayout.Unit.Point).size()
        )
        document.print_(imprimante)

    def imprimer_facture_a4(self) -> None:
        if self._derniere_vente_id is None:
            return
        try:
            contenu = self._construire_facture_a4_html(self._derniere_vente_id)
        except (ValidationError, TypeError) as erreur:
            QMessageBox.warning(self, "Facture indisponible", str(erreur))
            return

        self._imprimer_document_a4(contenu, "Imprimer la facture")

    def imprimer_proforma(self) -> None:
        if not self.panier:
            QMessageBox.warning(self, "Proforma indisponible", "Le panier est vide.")
            return
        if self._quote_id is not None:
            try:
                quote = get_quote(self.conn, self._quote_id)
                contenu = self._construire_proforma_html(
                    quote["number"],
                    self._quote_recipient,
                    is_quote=True,
                    valid_until=quote["valid_until"],
                    document_at=quote["created_at"],
                    tax_rate_overrides=self._quote_tax_rates,
                )
            except (MagasinError, sqlite3.Error, TypeError) as erreur:
                QMessageBox.warning(self, "Devis indisponible", str(erreur))
                return
            self._imprimer_document_a4(contenu, "Imprimer le devis")
            return

        client_id = self.client.currentData()
        client = self.conn.execute(
            "SELECT name, phone, address FROM customers WHERE id = ?", (client_id,)
        ).fetchone() if client_id is not None else None
        dialogue = ProformaDestinataireDialogue(
            self,
            nom=client["name"] if client else "",
            telephone=client["phone"] or "" if client else "",
            adresse=client["address"] or "" if client else "",
        )
        if ouvrir_dialogue(dialogue) != QDialog.DialogCode.Accepted or dialogue.valeurs is None:
            return
        try:
            remise = self._remise_centimes()
            if remise < 0 or remise > self._total():
                raise ValidationError("La remise est invalide pour cette proforma.")
            numero = next_number(self.conn, "PRO")
            contenu = self._construire_proforma_html(numero, dialogue.valeurs)
        except (MagasinError, sqlite3.Error, TypeError) as erreur:
            QMessageBox.warning(self, "Proforma indisponible", str(erreur))
            return
        self._imprimer_document_a4(contenu, "Imprimer la facture proforma")

    def imprimer_bon_livraison(self) -> None:
        if self._derniere_vente_id is None:
            return
        try:
            contenu = self._construire_bon_livraison_html(self._derniere_vente_id)
        except (ValidationError, TypeError) as erreur:
            QMessageBox.warning(self, "Bon de livraison indisponible", str(erreur))
            return
        self._imprimer_document_a4(contenu, "Imprimer le bon de livraison")

    def enregistrer_devis(self) -> None:
        if self._quote_id is not None:
            return
        if not self.panier:
            montrer_erreur(self, ValidationError("Le panier est vide."))
            return
        try:
            remise = self._remise_centimes()
            if remise < 0 or remise > self._total():
                raise ValidationError("La remise est invalide pour ce devis.")
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        customer_id = self.client.currentData()
        customer = self.conn.execute(
            "SELECT name, phone, address FROM customers WHERE id = ?", (customer_id,)
        ).fetchone() if customer_id is not None else None
        dialogue = ProformaDestinataireDialogue(
            self,
            nom=customer["name"] if customer else "",
            telephone=(customer["phone"] or "") if customer else "",
            adresse=(customer["address"] or "") if customer else "",
        )
        if ouvrir_dialogue(dialogue) != QDialog.DialogCode.Accepted or dialogue.valeurs is None:
            return
        recipient = dialogue.valeurs
        if customer is None or recipient["name"] != customer["name"]:
            customer_id = None

        try:
            quote = create_quote(
                self.conn,
                [
                    QuoteLine(
                        ligne["product_id"], ligne["quantity"],
                        ligne["unit_price_centimes"],
                    )
                    for ligne in self.panier
                ],
                recipient_name=recipient["name"],
                recipient_phone=recipient["phone"],
                recipient_address=recipient["address"],
                customer_id=customer_id,
                discount_centimes=remise,
                actor_user_id=self.actor_user_id,
            )
            saved_quote = get_quote(self.conn, quote["id"])
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return

        self._quote_id = quote["id"]
        self._quote_number = quote["number"]
        self._quote_valid_until = quote["valid_until"]
        self._quote_created_at = quote["created_at"]
        self._quote_recipient = recipient
        self._quote_tax_rates = {
            ligne["product_id"]: ligne["tax_rate_bp"] for ligne in saved_quote["lines"]
        }
        for ligne in self.panier:
            ligne["imeis"] = []
        self.paiements.clear()
        self._rafraichir_paiements()
        self._mettre_a_jour_boutons()
        QMessageBox.information(
            self,
            "Devis enregistré",
            f"Devis {quote['number']} enregistré jusqu'au "
            f"{format_date(quote['valid_until'])}.\n"
            "Le stock n'est pas réservé. Vous pouvez imprimer le devis ou le charger "
            "depuis l'écran Devis.",
        )

    def charger_devis(self, quote_id: int) -> bool:
        if (self.panier or self.paiements) and not demander_confirmation(
            self,
            "Charger un devis",
            "Remplacer le panier et les paiements en cours par les lignes du devis ?",
        ):
            return False
        try:
            quote = get_quote(self.conn, quote_id)
            if quote["status"] != "open":
                raise ValidationError("Ce devis n'est plus ouvert.")
            if quote["valid_until"] < today().isoformat():
                raise ValidationError("Ce devis a expiré et ne peut plus être converti.")
            panier = []
            for ligne in quote["lines"]:
                product = self.conn.execute(
                    "SELECT archived FROM products WHERE id = ?", (ligne["product_id"],)
                ).fetchone()
                if product is None or product["archived"]:
                    raise ValidationError(
                        f"Le produit « {ligne['name']} » du devis est archivé ou introuvable."
                    )
                panier.append({
                    "product_id": ligne["product_id"],
                    "reference": ligne["reference"],
                    "name": ligne["name"],
                    "quantity": ligne["quantity"],
                    "unit_price_centimes": ligne["unit_price_centimes"],
                    "imeis": [],
                })
            customer_index = self.client.findData(quote["customer_id"])
            if quote["customer_id"] is not None and customer_index < 0:
                raise ValidationError("Le client de ce devis n'est plus disponible.")
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return False

        self.panier = panier
        self.paiements.clear()
        self.client.setCurrentIndex(max(0, customer_index))
        self.remise.setText(centimes_to_text(quote["discount_centimes"]))
        self._quote_id = quote["id"]
        self._quote_number = quote["number"]
        self._quote_valid_until = quote["valid_until"]
        self._quote_created_at = quote["created_at"]
        self._quote_recipient = {
            "name": quote["recipient_name"],
            "phone": quote["recipient_phone"] or "",
            "address": quote["recipient_address"] or "",
        }
        self._quote_tax_rates = {
            ligne["product_id"]: ligne["tax_rate_bp"] for ligne in quote["lines"]
        }
        self.points_fidelite.setValue(0)
        self._rafraichir_panier()
        self._rafraichir_paiements()
        return True

    def _saisir_imeis_devis(self) -> bool:
        nouvelles_valeurs = []
        deja_saisis = set()
        for ligne in self.panier:
            product = self.conn.execute(
                "SELECT track_serial FROM products WHERE id = ?", (ligne["product_id"],)
            ).fetchone()
            if product is None or not product["track_serial"]:
                nouvelles_valeurs.append(ligne["imeis"])
                continue
            texte, accepte = QInputDialog.getMultiLineText(
                self,
                "IMEI des appareils du devis",
                f"Saisissez un IMEI en stock par appareil pour « {ligne['name']} » "
                f"({ligne['quantity']} appareil(s)), un par ligne :",
            )
            if not accepte:
                return False
            try:
                imeis = [
                    validate_imei(value, f"IMEI {index}")
                    for index, value in enumerate(texte.splitlines(), start=1)
                    if value.strip()
                ]
                if len(imeis) != ligne["quantity"]:
                    raise ValidationError(
                        f"Saisissez exactement {ligne['quantity']} IMEI pour « {ligne['name']} »."
                    )
                for imei in imeis:
                    cleaned = clean_imei(imei)
                    if cleaned in deja_saisis:
                        raise ValidationError(f"L'IMEI {imei} apparaît plusieurs fois.")
                    deja_saisis.add(cleaned)
            except MagasinError as erreur:
                montrer_erreur(self, erreur)
                return False
            nouvelles_valeurs.append(imeis)
        for ligne, imeis in zip(self.panier, nouvelles_valeurs):
            ligne["imeis"] = imeis
        return True

    def valider_vente(self) -> None:
        if not self.panier:
            montrer_erreur(self, ValidationError("Le panier est vide."))
            return
        if self._quote_id is not None and not self._saisir_imeis_devis():
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
                loyalty_points=self.points_fidelite.value(),
                quote_id=self._quote_id,
                actor_user_id=self.actor_user_id,
            )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return

        created_at = self.conn.execute(
            "SELECT created_at FROM sales WHERE id = ?", (resultat.sale_id,)
        ).fetchone()["created_at"]
        self._dernier_ticket = self._construire_ticket_html(
            resultat, list(self.panier), remise, created_at
        )
        self._derniere_vente_id = resultat.sale_id
        self.btn_imprimer.setEnabled(True)
        self.btn_imprimer_facture.setEnabled(True)
        self.btn_bon_livraison.setEnabled(True)
        self._quote_id = None
        self._quote_number = None
        self._quote_valid_until = None
        self._quote_created_at = None
        self._quote_recipient = None
        self._quote_tax_rates = {}
        self.panier.clear()
        self.paiements.clear()
        self.remise.setText("0")
        self.points_fidelite.setValue(0)
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

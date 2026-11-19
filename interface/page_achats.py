"""Réceptions fournisseur et consultation de leur historique."""

import sqlite3

from PySide6.QtCore import QDate, QStringListModel, Qt
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QComboBox, QCompleter, QDateEdit, QDialog, QGroupBox, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog)

from magasin.clock import format_dt, today
from magasin.db import transaction
from magasin.errors import MagasinError, ValidationError
from magasin.money import centimes_to_text, da_to_centimes, format_da
from magasin.products import create_product, find_product
from magasin.purchases import (ReceiptLine, cancel_purchase_receipt, create_supplier,
                               get_purchase_receipt, receive_stock,
                               record_supplier_payment_for_supplier, set_supplier_current_debt,
                               update_purchase_receipt, list_suppliers, supplier_balances,
                               supplier_receipt_balances)
from magasin.serials import parse_imei_list

from .dialogues import NouveauProduitDialogue
from .outils import demander_confirmation, montrer_erreur, ouvrir_dialogue

TEXTE_AJOUTER = "Ajouter à la liste"
TEXTE_MODIFIER_LIGNE = "Enregistrer les modifications"
TEXTE_VALIDER = "Valider et mettre en stock"

MOYENS_PAIEMENT = (
    ("Espèces", "especes"),
    ("Virement bancaire", "virement"),
    ("Chèque", "cheque"),
)
LIBELLE_MOYEN = {code: libelle for libelle, code in MOYENS_PAIEMENT}


class PageAchats(QWidget):
    """Enregistre les produits reçus et augmente le stock via son historique de mouvements."""

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------
    def __init__(self, conn, actor_user_id: int | None = None):
        super().__init__()
        self.conn = conn
        self.actor_user_id = actor_user_id
        self.panier: list[dict] = []
        self._ligne_modifiee: int | None = None
        self._reception_modifiee: int | None = None

        titre = QLabel("Achats et réceptions")
        titre.setObjectName("titre")
        self.resume = QLabel(
            "1. Indiquez le fournisseur.  2. Ajoutez les articles et quantités reçus.  "
            "Saisissez le coût d'achat unitaire pour calculer le total.  "
            "3. Indiquez le montant payé maintenant (0 pour payer plus tard), puis validez. "
            "Le reste apparaîtra dans les règlements fournisseurs à effectuer."
        )
        self.resume.setObjectName("info")
        self.resume.setWordWrap(True)

        groupe_fournisseur = self._construire_groupe_fournisseur()
        groupe_articles = self._construire_groupe_articles()
        groupe_dettes = self._construire_groupe_dettes()
        groupe_historique = self._construire_groupe_historique()

        contenu = QWidget()
        mise_en_page = QVBoxLayout(contenu)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(self.resume)
        mise_en_page.addWidget(groupe_fournisseur)
        mise_en_page.addWidget(groupe_articles, 1)
        mise_en_page.addWidget(groupe_dettes)
        mise_en_page.addWidget(groupe_historique)
        defilement = QScrollArea(self)
        defilement.setWidgetResizable(True)
        defilement.setWidget(contenu)
        layout_page = QVBoxLayout(self)
        layout_page.setContentsMargins(0, 0, 0, 0)
        layout_page.addWidget(defilement)

        self.fournisseur.textChanged.connect(self._mettre_a_jour_etat)
        self.paiement_maintenant.textChanged.connect(self._mettre_a_jour_reste)
        self.rafraichir()

    def _construire_groupe_fournisseur(self) -> QGroupBox:
        groupe = QGroupBox("1. Fournisseur")
        self.fournisseur = QLineEdit()
        self.fournisseur.setPlaceholderText("Nom du fournisseur")
        self.fournisseurs_modele = QStringListModel(self.fournisseur)
        self.fournisseur.setCompleter(QCompleter(self.fournisseurs_modele, self.fournisseur))
        self.fournisseur.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.reference_fournisseur = QLineEdit()
        self.reference_fournisseur.setPlaceholderText("N° facture / bon fournisseur (facultatif)")
        self.date_reception = QDateEdit(QDate.fromString(today().isoformat(), "yyyy-MM-dd"))
        self.date_reception.setCalendarPopup(True)
        self.date_reception.setDisplayFormat("dd/MM/yyyy")

        self.paiement_maintenant = QLineEdit("0")
        self.paiement_maintenant.setPlaceholderText("0 si paiement plus tard")
        self.reste_a_payer = QLineEdit("0,00")
        self.reste_a_payer.setReadOnly(True)
        self.moyen_paiement = QComboBox()
        for libelle, code in MOYENS_PAIEMENT:
            self.moyen_paiement.addItem(libelle, code)

        formulaire = QFormLayout(groupe)
        formulaire.addRow("Fournisseur *", self.fournisseur)
        formulaire.addRow("Référence fournisseur", self.reference_fournisseur)
        formulaire.addRow("Date de réception", self.date_reception)
        formulaire.addRow("Montant versé (DA)", self.paiement_maintenant)
        formulaire.addRow("Reste à payer / Crédit fournisseur (DA)", self.reste_a_payer)
        formulaire.addRow("Moyen de paiement", self.moyen_paiement)
        return groupe

    def _construire_groupe_articles(self) -> QGroupBox:
        groupe = QGroupBox("2. Articles reçus")
        aide = QLabel(
            "Scannez ou saisissez la référence, le code-barres ou le nom du produit. "
            "Saisissez le coût d'achat unitaire de la facture ; il est prérempli depuis la fiche "
            "produit s'il est disponible. Vous pouvez modifier le coût et le prix de vente "
            "avant d'ajouter la ligne : la fiche produit sera mise à jour à la validation. "
            "Si le produit est introuvable, vous pourrez créer sa fiche."
        )
        aide.setObjectName("info")
        aide.setWordWrap(True)

        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Référence, code-barres ou nom du produit")
        self.recherche.returnPressed.connect(self.ajouter_produit)
        self.quantite = QSpinBox()
        self.quantite.setRange(1, 9999)
        self.quantite.setValue(1)
        self.cout_unitaire = QLineEdit()
        self.cout_unitaire.setPlaceholderText("Prix d'achat unitaire en DA")
        self.prix_vente = QLineEdit()
        self.prix_vente.setPlaceholderText("Prix de vente unitaire en DA")
        self.bouton_ajouter = QPushButton(TEXTE_AJOUTER)
        self.bouton_ajouter.clicked.connect(self.ajouter_produit)

        barre_produit = QHBoxLayout()
        barre_produit.addWidget(self.recherche, 1)
        barre_produit.addWidget(QLabel("Quantité reçue"))
        barre_produit.addWidget(self.quantite)
        barre_produit.addWidget(QLabel("Coût unitaire (DA)"))
        barre_produit.addWidget(self.cout_unitaire)
        barre_produit.addWidget(QLabel("Prix de vente (DA)"))
        barre_produit.addWidget(self.prix_vente)
        barre_produit.addWidget(self.bouton_ajouter)

        self.note_panier_vide = QLabel("Aucun article ajouté. Recherchez un produit ci-dessus.")
        self.note_panier_vide.setObjectName("info")
        self.tableau_panier = self._creer_tableau(
            ["Référence", "Produit", "Quantité reçue", "Coût unitaire", "Prix de vente",
             "Total ligne", "Suivi IMEI", "Fiche"]
        )
        self.total_reception = QLabel("Total d'achat : 0,00 DA")
        self.total_reception.setObjectName("titre")

        self.bouton_modifier_ligne = QPushButton("Modifier la ligne")
        self.bouton_modifier_ligne.clicked.connect(self.charger_ligne_selectionnee)
        self.bouton_modifier_ligne.setEnabled(False)
        self.bouton_retirer = QPushButton("Supprimer la ligne")
        self.bouton_retirer.clicked.connect(self.retirer_ligne)
        self.bouton_retirer.setEnabled(False)
        self.tableau_panier.itemSelectionChanged.connect(self._mettre_a_jour_actions_ligne)
        self.bouton_enregistrer = QPushButton(TEXTE_VALIDER)
        self.bouton_enregistrer.setObjectName("principal")
        self.bouton_enregistrer.clicked.connect(self.enregistrer_reception)

        actions = QHBoxLayout()
        actions.addWidget(self.bouton_modifier_ligne)
        actions.addWidget(self.bouton_retirer)
        actions.addStretch()
        actions.addWidget(self.bouton_enregistrer)
        self.note_validation = QLabel("")
        self.note_validation.setObjectName("info")
        self.note_validation.setWordWrap(True)

        layout = QVBoxLayout(groupe)
        layout.addWidget(aide)
        layout.addLayout(barre_produit)
        layout.addWidget(self.note_panier_vide)
        layout.addWidget(self.tableau_panier, 1)
        layout.addWidget(self.total_reception)
        layout.addLayout(actions)
        layout.addWidget(self.note_validation)
        return groupe

    def _construire_groupe_dettes(self) -> QGroupBox:
        groupe = QGroupBox("Suivi des règlements fournisseurs")
        self.note_dettes_vides = QLabel("Aucune réception enregistrée.")
        self.note_dettes_vides.setObjectName("info")
        self.tableau_dettes = self._creer_tableau(
            ["Fournisseur", "Réceptions", "Total achats", "Payé", "Dette / crédit net", "Moyen(s)"]
        )
        self.bouton_regler = QPushButton("Enregistrer un versement fournisseur")
        self.bouton_regler.clicked.connect(self.regler_reception_selectionnee)
        self.bouton_regler.setEnabled(False)
        self.tableau_dettes.itemSelectionChanged.connect(self._mettre_a_jour_bouton_reglement)
        self.bouton_dette_initiale = QPushButton("Définir / corriger la dette d'un fournisseur")
        self.bouton_dette_initiale.clicked.connect(self.definir_dette_fournisseur)

        boutons = QHBoxLayout()
        boutons.addWidget(self.bouton_regler)
        boutons.addWidget(self.bouton_dette_initiale)
        boutons.addStretch()
        layout = QVBoxLayout(groupe)
        layout.addWidget(self.note_dettes_vides)
        layout.addWidget(self.tableau_dettes)
        layout.addLayout(boutons)
        groupe.setMaximumHeight(240)
        return groupe

    def _construire_groupe_historique(self) -> QGroupBox:
        groupe = QGroupBox("3. Réceptions déjà enregistrées")
        self.note_historique_vide = QLabel("Aucune réception enregistrée pour le moment.")
        self.note_historique_vide.setObjectName("info")
        self.tableau_historique = self._creer_tableau(
            ["Date", "Réception", "Fournisseur", "Pièce fournisseur", "Produit", "Quantité", "État"]
        )
        self.tableau_historique.itemSelectionChanged.connect(self._mettre_a_jour_actions_reception)
        self.bouton_modifier_reception = QPushButton("Modifier la réception")
        self.bouton_modifier_reception.clicked.connect(self.charger_reception_selectionnee)
        self.bouton_modifier_reception.setEnabled(False)
        self.bouton_annuler_reception = QPushButton("Annuler la réception")
        self.bouton_annuler_reception.clicked.connect(self.annuler_reception_selectionnee)
        self.bouton_annuler_reception.setEnabled(False)

        actions = QHBoxLayout()
        actions.addWidget(self.bouton_modifier_reception)
        actions.addWidget(self.bouton_annuler_reception)
        actions.addStretch()
        layout = QVBoxLayout(groupe)
        layout.addWidget(self.note_historique_vide)
        layout.addWidget(self.tableau_historique)
        layout.addLayout(actions)
        groupe.setMaximumHeight(240)
        return groupe

    @staticmethod
    def _creer_tableau(entetes: list[str]) -> QTableWidget:
        tableau = QTableWidget(0, len(entetes))
        tableau.setHorizontalHeaderLabels(entetes)
        tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        tableau.setSelectionMode(QAbstractItemView.SingleSelection)
        tableau.setAlternatingRowColors(True)
        tableau.verticalHeader().setVisible(False)
        tableau.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        if len(entetes) > 1:
            tableau.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        return tableau

    # ------------------------------------------------------------------
    # Rafraîchissement des tableaux
    # ------------------------------------------------------------------
    def rafraichir(self) -> None:
        self.fournisseurs_modele.setStringList(
            [s["name"] for s in list_suppliers(self.conn, include_archived=False)]
        )
        self._afficher_panier()
        self._rafraichir_historique()
        self._rafraichir_dettes()
        self.bouton_regler.setEnabled(False)
        self.bouton_modifier_reception.setEnabled(False)
        self.bouton_annuler_reception.setEnabled(False)

    def _rafraichir_historique(self) -> None:
        mouvements = self.conn.execute(
            """
            SELECT m.created_at, m.ref_number, m.counterparty, m.note, m.quantity,
                   p.reference, p.name, m.ref_id AS receipt_id, r.status
            FROM stock_movements m
            JOIN products p ON p.id = m.product_id
            JOIN purchase_receipts r ON r.id = m.ref_id
            WHERE m.movement_type = 'reception_achat'
              AND m.ref_type IN ('Receipt','ReceiptAdjustment')
            ORDER BY m.id DESC
            LIMIT 200
            """
        ).fetchall()
        self.tableau_historique.setRowCount(len(mouvements))
        self.note_historique_vide.setVisible(not mouvements)
        self.tableau_historique.setVisible(bool(mouvements))
        prefixe = " — pièce fournisseur "
        for ligne, mouvement in enumerate(mouvements):
            note = mouvement["note"] or ""
            piece_fournisseur = note.split(prefixe, 1)[1] if prefixe in note else ""
            valeurs = (
                format_dt(mouvement["created_at"]),
                mouvement["ref_number"] or "—",
                mouvement["counterparty"] or "—",
                piece_fournisseur or "—",
                f"{mouvement['reference']} — {mouvement['name']}",
                str(mouvement["quantity"]),
                "Annulée" if mouvement["status"] == "annulee" else "Validée",
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                cellule.setData(Qt.UserRole, mouvement["receipt_id"])
                cellule.setData(Qt.UserRole + 1, mouvement["status"])
                if colonne == 5:
                    cellule.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau_historique.setItem(ligne, colonne, cellule)

    def _rafraichir_dettes(self) -> None:
        soldes = supplier_balances(self.conn)
        self.note_dettes_vides.setVisible(not soldes)
        self.tableau_dettes.setVisible(bool(soldes))
        self.tableau_dettes.setRowCount(len(soldes))
        for ligne, solde in enumerate(soldes):
            moyens = ", ".join(
                LIBELLE_MOYEN.get(m, m)
                for m in (solde["methods"] or "").split(",") if m
            ) or "—"
            valeurs = (
                solde["supplier"],
                str(solde["receipt_count"]),
                format_da(solde["total_centimes"]),
                format_da(solde["paid_centimes"]),
                format_da(solde["due_centimes"]),
                moyens,
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                cellule.setData(Qt.UserRole, solde["supplier_id"])
                if colonne == 0:
                    cellule.setData(Qt.UserRole + 1, solde["due_centimes"])
                if colonne >= 2:
                    cellule.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau_dettes.setItem(ligne, colonne, cellule)

    def _afficher_panier(self) -> None:
        self.tableau_panier.setRowCount(len(self.panier))
        self.note_panier_vide.setVisible(not self.panier)
        self.tableau_panier.setVisible(bool(self.panier))
        for ligne, produit in enumerate(self.panier):
            montant_ligne = produit["quantity"] * produit["unit_cost_centimes"]
            valeurs = (
                produit["reference"],
                produit["name"],
                str(produit["quantity"]),
                format_da(produit["unit_cost_centimes"]),
                format_da(produit["unit_price_centimes"]),
                format_da(montant_ligne),
                f"{len(produit['imeis'])} appareil(s)" if produit["imeis"] is not None else "—",
                "À créer" if produit.get("new_product") is not None else "Existante",
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                cellule.setData(Qt.UserRole, produit["product_id"])
                if colonne >= 2:
                    cellule.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tableau_panier.setItem(ligne, colonne, cellule)
        self.total_reception.setText(f"Total d'achat : {format_da(self._total_panier())}")
        self._mettre_a_jour_actions_ligne()
        self._mettre_a_jour_reste()
        self._mettre_a_jour_etat()

    # ------------------------------------------------------------------
    # Mise à jour de l'état des boutons et du reste à payer
    # ------------------------------------------------------------------
    def _total_panier(self) -> int:
        return sum(l["quantity"] * l["unit_cost_centimes"] for l in self.panier)

    def _mettre_a_jour_bouton_reglement(self) -> None:
        ligne = self.tableau_dettes.currentRow()
        item = self.tableau_dettes.item(ligne, 0) if ligne >= 0 else None
        restant = item.data(Qt.UserRole + 1) if item is not None else 0
        self.bouton_regler.setEnabled(bool(restant and restant > 0))

    def _mettre_a_jour_actions_ligne(self) -> None:
        selected = self.tableau_panier.currentRow() >= 0
        self.bouton_modifier_ligne.setEnabled(selected)
        self.bouton_retirer.setEnabled(selected)

    def _mettre_a_jour_actions_reception(self) -> None:
        row = self.tableau_historique.currentRow()
        item = self.tableau_historique.item(row, 0) if row >= 0 else None
        status = item.data(Qt.UserRole + 1) if item is not None else None
        active = item is not None and status == "validee"
        self.bouton_modifier_reception.setEnabled(active)
        self.bouton_annuler_reception.setEnabled(active)

    def _mettre_a_jour_reste(self) -> None:
        """Reste du bon = Total_achat - Montant_verse."""
        try:
            verse = da_to_centimes(self.paiement_maintenant.text())
        except MagasinError:
            self.reste_a_payer.setText("—")
            return
        self.reste_a_payer.setText(format_da(self._total_panier() - verse))

    def _mettre_a_jour_etat(self) -> None:
        pret = bool(self.panier) and bool(self.fournisseur.text().strip())
        self.bouton_enregistrer.setEnabled(pret)
        if not self.panier:
            self.note_validation.setText("Ajoutez au moins un article pour préparer la réception.")
        elif not self.fournisseur.text().strip():
            self.note_validation.setText(
                "Saisissez le nom du fournisseur pour activer la validation."
            )
        else:
            self.note_validation.setText(
                "Prêt à valider : le stock sera mis à jour. Le montant non réglé restera dû "
                "au fournisseur."
            )

    # ------------------------------------------------------------------
    # Ajout d'un produit au panier
    # ------------------------------------------------------------------
    def _choisir_produit(self, texte: str):
        """Cherche un produit existant (actif, sinon archivé).

        Retourne le produit trouvé, ou None si aucun ne correspond.
        Retourne False si l'utilisateur annule.
        """
        tous = find_product(self.conn, texte)
        actifs = [p for p in tous if not p["archived"]]
        if len(actifs) == 1:
            return actifs[0]
        if len(actifs) > 1:
            choix = [
                f"{p['reference']} — {p['name']} — {format_da(p['price_centimes'])}"
                for p in actifs
            ]
            libelle, accepte = QInputDialog.getItem(
                self, "Choisir un produit", "Résultats :", choix, 0, False
            )
            return actifs[choix.index(libelle)] if accepte else False
        if tous:
            archive = tous[0]
            if not demander_confirmation(
                self,
                "Produit archivé",
                f"« {archive['reference']} — {archive['name']} » existe déjà "
                "mais il est archivé.\n\nL'utiliser pour cette réception ?",
            ):
                return False
            return archive
        return None

    def _creer_fiche_produit(self, texte: str):
        """Propose de créer une fiche. Retourne (produit, nouveau_produit) ou None si annulé.

        Si la référence saisie existe déjà, le produit existant est repris
        (plus de message d'erreur d'existence).
        """
        if not demander_confirmation(
            self,
            "Produit introuvable",
            f"Aucun produit actif ne correspond à « {texte} ».\n\n"
            "Voulez-vous créer une nouvelle fiche produit ? "
            "La quantité reçue ne sera ajoutée au stock qu'après validation de la réception.",
        ):
            return None
        dialogue = NouveauProduitDialogue(self, reference_suggeree=texte)
        dialogue.avec_stock_initial = False
        dialogue.formulaire.removeRow(dialogue.stock_initial)
        note = QLabel("La quantité sera ajoutée au stock lorsque vous enregistrerez la réception.")
        note.setObjectName("info")
        note.setWordWrap(True)
        dialogue.formulaire.addRow("", note)
        if ouvrir_dialogue(dialogue) != QDialog.DialogCode.Accepted or dialogue.valeurs is None:
            return None

        valeurs = dialogue.valeurs
        existants = [
            p for p in find_product(self.conn, valeurs["reference"])
            if p["reference"].casefold() == valeurs["reference"].casefold()
        ]
        if existants:
            return existants[0], None
        produit = {
            "id": None,
            "reference": valeurs["reference"],
            "name": valeurs["name"],
            "cost_centimes": valeurs["cost_centimes"],
            "price_centimes": valeurs["price_centimes"],
            "track_serial": valeurs["track_serial"],
        }
        return produit, valeurs

    def _produit_ligne_modifiee(self, index: int):
        """Reconstitue (produit, nouveau_produit) pour la ligne du panier en cours de modification."""
        ligne = self.panier[index]
        nouveau = ligne["new_product"]
        if nouveau is not None:
            produit = {
                "id": None,
                "reference": ligne["reference"],
                "name": ligne["name"],
                "cost_centimes": ligne["unit_cost_centimes"],
                "price_centimes": ligne["unit_price_centimes"],
                "track_serial": nouveau["track_serial"],
            }
        else:
            produit = self.conn.execute(
                "SELECT * FROM products WHERE id = ?", (ligne["product_id"],)
            ).fetchone()
        return produit, nouveau

    def _lire_prix(self, produit, index_modifie):
        """Lit coût et prix de vente saisis. Préremplit depuis la fiche si les champs sont vides."""
        if index_modifie is None:
            if not self.cout_unitaire.text().strip():
                self.cout_unitaire.setText(centimes_to_text(produit["cost_centimes"]))
            if not self.prix_vente.text().strip():
                self.prix_vente.setText(centimes_to_text(produit["price_centimes"]))
        try:
            cout = da_to_centimes(self.cout_unitaire.text())
            prix = da_to_centimes(self.prix_vente.text())
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return None
        if cout <= 0:
            montrer_erreur(
                self,
                ValidationError(
                    "Saisissez le coût d'achat unitaire en DA avant d'ajouter l'article. "
                    "Le coût de la fiche produit peut servir de valeur de départ."
                ),
            )
            self.cout_unitaire.setFocus()
            return None
        return cout, prix

    def _saisir_imeis(self, produit):
        """Demande les IMEI. Retourne (imeis, quantite) ou None si annulé/invalide."""
        texte_imeis, accepte = QInputDialog.getMultiLineText(
            self,
            "IMEI des appareils reçus",
            f"Saisissez un IMEI1 par appareil et, si nécessaire, son IMEI2 sur la même ligne "
            f"pour « {produit['name']} » :",
        )
        if not accepte:
            return None
        try:
            imeis = parse_imei_list(texte_imeis)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return None
        return imeis, len(imeis)

    def _trouver_ligne_panier(self, produit, nouveau_produit):
        for ligne in self.panier:
            if nouveau_produit is None:
                if ligne["product_id"] == produit["id"]:
                    return ligne
            elif (ligne["new_product"] is not None
                  and ligne["reference"].casefold() == produit["reference"].casefold()):
                return ligne
        return None

    def _fusionner_dans_ligne(self, ligne, quantite, cout, prix, imeis) -> bool:
        """Ajoute la quantité à une ligne déjà présente. Retourne False si refusé."""
        if ligne["unit_cost_centimes"] != cout or ligne["unit_price_centimes"] != prix:
            montrer_erreur(
                self,
                ValidationError(
                    "Ce produit est déjà dans la réception avec d'autres prix. "
                    "Utilisez « Modifier la ligne » pour les changer."
                ),
            )
            return False
        if imeis is not None:
            anciens = {i for paire in ligne["imeis"] for i in paire if i}
            nouveaux = {i for paire in imeis for i in paire if i}
            doublons = anciens & nouveaux
            if doublons:
                montrer_erreur(
                    self,
                    ValidationError(f"L'IMEI {sorted(doublons)[0]} est déjà dans la réception."),
                )
                return False
            ligne["imeis"].extend(imeis)
        ligne["quantity"] += quantite
        return True

    def ajouter_produit(self) -> None:
        index_modifie = self._ligne_modifiee

        # 1. Déterminer le produit
        if index_modifie is not None:
            produit, nouveau_produit = self._produit_ligne_modifiee(index_modifie)
        else:
            texte = self.recherche.text().strip()
            if not texte:
                montrer_erreur(self, ValidationError("Saisissez ou scannez un produit reçu."))
                return
            trouve = self._choisir_produit(texte)
            if trouve is False:
                return
            if trouve is None:
                resultat = self._creer_fiche_produit(texte)
                if resultat is None:
                    return
                produit, nouveau_produit = resultat
            else:
                produit, nouveau_produit = trouve, None

        # 2. Prix
        prix_lus = self._lire_prix(produit, index_modifie)
        if prix_lus is None:
            return
        cout_unitaire, prix_vente = prix_lus

        # 3. Quantité / IMEI
        quantite = self.quantite.value()
        imeis = None
        if produit["track_serial"]:
            saisie = self._saisir_imeis(produit)
            if saisie is None:
                return
            imeis, quantite = saisie

        # 4. Mise à jour du panier
        if index_modifie is not None:
            self.panier[index_modifie].update({
                "quantity": quantite,
                "unit_cost_centimes": cout_unitaire,
                "unit_price_centimes": prix_vente,
                "imeis": imeis,
            })
        else:
            ligne_existante = self._trouver_ligne_panier(produit, nouveau_produit)
            if ligne_existante is not None:
                if not self._fusionner_dans_ligne(
                    ligne_existante, quantite, cout_unitaire, prix_vente, imeis
                ):
                    return
            else:
                self.panier.append({
                    "product_id": produit["id"],
                    "new_product": nouveau_produit,
                    "reference": produit["reference"],
                    "name": produit["name"],
                    "quantity": quantite,
                    "unit_cost_centimes": cout_unitaire,
                    "unit_price_centimes": prix_vente,
                    "imeis": imeis,
                })

        self._reinitialiser_saisie_produit()
        self._afficher_panier()

    def _reinitialiser_saisie_produit(self) -> None:
        self._ligne_modifiee = None
        self.recherche.clear()
        self.cout_unitaire.clear()
        self.prix_vente.clear()
        self.quantite.setValue(1)
        self.bouton_ajouter.setText(TEXTE_AJOUTER)

    def charger_ligne_selectionnee(self) -> None:
        index = self.tableau_panier.currentRow()
        if not 0 <= index < len(self.panier):
            return
        produit = self.panier[index]
        self._ligne_modifiee = index
        self.recherche.setText(produit["reference"])
        self.quantite.setValue(produit["quantity"])
        self.cout_unitaire.setText(centimes_to_text(produit["unit_cost_centimes"]))
        self.prix_vente.setText(centimes_to_text(produit["unit_price_centimes"]))
        self.bouton_ajouter.setText(TEXTE_MODIFIER_LIGNE)

    def retirer_ligne(self) -> None:
        ligne = self.tableau_panier.currentRow()
        if 0 <= ligne < len(self.panier):
            del self.panier[ligne]
            if self._ligne_modifiee == ligne:
                self._ligne_modifiee = None
                self.bouton_ajouter.setText(TEXTE_AJOUTER)
            self._afficher_panier()

    # ------------------------------------------------------------------
    # Validation de la réception
    # ------------------------------------------------------------------
    def _dette_globale_fournisseur(self, fournisseur: str) -> int:
        nom = fournisseur.casefold()
        return next(
            (s["due_centimes"] for s in supplier_balances(self.conn)
             if s["supplier"].casefold() == nom),
            0,
        )

    def _construire_lignes_reception(self, original) -> list[ReceiptLine]:
        """Crée les fiches produits à créer, puis les lignes de réception (dans la transaction)."""
        anciennes_quantites = {
            item["product_id"]: item["quantity"]
            for item in (original["items"] if original else [])
        }
        lignes = []
        for ligne in self.panier:
            product_id = ligne["product_id"]
            if ligne["new_product"] is not None:
                valeurs = ligne["new_product"].copy()
                reference = valeurs.pop("reference")
                nom = valeurs.pop("name")
                valeurs.pop("price_centimes", None)
                valeurs.pop("cost_centimes", None)
                valeurs.pop("initial_stock", None)
                product_id = create_product(
                    self.conn, reference, nom, ligne["unit_price_centimes"],
                    cost_centimes=ligne["unit_cost_centimes"],
                    actor_user_id=self.actor_user_id,
                    **valeurs,
                )
            imeis = ligne["imeis"]
            if (
                self._reception_modifiee is not None
                and imeis is not None
                and ligne["quantity"] > anciennes_quantites.get(product_id, 0)
            ):
                imeis = imeis[anciennes_quantites.get(product_id, 0):]
            lignes.append(ReceiptLine(
                product_id=product_id,
                quantity=ligne["quantity"],
                imeis=imeis,
                unit_cost_centimes=ligne["unit_cost_centimes"],
                unit_price_centimes=ligne["unit_price_centimes"],
            ))
        return lignes

    def _reinitialiser_formulaire(self) -> None:
        self.panier.clear()
        self._reception_modifiee = None
        self.fournisseur.clear()
        self.reference_fournisseur.clear()
        self.paiement_maintenant.setText("0")
        self.paiement_maintenant.setEnabled(True)
        self.moyen_paiement.setEnabled(True)
        self.date_reception.setDate(QDate.fromString(today().isoformat(), "yyyy-MM-dd"))
        self.bouton_enregistrer.setText(TEXTE_VALIDER)
        self._reinitialiser_saisie_produit()

    def enregistrer_reception(self) -> None:
        fournisseur = self.fournisseur.text().strip()
        if not fournisseur:
            montrer_erreur(self, ValidationError("Saisissez le nom du fournisseur."))
            return
        if not self.panier:
            montrer_erreur(self, ValidationError("Ajoutez au moins un produit à la réception."))
            return
        try:
            montant_verse = da_to_centimes(self.paiement_maintenant.text())
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        if montant_verse < 0:
            montrer_erreur(self, ValidationError("Le montant payé ne peut pas être négatif."))
            return

        modification = self._reception_modifiee is not None
        total_achat = self._total_panier()
        dette_avant = 0 if modification else self._dette_globale_fournisseur(fournisseur)
        if not modification and montant_verse > total_achat + max(dette_avant, 0):
            montrer_erreur(
                self,
                ValidationError(
                    "Le montant versé dépasse le total du bon plus la dette actuelle "
                    "du fournisseur."
                ),
            )
            return

        # Reste du bon = Total_achat - Montant_verse
        if modification:
            solde_bon = next(
                item for item in supplier_receipt_balances(self.conn)
                if item["id"] == self._reception_modifiee
            )
            reste_bon = total_achat - solde_bon["paid_centimes"]
        else:
            reste_bon = total_achat - montant_verse

        resume = "\n".join(
            f"{l['quantity']} × {l['name']} — {format_da(l['quantity'] * l['unit_cost_centimes'])}"
            for l in self.panier
        )
        if modification:
            detail = f"Dette recalculée après modification : {format_da(reste_bon)}"
        else:
            if reste_bon >= 0:
                ligne_reste = f"Reste dû sur ce bon : {format_da(reste_bon)}"
            else:
                ligne_reste = (
                    f"Excédent versé (déduit de l'ancienne dette) : {format_da(-reste_bon)}"
                )
            detail = (
                f"Payé maintenant : {format_da(montant_verse)}\n"
                f"{ligne_reste}\n"
                f"Dette globale avant : {format_da(dette_avant)}\n"
                f"Dette globale après validation : {format_da(dette_avant + reste_bon)}"
            )
        verbe = "Modifier" if modification else "Enregistrer"
        if not demander_confirmation(
            self, "Confirmer la réception",
            f"{verbe} les produits reçus de « {fournisseur} » ?\n\n{resume}\n\n"
            f"Total : {format_da(total_achat)}\n{detail}",
        ):
            return

        date_receipt = self.date_reception.date().toString("yyyy-MM-dd") + "T12:00:00+01:00"
        try:
            with transaction(self.conn):
                original = (
                    get_purchase_receipt(self.conn, self._reception_modifiee)
                    if modification else None
                )
                lignes = self._construire_lignes_reception(original)
                if not modification:
                    numero = receive_stock(
                        self.conn,
                        lignes,
                        fournisseur,
                        supplier_reference=self.reference_fournisseur.text(),
                        payment_centimes=montant_verse,
                        payment_method=self.moyen_paiement.currentData(),
                        at=date_receipt,
                        actor_user_id=self.actor_user_id,
                    )
                else:
                    numero = original["number"]
                    update_purchase_receipt(
                        self.conn, self._reception_modifiee, lignes, fournisseur,
                        supplier_reference=self.reference_fournisseur.text(),
                        at=date_receipt, actor_user_id=self.actor_user_id,
                    )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return

        self._reinitialiser_formulaire()
        self._afficher_panier()
        self.rafraichir()
        QMessageBox.information(
            self, "Réception mise à jour" if modification else "Réception enregistrée",
            f"Réception {numero} {'mise à jour' if modification else 'enregistrée'}.\n"
            "Le stock et les prix produits ont été mis à jour.\n"
            f"Total : {format_da(total_achat)} — dette globale du fournisseur : "
            f"{format_da(self._dette_globale_fournisseur(fournisseur))}.",
        )

    # ------------------------------------------------------------------
    # Historique : modifier / annuler une réception
    # ------------------------------------------------------------------
    def charger_reception_selectionnee(self) -> None:
        row = self.tableau_historique.currentRow()
        item = self.tableau_historique.item(row, 0) if row >= 0 else None
        if item is None:
            return
        receipt_id = item.data(Qt.UserRole)
        receipt = get_purchase_receipt(self.conn, receipt_id)
        if receipt is None or receipt["status"] != "validee":
            montrer_erreur(self, ValidationError("Cette réception n'est plus modifiable."))
            self.rafraichir()
            return
        if self.panier and not demander_confirmation(
            self,
            "Remplacer la saisie en cours",
            "Le panier actuel sera remplacé par les lignes de cette réception.",
        ):
            return
        self._reception_modifiee = receipt_id
        self.panier = [{
            "product_id": line["product_id"],
            "new_product": None,
            "reference": line["reference"],
            "name": line["name"],
            "quantity": line["quantity"],
            "unit_cost_centimes": line["unit_cost_centimes"],
            "unit_price_centimes": line["unit_price_centimes"],
            "imeis": line["imeis"] if line["track_serial"] else None,
        } for line in receipt["items"]]
        self.fournisseur.setText(receipt["supplier"])
        self.reference_fournisseur.setText(receipt["supplier_reference"] or "")
        self.date_reception.setDate(QDate.fromString(receipt["created_at"][:10], "yyyy-MM-dd"))
        solde_bon = next(
            b for b in supplier_receipt_balances(self.conn) if b["id"] == receipt_id
        )
        self.paiement_maintenant.setText(centimes_to_text(solde_bon["paid_centimes"]))
        self.paiement_maintenant.setEnabled(False)
        self.moyen_paiement.setEnabled(False)
        self.bouton_enregistrer.setText("Enregistrer les modifications")
        self._afficher_panier()

    def annuler_reception_selectionnee(self) -> None:
        row = self.tableau_historique.currentRow()
        item = self.tableau_historique.item(row, 0) if row >= 0 else None
        if item is None:
            return
        receipt_id = item.data(Qt.UserRole)
        receipt = get_purchase_receipt(self.conn, receipt_id)
        if receipt is None or receipt["status"] != "validee":
            montrer_erreur(self, ValidationError("Cette réception est déjà annulée."))
            self.rafraichir()
            return
        if not demander_confirmation(
            self,
            "Annuler la réception",
            f"Annuler {receipt['number']} — {receipt['supplier']} ?\n"
            "Le stock encore présent sera ajusté, les appareils déjà sortis ou vendus "
            "resteront dans leur historique. Les règlements déjà effectués resteront "
            "comptabilisés comme crédit fournisseur.",
        ):
            return
        try:
            cancel_purchase_receipt(self.conn, receipt_id, actor_user_id=self.actor_user_id)
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        QMessageBox.information(
            self, "Réception annulée",
            f"La réception {receipt['number']} a été annulée et le stock ajusté.",
        )

    # ------------------------------------------------------------------
    # Règlement d'une dette fournisseur
    # ------------------------------------------------------------------
    def definir_dette_fournisseur(self) -> None:
        """Aligne la dette globale d'un fournisseur sur sa valeur réelle (ancien solde inclus)."""
        noms = [s["name"] for s in list_suppliers(self.conn, include_archived=False)]
        ligne = self.tableau_dettes.currentRow()
        item = self.tableau_dettes.item(ligne, 0) if ligne >= 0 else None
        defaut = noms.index(item.text()) if item is not None and item.text() in noms else 0
        nom, accepte = QInputDialog.getItem(
            self, "Dette fournisseur",
            "Fournisseur (tapez un nouveau nom pour créer la fiche) :",
            noms, defaut, True,
        )
        if not accepte:
            return
        nom = nom.strip()
        if not nom:
            montrer_erreur(self, ValidationError("Saisissez le nom du fournisseur."))
            return
        fiche = next(
            (s for s in list_suppliers(self.conn) if s["name"].casefold() == nom.casefold()),
            None,
        )
        actuelle = fiche["due_centimes"] if fiche else 0
        texte, accepte = QInputDialog.getText(
            self, "Dette globale réelle",
            f"Dette actuelle dans le logiciel : {format_da(actuelle)}\n\n"
            f"Saisissez la dette globale RÉELLE envers « {nom} » aujourd'hui, "
            "AVANT le prochain bon d'achat (en DA, sans espaces) :",
            text=centimes_to_text(actuelle),
        )
        if not accepte:
            return
        try:
            cible = da_to_centimes(texte)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        if not demander_confirmation(
            self, "Confirmer la dette fournisseur",
            f"Dette de « {nom} » : {format_da(actuelle)} → {format_da(cible)} ?\n\n"
            f"L'écart de {format_da(cible - actuelle)} sera enregistré comme ancien solde.",
        ):
            return
        try:
            with transaction(self.conn):
                supplier_id = (
                    fiche["id"] if fiche
                    else create_supplier(self.conn, self.actor_user_id, nom)
                )
                set_supplier_current_debt(
                    self.conn, self.actor_user_id, supplier_id, cible
                )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        QMessageBox.information(
            self, "Dette enregistrée",
            f"Dette globale de « {nom} » : {format_da(cible)}.",
        )

    def regler_reception_selectionnee(self) -> None:
        ligne = self.tableau_dettes.currentRow()
        if ligne < 0 or self.tableau_dettes.item(ligne, 0) is None:
            montrer_erreur(self, ValidationError("Sélectionnez une dette fournisseur à régler."))
            return
        supplier_id = self.tableau_dettes.item(ligne, 0).data(Qt.UserRole)
        solde = next(
            (s for s in supplier_balances(self.conn) if s["supplier_id"] == supplier_id),
            None,
        )
        if solde is None or solde["due_centimes"] <= 0:
            montrer_erreur(self, ValidationError("Cette réception est déjà réglée."))
            self.rafraichir()
            return
        montant, accepte = QInputDialog.getText(
            self,
            "Versement fournisseur",
            f"Dette globale — {solde['supplier']} : "
            f"{format_da(solde['due_centimes'])}\nMontant versé (DA) :",
            text=centimes_to_text(solde["due_centimes"]),
        )
        if not accepte:
            return
        try:
            amount_centimes = da_to_centimes(montant)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        libelles = [libelle for libelle, _ in MOYENS_PAIEMENT]
        mode, accepte = QInputDialog.getItem(
            self, "Moyen de paiement", "Choisissez le moyen de paiement :", libelles, 0, False
        )
        if not accepte:
            return
        method = dict(MOYENS_PAIEMENT)[mode]
        try:
            record_supplier_payment_for_supplier(
                self.conn, supplier_id, amount_centimes, method,
                actor_user_id=self.actor_user_id,
            )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        QMessageBox.information(
            self,
            "Versement enregistré",
            f"Versement de {format_da(amount_centimes)} enregistré pour {solde['supplier']}.",
        )


__all__ = ["PageAchats"]
"""Écran « Produits et stock » : liste, recherche, nouveau produit, inventaire, mouvements."""

import sqlite3

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from magasin.errors import MagasinError
from magasin.import_excel import import_stock_workbook, preview_stock_workbook
from magasin.serials import clean_imei
from magasin.money import format_da
from magasin.db import transaction
from magasin.products import (archive_product, create_product, delete_product, product_usage, restore_product,
                              stock_report_page, update_product)
from magasin.stock import inventory_count
from magasin.supplier_links import (apply_supplier_links, preview_supplier_links,
                                    set_product_supplier)
from magasin.users import can, has_users

from .dialogues_imei import AppareilsDialogue, RechercheImeiDialogue
from .dialogues import InventaireDialogue, ModifierProduitDialogue, MouvementsDialogue, NouveauProduitDialogue
from .dialogues_import_excel import ApercuImportExcelDialogue
from .dialogues_rattachement_fournisseurs import ApercuRattachementFournisseursDialogue
from .outils import demander_confirmation, montrer_erreur, ouvrir_dialogue

COLONNES = ["Référence", "Nom du produit", "Prix de vente", "Prix d'achat", "Majoration", "Marge",
            "Stock", "Suivi", "Fournisseur(s)"]
ROUGE = QBrush(QColor("#b91c1c"))
ORANGE = QBrush(QColor("#c2410c"))
TAILLE_PAGE_PRODUITS = 50


class _ChargementPageSignaux(QObject):
    termine = Signal(int, object)
    erreur = Signal(int, object)


class _ChargementPageProduits(QRunnable):
    def __init__(self, chemin_base: str, numero: int, options: dict):
        super().__init__()
        self.chemin_base = chemin_base
        self.numero = numero
        self.options = options
        self.signaux = _ChargementPageSignaux()

    def run(self) -> None:
        connexion = None
        resultat = None
        erreur = None
        try:
            connexion = sqlite3.connect(self.chemin_base, timeout=5)
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            resultat = stock_report_page(connexion, **self.options)
        except Exception as exception:
            erreur = exception
        finally:
            if connexion is not None:
                connexion.close()
        if erreur is not None:
            self.signaux.erreur.emit(self.numero, erreur)
        else:
            self.signaux.termine.emit(self.numero, resultat)


class PageProduits(QWidget):
    def __init__(self, conn, current_user: dict | None = None):
        super().__init__()
        self.conn = conn
        self.current_user = current_user
        self.actor_user_id = current_user["id"] if current_user else None
        self.lignes: list[dict] = []
        self.page_index = 0
        self.page_size = TAILLE_PAGE_PRODUITS
        self.page_count = 0
        self.total_count = 0
        self._request_number = 0
        self._chargements: dict[int, _ChargementPageProduits] = {}
        self._selection_en_attente: int | None = None
        self._ouvrir_appareils_apres_chargement = False
        self._chemin_base = conn.execute("PRAGMA database_list").fetchone()["file"]
        self._temporisateur_recherche = QTimer(self)
        self._temporisateur_recherche.setSingleShot(True)
        self._temporisateur_recherche.setInterval(250)
        self._temporisateur_recherche.timeout.connect(self.rafraichir)

        titre = QLabel("Produits et stock")
        titre.setObjectName("titre")
        self.resume = QLabel("")
        self.resume.setObjectName("info")

        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Rechercher par nom, référence ou code-barres...")
        self.recherche.textChanged.connect(self._recherche_modifiee)
        self.stock_bas = QCheckBox("Stock bas ou négatif seulement")
        self.stock_bas.toggled.connect(self._recherche_modifiee)

        self.bouton_nouveau = QPushButton("Nouveau produit")
        self.bouton_nouveau.setObjectName("principal")
        self.bouton_nouveau.clicked.connect(lambda _coche=False: self.nouveau_produit())
        self.bouton_importer = QPushButton("Importer Excel")
        self.bouton_importer.clicked.connect(lambda _coche=False: self.importer_excel())
        self.bouton_associer_fournisseurs = QPushButton("Rattacher fournisseurs Excel")
        self.bouton_associer_fournisseurs.clicked.connect(
            lambda _coche=False: self.rattacher_fournisseurs_excel()
        )
        self.bouton_modifier = QPushButton("Modifier")
        self.bouton_modifier.clicked.connect(lambda _coche=False: self.modifier_produit())
        self.bouton_inventaire = QPushButton("Ajuster le stock")
        self.bouton_inventaire.clicked.connect(lambda _coche=False: self.inventaire())
        self.bouton_appareils = QPushButton("Appareils IMEI")
        self.bouton_appareils.clicked.connect(lambda _coche=False: self.appareils())
        self.bouton_mouvements = QPushButton("Mouvements")
        self.bouton_mouvements.clicked.connect(lambda _coche=False: self.mouvements())
        self.bouton_supprimer = QPushButton("Supprimer")
        self.bouton_supprimer.clicked.connect(lambda _coche=False: self.supprimer())
        self.voir_archives = QCheckBox("Voir les archivés")
        self.voir_archives.toggled.connect(self._recherche_modifiee)

        self.tableau = QTableWidget(0, len(COLONNES))
        self.tableau.setHorizontalHeaderLabels(COLONNES)
        self.tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableau.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tableau.setAlternatingRowColors(True)
        self.tableau.verticalHeader().setVisible(False)
        entete = self.tableau.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeToContents)
        entete.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tableau.itemSelectionChanged.connect(self._mettre_a_jour_boutons)
        self.tableau.doubleClicked.connect(lambda _index: self.modifier_produit())

        self.recherche_imei = QLineEdit()
        self.recherche_imei.setPlaceholderText("Chercher un IMEI...")
        self.recherche_imei.setMaximumWidth(300)
        self.recherche_imei.returnPressed.connect(self.chercher_imei)
        barre = QHBoxLayout()
        barre.addWidget(self.recherche, 1)
        barre.addWidget(self.stock_bas)
        barre.addWidget(self.recherche_imei)
        actions = QHBoxLayout()
        actions.addWidget(self.bouton_nouveau)
        actions.addWidget(self.bouton_importer)
        actions.addWidget(self.bouton_associer_fournisseurs)
        actions.addWidget(self.bouton_modifier)
        actions.addWidget(self.bouton_inventaire)
        actions.addWidget(self.bouton_appareils)
        actions.addWidget(self.bouton_mouvements)
        actions.addWidget(self.bouton_supprimer)
        actions.addStretch()
        actions.addWidget(self.voir_archives)

        self.bouton_precedent = QPushButton("Précédent")
        self.bouton_precedent.clicked.connect(self.page_precedente)
        self.indicateur_page = QLabel("Chargement…")
        self.indicateur_page.setAlignment(Qt.AlignCenter)
        self.bouton_suivant = QPushButton("Suivant")
        self.bouton_suivant.clicked.connect(self.page_suivante)
        navigation = QHBoxLayout()
        navigation.addStretch()
        navigation.addWidget(self.bouton_precedent)
        navigation.addWidget(self.indicateur_page)
        navigation.addWidget(self.bouton_suivant)
        navigation.addStretch()

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(self.resume)
        mise_en_page.addLayout(barre)
        mise_en_page.addLayout(actions)
        mise_en_page.addWidget(self.tableau, 1)
        mise_en_page.addLayout(navigation)

        self.rafraichir()

    # ------------------------------------------------------------------ données
    def rafraichir(self, *, selectionner_id: int | None = None) -> None:
        self._charger_page(selectionner_id=selectionner_id)

    def _recherche_modifiee(self, *_args) -> None:
        self.page_index = 0
        if self._chemin_base:
            self._temporisateur_recherche.start()
        else:
            self.rafraichir()

    def page_precedente(self) -> None:
        if self.page_index > 0:
            self.page_index -= 1
            self.rafraichir()

    def page_suivante(self) -> None:
        if self.page_index + 1 < self.page_count:
            self.page_index += 1
            self.rafraichir()

    def _options_chargement(self, *, selectionner_id: int | None = None) -> dict:
        return {
            "page": self.page_index,
            "page_size": self.page_size,
            "archived": self.voir_archives.isChecked(),
            "search": self.recherche.text().strip(),
            "low_stock_only": self.stock_bas.isChecked(),
            "select_product_id": selectionner_id,
        }

    def _charger_page(self, *, selectionner_id: int | None = None) -> None:
        self._request_number += 1
        if selectionner_id is not None:
            self._selection_en_attente = selectionner_id
        numero = self._request_number
        options = self._options_chargement(selectionner_id=selectionner_id)
        self.lignes = []
        self.tableau.setRowCount(0)
        self._mettre_a_jour_boutons()
        self.bouton_precedent.setEnabled(False)
        self.bouton_suivant.setEnabled(False)
        self.indicateur_page.setText("Chargement…")

        if not self._chemin_base:
            self._charger_page_terminee(
                numero, stock_report_page(self.conn, **options)
            )
            return

        chargement = _ChargementPageProduits(self._chemin_base, numero, options)
        chargement.signaux.termine.connect(self._charger_page_terminee)
        chargement.signaux.erreur.connect(self._chargement_page_echoue)
        self._chargements[numero] = chargement
        QThreadPool.globalInstance().start(chargement)

    def _charger_page_terminee(self, numero: int, resultat: dict) -> None:
        self._chargements.pop(numero, None)
        if numero != self._request_number:
            return
        self.lignes = resultat["rows"]
        self.page_index = resultat["page"]
        self.page_count = resultat["page_count"]
        self.total_count = resultat["total_count"]
        self._afficher_page(resultat["summary"])
        if self._selection_en_attente is not None:
            product_id = self._selection_en_attente
            self._selection_en_attente = None
            self._selectionner(product_id)
        if self._ouvrir_appareils_apres_chargement:
            self._ouvrir_appareils_apres_chargement = False
            self.appareils()
        self.indicateur_page.setText(
            f"Page {self.page_index + 1} / {max(self.page_count, 1)}"
            f" — {self.total_count} produit(s)"
        )
        self.bouton_precedent.setEnabled(self.page_index > 0)
        self.bouton_suivant.setEnabled(self.page_index + 1 < self.page_count)

    def _chargement_page_echoue(self, numero: int, erreur: Exception) -> None:
        self._chargements.pop(numero, None)
        if numero != self._request_number:
            return
        self.indicateur_page.setText("Erreur de chargement")
        montrer_erreur(self, erreur)

    def afficher(self) -> None:
        self._charger_page()

    def _afficher_page(self, resume: dict) -> None:
        self.tableau.setRowCount(len(self.lignes))
        for index, r in enumerate(self.lignes):
            valeurs = [r["reference"], r["name"], format_da(r["price_centimes"]), format_da(r["cost_centimes"]),
                       "—" if r["majoration_pct"] is None else f"{r['majoration_pct']} %",
                       "—" if r["marge_pct"] is None else f"{r['marge_pct']} %", str(r["stock"]),
                       "IMEI" if r["track_serial"] else "",
                       r["supplier_names"] or "—"]
            for colonne, valeur in enumerate(valeurs):
                case = QTableWidgetItem(valeur)
                case.setData(Qt.UserRole, r["id"])
                if 2 <= colonne <= 6:
                    case.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if colonne == 6:
                    if r["stock"] <= 0:
                        case.setForeground(ROUGE)
                    elif r["bas"]:
                        case.setForeground(ORANGE)
                    police = case.font()
                    police.setBold(True)
                    case.setFont(police)
                self.tableau.setItem(index, colonne, case)
        negatifs = resume["negative_count"]
        bas = resume["low_count"]
        valeur = resume["stock_value"]
        morceaux = [
            f"{resume['product_count']} produit(s)",
            f"valeur du stock au coût : {format_da(valeur)}",
        ]
        if bas:
            morceaux.append(f"{bas} en stock bas")
        if negatifs:
            morceaux.append(f"{negatifs} en stock négatif (vendus sans stock)")
        self.resume.setText("  ·  ".join(morceaux))
        self._mettre_a_jour_boutons()

    def _produit_selectionne(self):
        ligne = self.tableau.currentRow()
        if ligne < 0 or self.tableau.item(ligne, 0) is None:
            return None
        identifiant = self.tableau.item(ligne, 0).data(Qt.UserRole)
        return next((r for r in self.lignes if r["id"] == identifiant), None)

    def _mettre_a_jour_boutons(self) -> None:
        produit = self._produit_selectionne()
        actif = produit is not None
        archives = self.voir_archives.isChecked()
        peut_modifier = not has_users(self.conn) or can(self.current_user, "manage_products")
        peut_supprimer = not has_users(self.conn) or can(self.current_user, "delete_product")
        self.bouton_mouvements.setEnabled(actif)
        self.bouton_supprimer.setEnabled(
            actif and peut_modifier and (archives or peut_supprimer
                                         or not self._produit_supprimable(produit))
        )
        self.bouton_supprimer.setText(
            "Restaurer" if archives else "Supprimer" if peut_supprimer else "Archiver"
        )
        self.bouton_supprimer.setToolTip(
            "Remet ce produit dans la liste." if archives else
            "Supprime un produit créé par erreur. S'il a déjà un historique (ventes, IMEI...), "
            "il est retiré de la liste (archivé) et peut être restauré.")
        # produits archivés : consultation seulement
        for bouton in (self.bouton_modifier, self.bouton_inventaire):
            bouton.setEnabled(actif and not archives and peut_modifier)
        self.bouton_nouveau.setEnabled(not archives and peut_modifier)
        self.bouton_importer.setEnabled(not archives and peut_modifier)
        peut_gerer_achats = (
            not has_users(self.conn) or can(self.current_user, "purchases")
        )
        self.bouton_associer_fournisseurs.setEnabled(
            not archives and peut_gerer_achats
        )
        suivi = bool(actif and produit["track_serial"])
        self.bouton_appareils.setEnabled(suivi and not archives and peut_modifier)
        self.bouton_inventaire.setToolTip(
            "Produit suivi par IMEI : ouvre l'entrée des téléphones (scan ou liste collée)." if suivi
            else "Compter le stock réel : le logiciel crée le mouvement de l'écart.")

    def _produit_supprimable(self, produit: dict | None) -> bool:
        return bool(produit and product_usage(self.conn, produit["id"])["deletable"])

    # ------------------------------------------------------------------ actions
    def nouveau_produit(self) -> None:
        suggestion = str(len(self.lignes) + 101)
        dialogue = NouveauProduitDialogue(self, suggestion)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        v = dialogue.valeurs
        try:
            nouvel_id = create_product(self.conn, v["reference"], v["name"], v["price_centimes"],
                           cost_centimes=v["cost_centimes"], barcode=v["barcode"], category=v["category"],
                           brand=v["brand"], price_includes_tax=v["price_includes_tax"],
                           tax_rate_bp=v["tax_rate_bp"], min_stock=v["min_stock"], initial_stock=v["initial_stock"],
                           track_serial=v["track_serial"], actor_user_id=self.actor_user_id)
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self._ouvrir_appareils_apres_chargement = v["track_serial"]
        self.rafraichir(selectionner_id=nouvel_id)

    def importer_excel(self) -> None:
        chemin, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir le classeur Excel",
            "",
            "Classeur Excel (*.xlsx *.xlsm)",
        )
        if not chemin:
            return
        try:
            preview = preview_stock_workbook(self.conn, chemin)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        dialogue = ApercuImportExcelDialogue(self, preview)
        if ouvrir_dialogue(dialogue) != QDialog.DialogCode.Accepted:
            return
        try:
            nombre = import_stock_workbook(
                self.conn, preview, actor_user_id=self.actor_user_id
            )
        except (MagasinError, OSError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        QMessageBox.information(
            self,
            "Import terminé",
            f"{nombre} produit(s) importé(s). Le stock restant du classeur a été enregistré "
            "comme stock de départ. Les ventes historiques n'ont pas été recréées.",
        )

    def rattacher_fournisseurs_excel(self) -> None:
        chemin, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir le classeur contenant SUIVI_STOCK",
            "",
            "Classeur Excel (*.xlsx *.xlsm)",
        )
        if not chemin:
            return
        try:
            preview = preview_supplier_links(self.conn, chemin)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        dialogue = ApercuRattachementFournisseursDialogue(self, preview)
        if ouvrir_dialogue(dialogue) != QDialog.DialogCode.Accepted:
            return
        try:
            nombre = apply_supplier_links(
                self.conn, preview, actor_user_id=self.actor_user_id
            )
        except (MagasinError, OSError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()
        QMessageBox.information(
            self,
            "Rattachement terminé",
            f"{nombre} association(s) fournisseur-produit enregistrée(s). "
            "Les stocks, ventes et réceptions passées n’ont pas été modifiés.",
        )

    def _selectionner(self, product_id: int) -> None:
        for ligne in range(self.tableau.rowCount()):
            if self.tableau.item(ligne, 0).data(Qt.UserRole) == product_id:
                self.tableau.selectRow(ligne)
                return
        self._selection_en_attente = product_id

    def modifier_produit(self) -> None:
        produit = self._produit_selectionne()
        if produit is None:
            return
        dialogue = ModifierProduitDialogue(self, produit, self.conn)
        if ouvrir_dialogue(dialogue) != QDialog.Accepted or dialogue.valeurs is None:
            return
        self.appliquer_modification(produit["id"], dialogue.valeurs)

    def appliquer_modification(self, product_id: int, v: dict) -> None:
        try:
            with transaction(self.conn):
                update_product(self.conn, product_id, reference=v["reference"], name=v["name"],
                               barcode=v["barcode"], category=v["category"], brand=v["brand"],
                               cost_centimes=v["cost_centimes"], price_centimes=v["price_centimes"],
                               price_includes_tax=v["price_includes_tax"], tax_rate_bp=v["tax_rate_bp"],
                               min_stock=v["min_stock"], track_serial=v["track_serial"],
                               actor_user_id=self.actor_user_id)
                set_product_supplier(
                    self.conn, product_id, v["supplier_id"],
                    actor_user_id=self.actor_user_id,
                )
        except (MagasinError, sqlite3.Error) as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir(selectionner_id=product_id)

    def appareils(self) -> None:
        produit = self._produit_selectionne()
        if produit is None or not produit["track_serial"]:
            return
        dialogue = AppareilsDialogue(
            self, self.conn, produit, actor_user_id=self.actor_user_id
        )
        ouvrir_dialogue(dialogue)
        if dialogue.modifie:
            self.rafraichir()
            for ligne in range(self.tableau.rowCount()):
                if self.tableau.item(ligne, 0).data(Qt.UserRole) == produit["id"]:
                    self.tableau.selectRow(ligne)
                    break

    def chercher_imei(self) -> None:
        texte = self.recherche_imei.text()
        if not clean_imei(texte):
            return
        try:
            dialogue = RechercheImeiDialogue(self, self.conn, texte)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        ouvrir_dialogue(dialogue)
        self.recherche_imei.clear()

    def inventaire(self) -> None:
        produit = self._produit_selectionne()
        if produit is None:
            return
        if produit["track_serial"]:  # un téléphone ne se compte pas : on scanne ses IMEI
            self.appareils()
            return
        dialogue = InventaireDialogue(self, produit["name"], produit["stock"])
        if ouvrir_dialogue(dialogue) != QDialog.Accepted:
            return
        try:
            inventory_count(self.conn, produit["id"], dialogue.quantite.value(),
                            dialogue.note.text(), actor_user_id=self.actor_user_id)
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

    def mouvements(self) -> None:
        produit = self._produit_selectionne()
        if produit is not None:
            ouvrir_dialogue(MouvementsDialogue(self, self.conn, produit["id"], produit["name"]))

    def supprimer(self) -> None:
        """Supprime un produit sans historique ; sinon propose de l'archiver (retiré de la liste)."""
        produit = self._produit_selectionne()
        if produit is None:
            return
        if self.voir_archives.isChecked():
            self.restaurer(produit)
            return
        try:
            usage = product_usage(self.conn, produit["id"])
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        if usage["deletable"] and (
            not has_users(self.conn) or can(self.current_user, "delete_product")
        ):
            question = (f"Supprimer définitivement « {produit['name']} » ?\n\n"
                        f"Stock actuel : {produit['stock']}. Cette action est irréversible.")
            if not demander_confirmation(self, "Supprimer le produit", question):
                return
            action = lambda: delete_product(  # noqa: E731
                self.conn, produit["id"], actor_user_id=self.actor_user_id
            )
        else:
            question = (f"« {produit['name']} » a déjà un historique : {', '.join(usage['reasons'])}.\n\n"
                        "Pour garder la traçabilité, il ne peut pas être effacé pour de bon.\n"
                        "Voulez-vous le retirer de la liste (archivé) ?\n"
                        "Vous pourrez le remettre avec « Voir les archivés » puis « Restaurer ».")
            if not demander_confirmation(self, "Retirer de la liste", question):
                return
            action = lambda: archive_product(  # noqa: E731
                self.conn, produit["id"], actor_user_id=self.actor_user_id
            )
        try:
            action()
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

    def restaurer(self, produit: dict) -> None:
        try:
            restore_product(self.conn, produit["id"], actor_user_id=self.actor_user_id)
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

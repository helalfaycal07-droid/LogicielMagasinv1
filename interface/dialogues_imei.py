"""Fenêtres du suivi par IMEI : entrer des appareils, voir leur état, retrouver un IMEI."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from magasin.clock import format_dt
from magasin.errors import MagasinError
from magasin.serials import (STATUS_LABELS, add_units, clean_imei, get_unit, list_units_detailed, parse_imei_list,
                             remove_unit, search_detailed, validate_imei)

from .outils import demander_confirmation, montrer_erreur


def _tableau(colonnes: list[str]) -> QTableWidget:
    tableau = QTableWidget(0, len(colonnes))
    tableau.setHorizontalHeaderLabels(colonnes)
    tableau.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tableau.setSelectionBehavior(QAbstractItemView.SelectRows)
    tableau.setSelectionMode(QAbstractItemView.SingleSelection)
    tableau.setAlternatingRowColors(True)
    tableau.verticalHeader().setVisible(False)
    tableau.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    tableau.horizontalHeader().setStretchLastSection(True)
    return tableau


def _remplir_appareils(tableau: QTableWidget, lignes: list[dict], avec_produit: bool = False) -> None:
    tableau.setRowCount(len(lignes))
    for index, u in enumerate(lignes):
        valeurs = ([u["product_name"]] if avec_produit else []) + [
            u["imei1"], u["imei2"] or "—", STATUS_LABELS[u["status"]], u["sale_number"] or "",
            u["customer_name"] or ("Client de passage" if u["sale_number"] else "")]
        for colonne, texte in enumerate(valeurs):
            case = QTableWidgetItem(texte)
            case.setData(Qt.UserRole, u["id"])
            if STATUS_LABELS[u["status"]] == texte and u["status"] != "en_stock":
                case.setForeground(Qt.darkGray)
            tableau.setItem(index, colonne, case)


class AppareilsDialogue(QDialog):
    """Entrée des smartphones (IMEI1 + IMEI2 scannés) et liste des appareils d'un produit."""

    def __init__(self, parent, conn, produit: dict):
        super().__init__(parent)
        self.conn = conn
        self.produit = produit
        self.modifie = False
        self.en_attente: list[tuple[str, str | None]] = []
        self.setWindowTitle(f"Appareils (IMEI) — {produit['name']}")
        self.resize(820, 520)

        # ---------------- onglet 1 : entrer des appareils
        self.imei1 = QLineEdit()
        self.imei1.setPlaceholderText("Scannez ou tapez l'IMEI1 (15 chiffres), puis Entrée")
        self.imei2 = QLineEdit()
        self.imei2.setPlaceholderText("Scannez ou tapez l'IMEI2, puis Entrée")
        self.une_sim = QCheckBox("Téléphone à une seule SIM (pas d'IMEI2)")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #b91c1c;")
        bouton_ajouter = QPushButton("Ajouter à la liste")
        bouton_ajouter.clicked.connect(lambda _coche=False: self.ajouter_a_la_liste())
        self.imei1.returnPressed.connect(self._apres_imei1)
        self.imei2.returnPressed.connect(self.ajouter_a_la_liste)
        self.une_sim.toggled.connect(lambda actif: (self.imei2.setEnabled(not actif), self.imei2.clear()))

        self.tableau_attente = _tableau(["IMEI1", "IMEI2"])
        self.bouton_retirer = QPushButton("Retirer la ligne choisie")
        self.bouton_retirer.clicked.connect(lambda _coche=False: self.retirer_de_la_liste())
        self.bouton_enregistrer = QPushButton("Enregistrer 0 appareil")
        self.bouton_enregistrer.setObjectName("principal")
        self.bouton_enregistrer.clicked.connect(lambda _coche=False: self.enregistrer())
        self.bouton_enregistrer.setEnabled(False)

        saisie = QVBoxLayout()
        info = QLabel("Un appareil à la fois : IMEI1, puis IMEI2. Chaque numéro est contrôlé (15 chiffres, "
                      "chiffre de contrôle) avant d'être ajouté à la liste.")
        info.setObjectName("info")
        info.setWordWrap(True)
        saisie.addWidget(info)
        saisie.addWidget(QLabel("IMEI1"))
        saisie.addWidget(self.imei1)
        saisie.addWidget(QLabel("IMEI2"))
        saisie.addWidget(self.imei2)
        saisie.addWidget(self.une_sim)
        saisie.addWidget(bouton_ajouter)
        saisie.addWidget(self.message)
        saisie.addWidget(QLabel("Appareils prêts à être enregistrés :"))
        saisie.addWidget(self.tableau_attente, 1)
        pied = QHBoxLayout()
        pied.addWidget(self.bouton_retirer)
        pied.addStretch()
        pied.addWidget(self.bouton_enregistrer)
        saisie.addLayout(pied)
        page_entree = QWidget()
        page_entree.setLayout(saisie)

        # ---------------- onglet « Coller une liste »
        self.zone_liste = QPlainTextEdit()
        self.zone_liste.setPlaceholderText(
            "Collez ici la liste de votre fournisseur : une ligne par téléphone.\n"
            "IMEI1 puis IMEI2, séparés par un espace, une tabulation ou une virgule.\n\n"
            "356999040000013 356999040000021\n356999040000039 356999040000047")
        self.message_liste = QLabel("")
        self.message_liste.setWordWrap(True)
        self.message_liste.setStyleSheet("color: #b91c1c;")
        bouton_liste = QPushButton("Contrôler et ajouter à la liste")
        bouton_liste.setObjectName("principal")
        bouton_liste.clicked.connect(lambda _coche=False: self.ajouter_liste_collee())
        collage = QVBoxLayout()
        explication = QLabel("Pratique pour une livraison de plusieurs téléphones. Chaque numéro est contrôlé ; "
                             "si une ligne est fausse, rien n'est ajouté et la ligne en cause est indiquée.")
        explication.setObjectName("info")
        explication.setWordWrap(True)
        collage.addWidget(explication)
        collage.addWidget(self.zone_liste, 1)
        collage.addWidget(bouton_liste)
        collage.addWidget(self.message_liste)
        page_collage = QWidget()
        page_collage.setLayout(collage)

        # ---------------- onglet 2 : liste des appareils
        self.filtre = QComboBox()
        self.filtre.addItem("Tous", None)
        for code in ("en_stock", "vendu", "sorti"):
            self.filtre.addItem(STATUS_LABELS[code], code)
        self.filtre.currentIndexChanged.connect(lambda _i: self.charger_appareils())
        self.tableau = _tableau(["IMEI1", "IMEI2", "État", "Vente", "Client"])
        self.bouton_sortir = QPushButton("Sortir du stock...")
        self.bouton_sortir.clicked.connect(lambda _coche=False: self.sortir_du_stock())
        self.resume = QLabel("")
        self.resume.setObjectName("info")
        liste = QVBoxLayout()
        haut = QHBoxLayout()
        haut.addWidget(QLabel("Afficher :"))
        haut.addWidget(self.filtre)
        haut.addStretch()
        haut.addWidget(self.bouton_sortir)
        liste.addLayout(haut)
        liste.addWidget(self.resume)
        liste.addWidget(self.tableau, 1)
        page_liste = QWidget()
        page_liste.setLayout(liste)

        self.onglets = QTabWidget()
        self.onglets.addTab(page_entree, "Entrer des appareils")
        self.onglets.addTab(page_collage, "Coller une liste")
        self.onglets.addTab(page_liste, "Appareils en stock et vendus")
        fermer = QPushButton("Fermer")
        fermer.clicked.connect(self.accept)
        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(self.onglets)
        bas = QHBoxLayout()
        bas.addStretch()
        bas.addWidget(fermer)
        mise_en_page.addLayout(bas)
        self.charger_appareils()
        self.imei1.setFocus()

    # ------------------------------------------------------------------ saisie
    def _apres_imei1(self) -> None:
        if self.une_sim.isChecked():
            self.ajouter_a_la_liste()
        else:
            self.imei2.setFocus()

    def ajouter_a_la_liste(self) -> bool:
        """Contrôle les deux IMEI puis ajoute l'appareil à la liste. Retourne True si ajouté."""
        self.message.setText("")
        try:
            imei1 = validate_imei(self.imei1.text(), "IMEI1")
            imei2 = None if self.une_sim.isChecked() else validate_imei(self.imei2.text(), "IMEI2")
            if imei2 == imei1:
                raise MagasinError("L'IMEI2 ne peut pas être identique à l'IMEI1.")
            self._controler_unicite(imei1, imei2)
        except MagasinError as erreur:
            self.message.setText(str(erreur))
            self.imei1.setFocus() if "IMEI1" in str(erreur) else self.imei2.setFocus()
            return False
        self.en_attente.append((imei1, imei2))
        self.imei1.clear()
        self.imei2.clear()
        self.imei1.setFocus()
        self._afficher_attente()
        return True

    def _controler_unicite(self, imei1: str, imei2: str | None, deja_vus: set | None = None) -> None:
        """Refuse un IMEI déjà dans la liste en attente, dans cette saisie, ou déjà enregistré."""
        for numero in filter(None, (imei1, imei2)):
            if any(numero in paire for paire in self.en_attente) or (deja_vus is not None and numero in deja_vus):
                raise MagasinError(f"L'IMEI {numero} est déjà dans la liste.")
            existant = get_unit(self.conn, numero)
            if existant:
                raise MagasinError(f"L'IMEI {numero} existe déjà dans le logiciel "
                                   f"(état : {STATUS_LABELS[existant['status']].lower()}).")

    def ajouter_liste_collee(self) -> bool:
        """Lit la liste collée ; tout est ajouté à la liste en attente, ou rien."""
        self.message_liste.setText("")
        try:
            lues = parse_imei_list(self.zone_liste.toPlainText())
            vus: set[str] = set()
            for position, (imei1, imei2) in enumerate(lues, start=1):
                if imei2 == imei1:
                    raise MagasinError(f"Ligne {position} : l'IMEI2 ne peut pas être identique à l'IMEI1.")
                try:
                    self._controler_unicite(imei1, imei2, vus)
                except MagasinError as erreur:
                    raise MagasinError(f"Ligne {position} : {erreur}") from erreur
                vus.update(filter(None, (imei1, imei2)))
        except MagasinError as erreur:
            self.message_liste.setText(str(erreur))
            return False
        self.en_attente.extend(lues)
        self.zone_liste.clear()
        self._afficher_attente()
        self.message.setStyleSheet("color: #15803d;")
        self.message.setText(f"{len(lues)} appareil(s) ajouté(s) à la liste : vérifiez puis enregistrez.")
        self.onglets.setCurrentIndex(0)
        return True

    def _afficher_attente(self) -> None:
        self.tableau_attente.setRowCount(len(self.en_attente))
        for ligne, (a, b) in enumerate(self.en_attente):
            self.tableau_attente.setItem(ligne, 0, QTableWidgetItem(a))
            self.tableau_attente.setItem(ligne, 1, QTableWidgetItem(b or "—"))
        n = len(self.en_attente)
        self.bouton_enregistrer.setText(f"Enregistrer {n} appareil{'s' if n > 1 else ''}")
        self.bouton_enregistrer.setEnabled(n > 0)

    def retirer_de_la_liste(self) -> None:
        ligne = self.tableau_attente.currentRow()
        if 0 <= ligne < len(self.en_attente):
            del self.en_attente[ligne]
            self._afficher_attente()

    def enregistrer(self) -> bool:
        if not self.en_attente:
            return False
        try:
            add_units(self.conn, self.produit["id"], list(self.en_attente))
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return False
        self.modifie = True
        self.en_attente.clear()
        self._afficher_attente()
        self.message.setStyleSheet("color: #15803d;")
        self.message.setText("Appareils enregistrés. Le stock est mis à jour.")
        self.charger_appareils()
        return True

    # ------------------------------------------------------------------- liste
    def charger_appareils(self) -> None:
        lignes = list_units_detailed(self.conn, self.produit["id"], self.filtre.currentData())
        _remplir_appareils(self.tableau, lignes)
        tous = list_units_detailed(self.conn, self.produit["id"])
        en_stock = sum(1 for u in tous if u["status"] == "en_stock")
        self.resume.setText(f"{en_stock} en stock · {sum(1 for u in tous if u['status'] == 'vendu')} vendu(s) · "
                            f"{len(tous)} appareil(s) enregistré(s) au total")

    def sortir_du_stock(self) -> None:
        ligne = self.tableau.currentRow()
        if ligne < 0 or self.tableau.item(ligne, 0) is None:
            return
        serial_id = self.tableau.item(ligne, 0).data(Qt.UserRole)
        imei = self.tableau.item(ligne, 0).text()
        boite = QInputDialog(self)
        boite.setWindowTitle("Sortir du stock")
        boite.setLabelText(f"Pourquoi sortir l'appareil {imei} du stock ?\n(perdu, défectueux, erreur de saisie...)")
        boite.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if boite.exec() != QDialog.Accepted:
            return
        try:
            remove_unit(self.conn, serial_id, boite.textValue())
        except Exception as erreur:
            montrer_erreur(self, erreur)
            return
        self.modifie = True
        self.charger_appareils()


class RechercheImeiDialogue(QDialog):
    """Retrouve un appareil par son IMEI1 ou son IMEI2 : produit, état, vente, client."""

    def __init__(self, parent, conn, texte: str):
        super().__init__(parent)
        self.setWindowTitle(f"Recherche IMEI — {clean_imei(texte)}")
        self.resize(880, 300)
        self.lignes = search_detailed(conn, texte)
        titre = QLabel(f"{len(self.lignes)} appareil(s) trouvé(s)" if self.lignes
                       else "Aucun appareil avec cet IMEI dans le logiciel.")
        titre.setObjectName("info")
        self.tableau = _tableau(["Produit", "IMEI1", "IMEI2", "État", "Vente", "Client"])
        _remplir_appareils(self.tableau, self.lignes, avec_produit=True)
        fermer = QPushButton("Fermer")
        fermer.clicked.connect(self.accept)
        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(self.tableau, 1)
        bas = QHBoxLayout()
        bas.addStretch()
        bas.addWidget(fermer)
        mise_en_page.addLayout(bas)

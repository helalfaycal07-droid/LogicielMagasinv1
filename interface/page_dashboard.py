"""Tableau de bord : indicateurs, évolution des ventes et produits les plus vendus."""

from datetime import date, timedelta

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QPushButton, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QScrollArea
)

from magasin.clock import now
from magasin.customers import customer_debt
from magasin.reports import report_data, profit_report_data
from magasin.cash import current_session, session_summary
from magasin.credit import list_installments


def format_da(centimes: int) -> str:
    montant = f"{centimes / 100:,.2f}"
    return montant.replace(",", " ").replace(".", ",") + " DA"


def format_court(centimes: int) -> str:
    montant = centimes / 100
    if montant >= 1_000_000:
        return f"{montant / 1_000_000:.1f} M"
    if montant >= 1_000:
        return f"{montant / 1_000:.0f} K"
    return f"{montant:.0f}"


class Graphique(QWidget):
    """Petit graphique dessiné directement avec QPainter."""

    def __init__(self, titre, mode="ligne", parent=None):
        super().__init__(parent)
        self.titre = titre
        self.mode = mode
        self.labels = []
        self.series = []
        self.message = "Aucune donnée disponible"
        self.setMinimumHeight(270)

    def definir_donnees(self, labels, series):
        self.labels = labels
        self.series = series
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        painter.setPen(QColor("#1E293B"))
        painter.setFont(self.font())
        painter.drawText(18, 27, self.titre)

        if not self.labels or not self.series:
            painter.setPen(QColor("#64748B"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self.message
            )
            painter.end()
            return

        valeurs = [
            valeur
            for serie in self.series
            for valeur in serie["values"]
            if valeur is not None
        ]

        maximum = max(valeurs, default=0)
        if maximum <= 0:
            maximum = 1

        gauche = 65
        droite = 18
        haut = 55
        bas = 45

        largeur = max(1, self.width() - gauche - droite)
        hauteur = max(1, self.height() - haut - bas)

        # Lignes horizontales et valeurs de l'axe vertical.
        painter.setFont(self.font())
        for i in range(5):
            y = haut + hauteur * i / 4
            valeur = maximum * (4 - i) / 4

            painter.setPen(QPen(QColor("#E2E8F0"), 1))
            painter.drawLine(
                gauche, int(y),
                gauche + largeur, int(y)
            )

            painter.setPen(QColor("#64748B"))
            painter.drawText(
                QRectF(0, y - 9, gauche - 8, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_court(int(valeur))
            )

        couleurs = [
            QColor("#3B82F6"),
            QColor("#10B981"),
            QColor("#F59E0B"),
        ]

        if self.mode == "ligne":
            serie = self.series[0]
            points = []
            nombre = len(self.labels)

            for index, valeur in enumerate(serie["values"]):
                if valeur is None:
                    points.append(None)
                    continue

                x = gauche + (
                    largeur * index / max(1, nombre - 1)
                )
                y = haut + hauteur * (1 - valeur / maximum)
                points.append((x, y))

            chemin = QPainterPath()
            premier = True

            for point in points:
                if point is None:
                    premier = True
                    continue

                x, y = point
                if premier:
                    chemin.moveTo(x, y)
                    premier = False
                else:
                    chemin.lineTo(x, y)

            painter.setPen(QPen(couleurs[0], 2.5))
            painter.drawPath(chemin)

            for point in points:
                if point is None:
                    continue
                x, y = point
                painter.setBrush(couleurs[0])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))

        else:
            nombre = len(self.labels)
            largeur_groupe = largeur / max(1, nombre)
            nombre_series = len(self.series)
            largeur_barre = min(
                24,
                largeur_groupe * 0.65 / max(1, nombre_series)
            )

            for index, serie in enumerate(self.series):
                couleur = couleurs[index % len(couleurs)]
                painter.setBrush(couleur)
                painter.setPen(Qt.PenStyle.NoPen)

                for position, valeur in enumerate(serie["values"]):
                    if valeur is None:
                        continue

                    hauteur_barre = hauteur * valeur / maximum
                    centre = gauche + largeur_groupe * (
                        position + 0.5
                    )
                    decalage = (
                        index - (nombre_series - 1) / 2
                    ) * largeur_barre

                    rectangle = QRectF(
                        centre + decalage - largeur_barre / 2,
                        haut + hauteur - hauteur_barre,
                        largeur_barre,
                        hauteur_barre
                    )
                    painter.drawRoundedRect(rectangle, 3, 3)

        # Étiquettes de l'axe horizontal.
        painter.setPen(QColor("#64748B"))
        nombre = len(self.labels)
        pas = max(1, nombre // 7)

        for index, label in enumerate(self.labels):
            if index % pas != 0 and index != nombre - 1:
                continue

            if self.mode == "ligne":
                x = gauche + largeur * index / max(1, nombre - 1)
            else:
                x = gauche + largeur * (
                    index + 0.5
                ) / max(1, nombre)

            painter.drawText(
                QRectF(x - 30, haut + hauteur + 8, 60, 20),
                Qt.AlignmentFlag.AlignCenter,
                str(label)
            )

        # Légende.
        position_x = gauche
        position_y = self.height() - 8

        for index, serie in enumerate(self.series):
            couleur = couleurs[index % len(couleurs)]
            painter.setBrush(couleur)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(position_x, position_y - 9, 9, 9)
            )

            painter.setPen(QColor("#475569"))
            painter.drawText(
                position_x + 14,
                position_y,
                serie["name"]
            )
            position_x += 105

        painter.end()


class PageDashboard(QWidget):

    def __init__(self, conn, navigate=None):
        super().__init__()
        self.conn = conn
        self.navigate = navigate
        self.valeurs = {}

        # Zone défilante pour les écrans de petite taille.
        principal_page = QVBoxLayout(self)
        principal_page.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        principal_page.addWidget(scroll)

        contenu = QWidget()
        scroll.setWidget(contenu)

        principal = QVBoxLayout(contenu)
        principal.setContentsMargins(25, 20, 25, 25)
        principal.setSpacing(16)

        # En-tête.
        entete = QHBoxLayout()

        bloc_titre = QVBoxLayout()

        titre = QLabel("Tableau de bord")
        titre.setStyleSheet(
            "font-size: 25px; font-weight: bold; color: #1E293B;"
        )
        bloc_titre.addWidget(titre)

        self.sous_titre = QLabel("Vue générale de votre magasin")
        self.sous_titre.setStyleSheet(
            "font-size: 13px; color: #64748B;"
        )
        bloc_titre.addWidget(self.sous_titre)

        entete.addLayout(bloc_titre)
        entete.addStretch()

        self.periode = QComboBox()
        self.periode.addItem("7 derniers jours", "7")
        self.periode.addItem("30 derniers jours", "30")
        self.periode.addItem("Ce mois", "mois")
        self.periode.addItem("Cette année", "annee")
        self.periode.setMinimumWidth(170)
        self.periode.setStyleSheet("""
            QComboBox {
                padding: 8px;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                background: white;
            }
        """)
        self.periode.currentIndexChanged.connect(self.rafraichir)
        entete.addWidget(self.periode)

        principal.addLayout(entete)

        # Six indicateurs existants.
        grille = QGridLayout()
        grille.setSpacing(15)

        indicateurs = [
            ("ca", "Chiffre d'affaires net du jour",
             "#3B82F6", "#F8FAFC"),
            ("benefice", "Bénéfice brut du mois",
             "#10B981", "#F8FAFC"),
            ("stock", "Valeur actuelle du stock",
             "#F59E0B", "#F8FAFC"),
            ("clients", "Dettes des clients",
             "#9C27B0", "#F8FAFC"),
            ("bas", "Produits en stock bas",
             "#FF9800", "#FFFBEB"),
            ("negatif", "Produits en stock négatif",
             "#F44336", "#FEF2F2"),
        ]

        for index, (cle, libelle, couleur, fond) in enumerate(indicateurs):
            carte = QFrame()
            carte.setStyleSheet(f"""
                QFrame {{
                    background-color: {fond};
                    border: 1px solid #E2E8F0;
                    border-left: 6px solid {couleur};
                    border-radius: 10px;
                }}
            """)

            disposition = QVBoxLayout(carte)
            disposition.setContentsMargins(18, 14, 18, 14)

            nom = QLabel(libelle)
            nom.setStyleSheet("""
                font-size: 13px;
                font-weight: bold;
                color: #64748B;
                border: none;
            """)

            valeur = QLabel("...")
            valeur.setStyleSheet("""
                font-size: 21px;
                font-weight: bold;
                color: #1E293B;
                border: none;
            """)
            valeur.setWordWrap(True)

            disposition.addWidget(nom)
            disposition.addWidget(valeur)

            self.valeurs[cle] = valeur
            grille.addWidget(carte, index // 2, index % 2)

        principal.addLayout(grille)

        # Graphiques.
        titre_graphiques = QLabel("Analyse des ventes")
        titre_graphiques.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E293B;"
        )
        principal.addWidget(titre_graphiques)

        ligne_graphiques = QHBoxLayout()
        ligne_graphiques.setSpacing(15)

        carte_courbe = self._creer_carte()
        layout_courbe = QVBoxLayout(carte_courbe)
        layout_courbe.setContentsMargins(10, 10, 10, 10)

        self.graphique_ventes = Graphique(
            "Évolution du chiffre d'affaires net",
            "ligne"
        )
        layout_courbe.addWidget(self.graphique_ventes)

        carte_mensuelle = self._creer_carte()
        layout_mensuelle = QVBoxLayout(carte_mensuelle)
        layout_mensuelle.setContentsMargins(10, 10, 10, 10)

        self.graphique_mensuel = Graphique(
            "Chiffre d'affaires et bénéfice brut",
            "barres"
        )
        layout_mensuelle.addWidget(self.graphique_mensuel)

        ligne_graphiques.addWidget(carte_courbe)
        ligne_graphiques.addWidget(carte_mensuelle)

        principal.addLayout(ligne_graphiques)

        # Tableau des cinq meilleurs produits.
        titre_produits = QLabel("Top 5 des produits vendus")
        titre_produits.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E293B;"
        )
        principal.addWidget(titre_produits)

        self.table_produits = QTableWidget(0, 4)
        self.table_produits.setHorizontalHeaderLabels([
            "Référence", "Désignation", "Quantité nette", "CA net"
        ])
        self.table_produits.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table_produits.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table_produits.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table_produits.setAlternatingRowColors(True)
        self.table_produits.setMinimumHeight(210)
        principal.addWidget(self.table_produits)

        # Comparaison avec le mois précédent.
        titre_comparaison = QLabel("Comparaison avec le mois précédent")
        titre_comparaison.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E293B;"
        )
        principal.addWidget(titre_comparaison)
        self.comparaison = QLabel("Calcul en cours...")
        self.comparaison.setWordWrap(True)
        self.comparaison.setStyleSheet(
            "background: #EFF6FF; color: #1E40AF; padding: 14px;"
            "border: 1px solid #BFDBFE; border-radius: 8px; font-size: 14px;"
        )
        principal.addWidget(self.comparaison)

        # Échéances clients et situation de la caisse.
        ligne_suivi = QHBoxLayout()
        ligne_suivi.setSpacing(15)
        carte_echeances = self._creer_carte()
        layout_echeances = QVBoxLayout(carte_echeances)
        layout_echeances.setContentsMargins(12, 12, 12, 12)
        titre_echeances = QLabel("Échéances clients")
        titre_echeances.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #1E293B;"
        )
        layout_echeances.addWidget(titre_echeances)
        self.resume_echeances = QLabel("Chargement...")
        self.resume_echeances.setStyleSheet("color: #64748B;")
        layout_echeances.addWidget(self.resume_echeances)
        self.table_echeances = self._creer_table(
            ["Client", "Échéance", "Date", "Reste à payer", "État"]
        )
        self.table_echeances.setMinimumHeight(190)
        layout_echeances.addWidget(self.table_echeances)

        carte_caisse = self._creer_carte()
        layout_caisse = QVBoxLayout(carte_caisse)
        layout_caisse.setContentsMargins(12, 12, 12, 12)
        titre_caisse = QLabel("Situation de la caisse")
        titre_caisse.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #1E293B;"
        )
        layout_caisse.addWidget(titre_caisse)
        self.resume_caisse = QLabel("Chargement...")
        self.resume_caisse.setWordWrap(True)
        self.resume_caisse.setStyleSheet(
            "font-size: 14px; color: #334155; padding: 8px;"
        )
        layout_caisse.addWidget(self.resume_caisse)
        layout_caisse.addStretch()
        ligne_suivi.addWidget(carte_echeances, 3)
        ligne_suivi.addWidget(carte_caisse, 2)
        principal.addLayout(ligne_suivi)

        # Alertes de stock et dernières ventes.
        ligne_alertes = QHBoxLayout()
        ligne_alertes.setSpacing(15)
        carte_alertes = self._creer_carte()
        layout_alertes = QVBoxLayout(carte_alertes)
        layout_alertes.setContentsMargins(12, 12, 12, 12)
        titre_alertes = QLabel("Alertes de stock")
        titre_alertes.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #1E293B;"
        )
        layout_alertes.addWidget(titre_alertes)
        self.resume_stock = QLabel("Chargement...")
        self.resume_stock.setWordWrap(True)
        layout_alertes.addWidget(self.resume_stock)
        self.table_alertes_stock = self._creer_table(
            ["Référence", "Désignation", "Stock"]
        )
        self.table_alertes_stock.setMinimumHeight(180)
        layout_alertes.addWidget(self.table_alertes_stock)

        carte_ventes = self._creer_carte()
        layout_ventes = QVBoxLayout(carte_ventes)
        layout_ventes.setContentsMargins(12, 12, 12, 12)
        titre_ventes = QLabel("Dernières ventes")
        titre_ventes.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #1E293B;"
        )
        layout_ventes.addWidget(titre_ventes)
        self.table_dernieres_ventes = self._creer_table(
            ["N° facture", "Client", "Date", "Montant"]
        )
        self.table_dernieres_ventes.setMinimumHeight(210)
        layout_ventes.addWidget(self.table_dernieres_ventes)
        ligne_alertes.addWidget(carte_alertes, 1)
        ligne_alertes.addWidget(carte_ventes, 1)
        principal.addLayout(ligne_alertes)

        # Raccourcis vers les pages existantes.
        titre_raccourcis = QLabel("Accès rapides")
        titre_raccourcis.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E293B;"
        )
        principal.addWidget(titre_raccourcis)
        ligne_raccourcis = QHBoxLayout()
        ligne_raccourcis.setSpacing(10)
        for libelle, page in [
            ("Nouvelle vente", "Vente rapide"),
            ("Clients et crédit", "Clients et crédit"),
            ("Produits et stock", "Produits et stock"),
            ("Caisse", "Caisse"),
            ("Rapports", "Rapports"),
        ]:
            raccourci = QPushButton(libelle)
            raccourci.setCursor(Qt.CursorShape.PointingHandCursor)
            raccourci.setStyleSheet(
                "QPushButton { background: #E2E8F0; color: #1E293B;"
                "border: 0; border-radius: 6px; padding: 10px 13px;"
                "font-weight: bold; }"
                "QPushButton:hover { background: #CBD5E1; }"
            )
            raccourci.clicked.connect(
                lambda checked=False, destination=page: self._ouvrir_page(destination)
            )
            ligne_raccourcis.addWidget(raccourci)
        principal.addLayout(ligne_raccourcis)

        # Actualisation.
        bouton = QPushButton("Actualiser le tableau de bord")
        bouton.setCursor(Qt.CursorShape.PointingHandCursor)
        bouton.setStyleSheet("""
            QPushButton {
                background-color: #0F172A;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 11px 18px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1E293B;
            }
        """)
        bouton.clicked.connect(self.rafraichir)

        principal.addWidget(
            bouton,
            alignment=Qt.AlignmentFlag.AlignRight
        )

        self.rafraichir()

    @staticmethod
    def _creer_table(entetes):
        table = QTableWidget(0, len(entetes))
        table.setHorizontalHeaderLabels(entetes)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _remplir_table(table, lignes):
        table.setRowCount(len(lignes))
        for no_ligne, valeurs in enumerate(lignes):
            for no_colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(str(valeur))
                if no_colonne > 0:
                    cellule.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(no_ligne, no_colonne, cellule)
        table.resizeRowsToContents()

    def _ouvrir_page(self, libelle):
        if self.navigate is not None:
            self.navigate(libelle)

    def _charger_echeances(self, aujourd_hui):
        echeances = list_installments(self.conn, today=aujourd_hui)
        noms_clients = {
            row["id"]: row["name"]
            for row in self.conn.execute("SELECT id, name FROM customers").fetchall()
        }
        lignes = []
        retards = 0
        a_venir = 0
        partielles = 0
        for echeance in echeances:
            ligne = dict(echeance)
            statut = ligne.get("status", "")
            restant = int(ligne.get("reste_centimes", 0) or 0)
            if restant <= 0:
                continue
            if statut == "en_retard":
                retards += 1
                libelle_statut = "En retard"
            elif statut == "partielle":
                partielles += 1
                libelle_statut = "Partielle"
            else:
                a_venir += 1
                libelle_statut = "À venir"
            nom_client = noms_clients.get(ligne.get("customer_id"), "Client")
            numero = f"N° {ligne.get('number', '')}"
            date_echeance = ligne.get("due_date", "")
            lignes.append((
                str(nom_client), numero, str(date_echeance),
                format_da(restant), libelle_statut, str(date_echeance)
            ))
        lignes.sort(key=lambda x: x[5])
        self._remplir_table(self.table_echeances, [ligne[:5] for ligne in lignes[:8]])
        self.resume_echeances.setText(
            f"{retards} en retard · {partielles} partielles · {a_venir} à venir"
        )

    def _charger_caisse(self):
        session = current_session(self.conn)
        if not session:
            self.resume_caisse.setText(
                "Aucune session de caisse ouverte.\n"
                "Ouvrez la caisse pour enregistrer les opérations en espèces."
            )
            return
        session = dict(session)
        resume = dict(session_summary(self.conn, session["id"]))
        ouverture = resume.get("opening_centimes", resume.get("opening", 0))
        attendu = resume.get("expected_centimes", resume.get("expected", 0))
        self.resume_caisse.setText(
            f"Session ouverte : {session.get('opened_at', '')}\n"
            f"Fond d'ouverture : {format_da(int(ouverture or 0))}\n"
            f"Solde théorique : {format_da(int(attendu or 0))}"
        )

    def _charger_alertes_stock(self, donnees):
        inventaire = donnees.get("inventory", [])
        alertes = []
        for produit in inventaire:
            ligne = dict(produit)
            stock = ligne.get("stock", 0)
            if stock is not None and float(stock) < 0:
                alertes.append(ligne)
        alertes.sort(key=lambda p: float(p.get("stock", 0)))
        self._remplir_table(
            self.table_alertes_stock,
            [
                (produit.get("reference") or "", produit.get("name") or "",
                 str(produit.get("stock", "")))
                for produit in alertes[:8]
            ]
        )
        self.resume_stock.setText(
            f"{donnees.get('low_stock_count', 0)} produit(s) en stock bas · "
            f"{donnees.get('negative_stock_count', 0)} en stock négatif"
        )

    def _charger_dernieres_ventes(self):
        lignes = self.conn.execute(
            """
            SELECT s.number, s.total_centimes, s.created_at,
                   COALESCE(c.name, 'Client comptoir') AS customer_name
            FROM sales s
            LEFT JOIN customers c ON c.id = s.customer_id
            WHERE s.status IN ('validee', 'retournee')
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT 8
            """
        ).fetchall()
        self._remplir_table(
            self.table_dernieres_ventes,
            [
                (row["number"], row["customer_name"],
                 str(row["created_at"])[:16].replace("T", " "),
                 format_da(row["total_centimes"]))
                for row in lignes
            ]
        )

    def _charger_comparaison_mensuelle(self, aujourd_hui):
        debut_actuel = aujourd_hui.replace(day=1)
        fin_mois_precedent = debut_actuel - timedelta(days=1)
        jour_compare = min(aujourd_hui.day, fin_mois_precedent.day)
        debut_precedent = fin_mois_precedent.replace(day=1)
        fin_precedent = fin_mois_precedent.replace(day=jour_compare)
        actuel = report_data(
            self.conn, debut_actuel.isoformat(), aujourd_hui.isoformat()
        )
        precedent = report_data(
            self.conn, debut_precedent.isoformat(), fin_precedent.isoformat()
        )
        ca_actuel = actuel["net_sales_total_centimes"]
        ca_precedent = precedent["net_sales_total_centimes"]
        if ca_precedent > 0:
            evolution = (ca_actuel - ca_precedent) * 100 / ca_precedent
            evolution_texte = f"{evolution:+.1f} %"
        elif ca_actuel > 0:
            evolution_texte = "Nouvelle activité (base précédente nulle)"
        else:
            evolution_texte = "0,0 %"
        self.comparaison.setText(
            f"CA net du 1 au {aujourd_hui.day:02d} : {format_da(ca_actuel)}"
            f"   |   Même période précédente : {format_da(ca_precedent)}"
            f"   |   Évolution : {evolution_texte}"
        )

    @staticmethod
    def _creer_carte():
        carte = QFrame()
        carte.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        return carte

    def _periode_selectionnee(self):
        aujourd_hui = now().date()
        choix = self.periode.currentData()

        if choix == "7":
            debut = aujourd_hui - timedelta(days=6)
        elif choix == "30":
            debut = aujourd_hui - timedelta(days=29)
        elif choix == "annee":
            debut = aujourd_hui.replace(month=1, day=1)
        else:
            debut = aujourd_hui.replace(day=1)

        return debut, aujourd_hui

    def _charger_courbe_ventes(self, debut, fin):
        """Calcule les ventes nettes de chaque jour."""

        ventes = self.conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS jour,
                   SUM(total_centimes) AS total
            FROM sales
            WHERE status IN ('validee', 'retournee')
              AND substr(created_at, 1, 10) BETWEEN ? AND ?
            GROUP BY substr(created_at, 1, 10)
            """,
            (debut.isoformat(), fin.isoformat())
        ).fetchall()

        retours = self.conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS jour,
                   SUM(total_centimes) AS total
            FROM sale_returns
            WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
            GROUP BY substr(created_at, 1, 10)
            """,
            (debut.isoformat(), fin.isoformat())
        ).fetchall()

        ventes_par_jour = {
            row["jour"]: row["total"] for row in ventes
        }
        retours_par_jour = {
            row["jour"]: row["total"] for row in retours
        }

        labels = []
        valeurs = []

        jour = debut
        while jour <= fin:
            cle = jour.isoformat()

            total = (
                ventes_par_jour.get(cle, 0)
                - retours_par_jour.get(cle, 0)
            )

            labels.append(jour.strftime("%d/%m"))
            valeurs.append(total)

            jour += timedelta(days=1)

        self.graphique_ventes.definir_donnees(
            labels,
            [{"name": "CA net", "values": valeurs}]
        )

    def _charger_graphique_mensuel(self, debut, fin):
        donnees = profit_report_data(
            self.conn,
            debut.isoformat(),
            fin.isoformat(),
            "month"
        )

        labels = []
        chiffre_affaires = []
        benefices = []

        for ligne in donnees["periods"]:
            labels.append(ligne["period"])
            chiffre_affaires.append(
                ligne["net_revenue_centimes"]
            )
            benefices.append(
                ligne["profit_centimes"]
                if ligne["complete"] else None
            )

        self.graphique_mensuel.definir_donnees(
            labels,
            [
                {"name": "CA net", "values": chiffre_affaires},
                {"name": "Bénéfice brut", "values": benefices},
            ]
        )

        if not donnees["profit_complete"]:
            self.graphique_mensuel.message = (
                "Bénéfice indisponible : coûts historiques manquants"
            )

    def _charger_top_produits(self, donnees):
        produits = donnees["products_sold"][:5]

        self.table_produits.setRowCount(len(produits))

        for ligne, produit in enumerate(produits):
            elements = [
                produit["reference"] or "",
                produit["name"],
                str(produit["quantity"]),
                format_da(produit["total_centimes"]),
            ]

            for colonne, texte in enumerate(elements):
                cellule = QTableWidgetItem(texte)

                if colonne in (2, 3):
                    cellule.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )

                self.table_produits.setItem(
                    ligne, colonne, cellule
                )

        self.table_produits.resizeColumnsToContents()

    def rafraichir(self):
        aujourd_hui = now().date()
        debut_mois = aujourd_hui.replace(day=1)

        jour = report_data(
            self.conn,
            aujourd_hui.isoformat(),
            aujourd_hui.isoformat(),
        )

        benefice = profit_report_data(
            self.conn,
            debut_mois.isoformat(),
            aujourd_hui.isoformat(),
        )

        self.valeurs["ca"].setText(
            format_da(jour["net_sales_total_centimes"])
        )

        if benefice["profit_complete"]:
            texte_benefice = format_da(
                benefice["profit_centimes"]
            )
        else:
            texte_benefice = "Données incomplètes"

        self.valeurs["benefice"].setText(texte_benefice)

        self.valeurs["stock"].setText(
            format_da(jour["inventory_value_centimes"])
        )

        clients = self.conn.execute(
            "SELECT id FROM customers"
        ).fetchall()

        dette_totale = sum(
            max(0, customer_debt(self.conn, client["id"]))
            for client in clients
        )

        self.valeurs["clients"].setText(
            format_da(dette_totale)
        )
        self.valeurs["bas"].setText(
            str(jour["low_stock_count"])
        )
        self.valeurs["negatif"].setText(
            str(jour["negative_stock_count"])
        )

        debut, fin = self._periode_selectionnee()

        donnees_periode = report_data(
            self.conn,
            debut.isoformat(),
            fin.isoformat()
        )

        self._charger_courbe_ventes(debut, fin)
        self._charger_graphique_mensuel(debut, fin)
        self._charger_top_produits(donnees_periode)

        self._charger_echeances(aujourd_hui)
        self._charger_caisse()
        self._charger_alertes_stock(donnees_periode)
        self._charger_dernieres_ventes()
        self._charger_comparaison_mensuelle(aujourd_hui)

        self.sous_titre.setText(
            f"Situation au {aujourd_hui.strftime('%d/%m/%Y')} "
            "— bénéfice brut du mois en cours"
        )
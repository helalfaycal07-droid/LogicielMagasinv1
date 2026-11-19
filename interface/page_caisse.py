"""Écran de gestion de la caisse : ouverture, dépenses, résumé et clôture."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFormLayout, QFrame, QGraphicsDropShadowEffect, QGroupBox, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

from magasin.cash import (close_session, current_session, open_session, record_expense,
                          session_summary, suggested_opening)
from magasin.errors import MagasinError, ValidationError
from magasin.money import centimes_to_text, da_to_centimes, format_da
from magasin.users import can, has_users

from .outils import demander_confirmation, montrer_erreur

LIBELLES_MOUVEMENT = {
    "acompte": "Acomptes de ventes",
    "depense": "Dépenses",
    "retour_client": "Retours clients",
    "vente_especes": "Ventes en espèces",
    "sortie": "Remboursements et sorties",
}


class PageCaisse(QWidget):
    """Interface de caisse, sans dupliquer les règles métier de magasin.cash."""

    def __init__(self, conn, current_user: dict | None = None):
        super().__init__()
        self.conn = conn
        self.current_user = current_user
        self.actor_user_id = current_user["id"] if current_user else None
        self.session_id = None

        titre = QLabel("Caisse")
        titre.setObjectName("titre")

        self.resume = QLabel("")
        self.resume.setObjectName("info")
        self.resume.setWordWrap(True)

        self.libelle_solde = QLabel("SOLDE SUGGÉRÉ")
        self.libelle_solde.setObjectName("libelle_solde_caisse")
        self.affichage_solde = QLabel("")
        self.affichage_solde.setObjectName("affichage_solde_caisse")
        self.affichage_solde.setAlignment(Qt.AlignCenter)
        effet_lumineux = QGraphicsDropShadowEffect(self.affichage_solde)
        effet_lumineux.setColor(QColor("#39ff68"))
        effet_lumineux.setBlurRadius(24)
        effet_lumineux.setOffset(0, 0)
        self.affichage_solde.setGraphicsEffect(effet_lumineux)

        ecran_caisse = QFrame()
        ecran_caisse.setObjectName("ecran_caisse")
        ecran_caisse.setStyleSheet(
            "QFrame#ecran_caisse {"
            "background-color: #101914;"
            "border: 2px solid #34483a;"
            "border-radius: 12px;"
            "}"
            "QLabel#libelle_solde_caisse {"
            "color: #91a99a;"
            "font-size: 10pt;"
            "font-weight: bold;"
            "letter-spacing: 2px;"
            "}"
            "QLabel#affichage_solde_caisse {"
            "color: #71ff8b;"
            "font-family: Consolas, 'Courier New', monospace;"
            "font-size: 30pt;"
            "font-weight: bold;"
            "padding: 10px 12px 14px;"
            "}"
        )
        ecran_layout = QVBoxLayout(ecran_caisse)
        ecran_layout.setContentsMargins(18, 12, 18, 12)
        ecran_layout.setSpacing(2)
        ecran_layout.addWidget(self.libelle_solde, alignment=Qt.AlignCenter)
        ecran_layout.addWidget(self.affichage_solde)

        self.solde_ouverture = QLineEdit()
        self.solde_ouverture.setPlaceholderText("Montant en DA")
        self.bouton_ouvrir = QPushButton("Ouvrir la caisse")
        self.bouton_ouvrir.setObjectName("principal")
        self.bouton_ouvrir.clicked.connect(self.ouvrir_caisse)
        self.bouton_reinitialiser_ouverture = QPushButton("Réinitialiser le solde à 0")
        self.bouton_reinitialiser_ouverture.clicked.connect(self.reinitialiser_ouverture)
        formulaire_ouverture = QFormLayout()
        formulaire_ouverture.addRow("Solde d'ouverture (DA)", self.solde_ouverture)
        formulaire_ouverture.addRow(self.bouton_ouvrir)
        formulaire_ouverture.addRow(self.bouton_reinitialiser_ouverture)
        groupe_ouverture = QGroupBox("Ouverture")
        groupe_ouverture.setLayout(formulaire_ouverture)

        self.montant_depense = QLineEdit()
        self.montant_depense.setPlaceholderText("Montant en DA")
        self.description_depense = QLineEdit()
        self.description_depense.setPlaceholderText("Motif de la dépense")
        self.bouton_depense = QPushButton("Enregistrer la dépense")
        self.bouton_depense.clicked.connect(self.enregistrer_depense)
        formulaire_depense = QFormLayout()
        formulaire_depense.addRow("Montant (DA)", self.montant_depense)
        formulaire_depense.addRow("Description", self.description_depense)
        formulaire_depense.addRow(self.bouton_depense)
        groupe_depense = QGroupBox("Dépense en espèces")
        groupe_depense.setLayout(formulaire_depense)

        self.montant_compte = QLineEdit()
        self.montant_compte.setPlaceholderText("Montant compté en DA")
        self.raison_ecart = QLineEdit()
        self.raison_ecart.setPlaceholderText("Obligatoire en cas d'écart")
        self.bouton_cloturer = QPushButton("Clôturer la caisse")
        self.bouton_cloturer.clicked.connect(self.cloturer_caisse)
        formulaire_cloture = QFormLayout()
        formulaire_cloture.addRow("Montant compté (DA)", self.montant_compte)
        formulaire_cloture.addRow("Raison de l'écart", self.raison_ecart)
        formulaire_cloture.addRow(self.bouton_cloturer)
        groupe_cloture = QGroupBox("Clôture")
        groupe_cloture.setLayout(formulaire_cloture)

        mise_en_page = QVBoxLayout(self)
        mise_en_page.addWidget(titre)
        mise_en_page.addWidget(ecran_caisse)
        mise_en_page.addWidget(self.resume)
        mise_en_page.addWidget(groupe_ouverture)
        mise_en_page.addWidget(groupe_depense)
        mise_en_page.addWidget(groupe_cloture)
        mise_en_page.addStretch()

        self.rafraichir()

    def rafraichir(self) -> None:
        """Recharge l'état de la caisse et le résumé de la session en cours."""
        session = current_session(self.conn)
        if session is None:
            self.session_id = None
            ouverture = suggested_opening(self.conn)
            self.solde_ouverture.setText(centimes_to_text(ouverture))
            self.montant_compte.clear()
            self.libelle_solde.setText("SOLDE SUGGÉRÉ")
            self.affichage_solde.setText(format_da(ouverture))
            self.resume.setText(
                "Aucune session ouverte. "
                f"Solde d'ouverture suggéré : {format_da(ouverture)}."
            )
        else:
            self.session_id = session["id"]
            resume = session_summary(self.conn, self.session_id)
            details = [
                f"{LIBELLES_MOUVEMENT.get(kind, kind)} : {format_da(montant)}"
                for kind, montant in sorted(resume["by_kind"].items())
            ]
            texte = [
                f"Session ouverte — solde initial : {format_da(resume['opening'])}.",
                *details,
            ]
            self.libelle_solde.setText("SOLDE ATTENDU")
            self.affichage_solde.setText(format_da(resume["expected"]))
            self.resume.setText("\n".join(texte))
            self.montant_compte.setText(centimes_to_text(resume["expected"]))

        ouverte = self.session_id is not None
        legacy = not has_users(self.conn)
        peut_ouvrir = legacy or can(self.current_user, "cash_open")
        peut_gerer = legacy or can(self.current_user, "cash_close")
        self.solde_ouverture.setEnabled(not ouverte)
        self.bouton_ouvrir.setEnabled(not ouverte and peut_ouvrir)
        self.bouton_reinitialiser_ouverture.setEnabled(not ouverte and peut_ouvrir)
        self.montant_depense.setEnabled(ouverte and peut_gerer)
        self.description_depense.setEnabled(ouverte and peut_gerer)
        self.bouton_depense.setEnabled(ouverte and peut_gerer)
        self.montant_compte.setEnabled(ouverte and peut_gerer)
        self.raison_ecart.setEnabled(ouverte and peut_gerer)
        self.bouton_cloturer.setEnabled(ouverte and peut_gerer)

    def reinitialiser_ouverture(self) -> None:
        """Prépare une ouverture à zéro sans modifier les sessions déjà enregistrées."""
        if self.session_id is not None:
            return
        self.solde_ouverture.setText("0")
        self.libelle_solde.setText("SOLDE D'OUVERTURE")
        self.affichage_solde.setText(format_da(0))
        self.resume.setText(
            "Solde d'ouverture réglé à 0,00 DA. Cliquez sur « Ouvrir la caisse » pour l'appliquer."
        )

    def ouvrir_caisse(self) -> None:
        try:
            ouverture = da_to_centimes(self.solde_ouverture.text())
            if ouverture < 0:
                raise ValidationError("Le solde d'ouverture ne peut pas être négatif.")
            open_session(self.conn, opening_centimes=ouverture,
                         actor_user_id=self.actor_user_id)
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        self.rafraichir()

    def enregistrer_depense(self) -> None:
        if self.session_id is None:
            self.rafraichir()
            return
        try:
            montant = da_to_centimes(self.montant_depense.text())
            record_expense(
                self.conn, self.session_id, montant, self.description_depense.text().strip(),
                actor_user_id=self.actor_user_id,
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return
        self.montant_depense.clear()
        self.description_depense.clear()
        self.rafraichir()

    def cloturer_caisse(self) -> None:
        if self.session_id is None:
            self.rafraichir()
            return
        try:
            compte = da_to_centimes(self.montant_compte.text())
            if compte < 0:
                raise ValidationError("Le montant compté ne peut pas être négatif.")
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        resume = session_summary(self.conn, self.session_id)
        difference = compte - resume["expected"]
        texte = (
            f"Confirmer la clôture ?\n\n"
            f"Solde attendu : {format_da(resume['expected'])}\n"
            f"Montant compté : {format_da(compte)}\n"
            f"Écart : {format_da(difference)}"
        )
        if not demander_confirmation(self, "Clôturer la caisse", texte):
            return

        try:
            resultat = close_session(
                self.conn,
                self.session_id,
                compte,
                reason=self.raison_ecart.text().strip() or None,
                actor_user_id=self.actor_user_id,
            )
        except MagasinError as erreur:
            montrer_erreur(self, erreur)
            return

        self.rafraichir()
        self.resume.setText(
            "Caisse clôturée. "
            f"Attendu : {format_da(resultat['expected'])} — "
            f"Compté : {format_da(resultat['counted'])} — "
            f"Écart : {format_da(resultat['difference'])}."
        )
        self.libelle_solde.setText("SOLDE COMPTÉ À LA CLÔTURE")
        self.affichage_solde.setText(format_da(resultat["counted"]))

"""Écran de gestion de la caisse : ouverture, dépenses, résumé et clôture."""

from PySide6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from magasin.cash import (close_session, current_session, open_session, record_expense,
                          session_summary, suggested_opening)
from magasin.errors import MagasinError, ValidationError
from magasin.money import centimes_to_text, da_to_centimes, format_da

from .outils import demander_confirmation, montrer_erreur

LIBELLES_MOUVEMENT = {
    "acompte": "Acomptes de ventes",
    "depense": "Dépenses",
    "retour_client": "Retours clients",
    "vente_especes": "Ventes en espèces",
}


class PageCaisse(QWidget):
    """Interface de caisse, sans dupliquer les règles métier de magasin.cash."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.session_id = None

        titre = QLabel("Caisse")
        titre.setObjectName("titre")

        self.resume = QLabel("")
        self.resume.setObjectName("info")
        self.resume.setWordWrap(True)

        self.solde_ouverture = QLineEdit()
        self.solde_ouverture.setPlaceholderText("Montant en DA")
        self.bouton_ouvrir = QPushButton("Ouvrir la caisse")
        self.bouton_ouvrir.setObjectName("principal")
        self.bouton_ouvrir.clicked.connect(self.ouvrir_caisse)
        formulaire_ouverture = QFormLayout()
        formulaire_ouverture.addRow("Solde d'ouverture (DA)", self.solde_ouverture)
        formulaire_ouverture.addRow(self.bouton_ouvrir)
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
                f"Solde attendu : {format_da(resume['expected'])}.",
            ]
            self.resume.setText("\n".join(texte))
            self.montant_compte.setText(centimes_to_text(resume["expected"]))

        ouverte = self.session_id is not None
        self.solde_ouverture.setEnabled(not ouverte)
        self.bouton_ouvrir.setEnabled(not ouverte)
        self.montant_depense.setEnabled(ouverte)
        self.description_depense.setEnabled(ouverte)
        self.bouton_depense.setEnabled(ouverte)
        self.montant_compte.setEnabled(ouverte)
        self.raison_ecart.setEnabled(ouverte)
        self.bouton_cloturer.setEnabled(ouverte)

    def ouvrir_caisse(self) -> None:
        try:
            ouverture = da_to_centimes(self.solde_ouverture.text())
            if ouverture < 0:
                raise ValidationError("Le solde d'ouverture ne peut pas être négatif.")
            open_session(self.conn, opening_centimes=ouverture)
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
                self.conn, self.session_id, montant, self.description_depense.text().strip()
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

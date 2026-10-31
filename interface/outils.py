"""Petits outils communs aux fenêtres."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from magasin.errors import MagasinError

TYPES_MOUVEMENT = {
    "reception_achat": "Réception achat", "vente": "Vente", "retour_client": "Retour client",
    "retour_fournisseur": "Retour fournisseur", "ajustement": "Ajustement",
    "inventaire": "Inventaire", "transfert": "Transfert",
}


def ouvrir_dialogue(dialogue):
    """Ouvre une fenêtre modale AU PREMIER PLAN (sous Windows, elle pourrait sinon s'ouvrir derrière)."""
    dialogue.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialogue.show()
    dialogue.raise_()
    dialogue.activateWindow()
    return dialogue.exec()


def _boite(parent, icone, titre: str, texte: str, boutons=QMessageBox.Ok) -> QMessageBox:
    boite = QMessageBox(icone, titre, texte, boutons, parent)
    boite.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return boite


def montrer_erreur(parent: QWidget | None, erreur: Exception, detail: str = "") -> None:
    """Affiche une erreur sans planter le logiciel (message clair pour une erreur métier)."""
    if isinstance(erreur, MagasinError):
        _boite(parent, QMessageBox.Warning, "Action impossible", str(erreur)).exec()
    else:
        texte = (f"Une erreur inattendue s'est produite :\n\n{erreur}\n\n"
                 "Elle est enregistrée dans le fichier journal_erreurs.txt. Envoyez ce texte à Claude.")
        if detail:
            texte += f"\n\n{detail[-700:]}"
        _boite(parent, QMessageBox.Critical, "Erreur inattendue", texte).exec()


def demander_confirmation(parent: QWidget | None, titre: str, texte: str) -> bool:
    boite = _boite(parent, QMessageBox.Question, titre, texte, QMessageBox.Yes | QMessageBox.No)
    boite.setDefaultButton(QMessageBox.No)
    return boite.exec() == QMessageBox.Yes

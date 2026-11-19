"""Démarrage commun : gestion des erreurs visibles, journal, fenêtre au premier plan."""

import sys
import traceback
from datetime import datetime

from PySide6.QtWidgets import QApplication, QDialog

from magasin.paths import data_dir
from magasin.users import has_users

from .fenetre_principale import FenetrePrincipale
from .outils import montrer_erreur
from .security import FirstAdminDialog, LoginDialog, afficher_code_secours


def installer_gestion_erreurs() -> None:
    """Une erreur dans un bouton ne reste plus silencieuse : message à l'écran + fichier journal."""
    def gestionnaire(type_erreur, erreur, trace):
        texte = "".join(traceback.format_exception(type_erreur, erreur, trace))
        print(texte, file=sys.stderr)
        try:
            with open(data_dir() / "journal_erreurs.txt", "a", encoding="utf-8") as fichier:
                fichier.write(f"\n--- {datetime.now():%d/%m/%Y %H:%M:%S} ---\n{texte}")
        except OSError:
            pass
        if QApplication.instance() is not None:
            montrer_erreur(None, erreur, texte)
    sys.excepthook = gestionnaire


def lancer(conn, titre: str, info: str) -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    installer_gestion_erreurs()
    creation_initiale = not has_users(conn)
    if not creation_initiale:
        connexion = LoginDialog(conn)
    else:
        connexion = FirstAdminDialog(conn)
    if connexion.exec() != QDialog.DialogCode.Accepted:
        return 0
    if creation_initiale:
        afficher_code_secours(None, connexion.recovery_code)
    fenetre = FenetrePrincipale(conn, titre, info, current_user=connexion.user)
    fenetre.show()
    fenetre.raise_()
    fenetre.activateWindow()
    return application.exec()

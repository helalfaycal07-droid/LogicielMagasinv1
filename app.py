"""Lance le logiciel avec VOS données (base dans le dossier utilisateur de Windows).

Dans VS Code : ouvrir ce fichier puis cliquer sur le bouton ▶ (Run Python File).
Pour essayer sans rien enregistrer, lancez plutôt app_demo.py.
"""

import sqlite3
import sys
from version_checker import check_for_updates

from PySide6.QtWidgets import QApplication, QMessageBox
from interface.demarrage import lancer
from magasin.db import connect
from magasin.paths import database_path
from magasin.startup import backup_before_migrations


def main() -> int:
    sauvegarde = None
    try:
        chemin = database_path()
        sauvegarde = backup_before_migrations(chemin)
        conn = connect(chemin)
    except (OSError, ValueError, sqlite3.Error) as erreur:
        application = QApplication.instance() or QApplication(sys.argv)
        details = f"Le démarrage est interrompu.\n\n{erreur}"
        if sauvegarde is not None:
            details += f"\n\nCopie de sécurité : {sauvegarde}"
        QMessageBox.critical(
            None,
            "Démarrage impossible",
            details,
        )
        return 1

    information = f"Base de données : {chemin}"
    if sauvegarde is not None:
        information += f"  |  Copie avant mise à jour : {sauvegarde}"
    return lancer(conn, "Gestion de magasin", information)


if __name__ == "__main__":
    sys.exit(main())

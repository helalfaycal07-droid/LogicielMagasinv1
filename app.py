"""Lance le logiciel avec VOS données (base dans le dossier utilisateur de Windows).

Dans VS Code : ouvrir ce fichier puis cliquer sur le bouton ▶ (Run Python File).
Pour essayer sans rien enregistrer, lancez plutôt app_demo.py.
"""

import sys

from interface.demarrage import lancer
from magasin.db import connect
from magasin.paths import database_path


def main() -> int:
    chemin = database_path()
    return lancer(connect(chemin), "Gestion de magasin", f"Base de données : {chemin}")


if __name__ == "__main__":
    sys.exit(main())

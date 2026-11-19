"""Lance le logiciel en MODE DÉMONSTRATION : données fictives, rien n'est enregistré.

Dans VS Code : ouvrir ce fichier puis cliquer sur le bouton ▶ (Run Python File).
"""

import sys

from interface.demarrage import lancer
from magasin.db import connect
from magasin.demo_data import remplir_demo


def main() -> int:
    conn = connect(":memory:")
    try:
        remplir_demo(conn)
        return lancer(
            conn,
            "Gestion de magasin — DÉMONSTRATION",
            "MODE DÉMONSTRATION : données fictives, rien n'est enregistré",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

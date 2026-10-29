"""Teste que PySide6 (les fenêtres) fonctionne sur cet ordinateur.

Dans VS Code : ouvrir ce fichier puis cliquer sur le bouton ▶ (Run Python File).
Si PySide6 n'est pas installé, le fichier l'installe tout seul (plusieurs centaines de Mo : patience).
"""

import subprocess
import sys


def assurer_pyside6():
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 n'est pas installé : installation en cours, cela peut durer quelques minutes...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PySide6"])


def main():
    assurer_pyside6()
    import PySide6
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

    print("PySide6 version :", PySide6.__version__, "| Python :", sys.version.split()[0])
    app = QApplication(sys.argv)
    fenetre = QWidget()
    fenetre.setWindowTitle("Test de fenêtre")
    mise_en_page = QVBoxLayout(fenetre)
    mise_en_page.addWidget(QLabel("Ça marche : les fenêtres fonctionnent sur cet ordinateur."))
    bouton = QPushButton("Fermer")
    bouton.clicked.connect(fenetre.close)
    mise_en_page.addWidget(bouton)
    fenetre.resize(420, 120)
    fenetre.show()
    app.exec()
    print("Test terminé.")


if __name__ == "__main__":
    main()

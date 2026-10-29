"""Lance tous les tests du noyau. Dans VS Code : ouvrir ce fichier puis cliquer sur le bouton ▶ (Run Python File)."""

import os
import sys
import unittest

dossier = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, dossier)

suite = unittest.defaultTestLoader.discover(os.path.join(dossier, "tests"), top_level_dir=dossier)
resultat = unittest.TextTestRunner(verbosity=2).run(suite)
print()
print("TOUT EST BON" if resultat.wasSuccessful() else "IL Y A DES ERREURS : envoyez ce texte à Claude")

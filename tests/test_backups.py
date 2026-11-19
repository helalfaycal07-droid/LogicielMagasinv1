"""Tests de sauvegarde et de restauration SQLite."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from magasin.backups import backup_database, restore_database
from magasin.errors import ValidationError
from magasin.products import create_product, get_product
from tests.helpers import new_db


class BackupsTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "BKP-001", "Produit sauvegardé", 20_000, initial_stock=4
        )

    def test_sauvegarde_et_restauration_retablissent_les_donnees(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "magasin.sqlite"
            backup_database(self.conn, chemin)
            create_product(self.conn, "BKP-002", "Produit temporaire", 5_000)

            restore_database(self.conn, chemin)

        self.assertIsNotNone(get_product(self.conn, self.product_id))
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM products WHERE reference = 'BKP-002'"
        ).fetchone())
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_refuse_un_fichier_qui_n_est_pas_une_sauvegarde_du_logiciel(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "invalide.sqlite"
            source = sqlite3.connect(chemin)
            try:
                source.execute("PRAGMA user_version = 3")
                source.execute("CREATE TABLE unrelated (id INTEGER)")
            finally:
                source.close()

            with self.assertRaises(ValidationError):
                restore_database(self.conn, chemin)
        self.assertIsNotNone(get_product(self.conn, self.product_id))

    def test_refuse_un_fichier_absent(self):
        with tempfile.TemporaryDirectory() as dossier:
            with self.assertRaises(ValidationError):
                restore_database(self.conn, Path(dossier) / "absent.sqlite")


if __name__ == "__main__":
    unittest.main()

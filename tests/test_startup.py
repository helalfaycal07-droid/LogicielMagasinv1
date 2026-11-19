"""Tests de sauvegarde automatique des bases avant migration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magasin.db import MIGRATIONS, _seed, connect
from magasin.startup import backup_before_migrations


class StartupBackupTests(unittest.TestCase):
    @staticmethod
    def _create_version_one_database(database: Path) -> None:
        old_connection = sqlite3.connect(str(database), isolation_level=None)
        old_connection.row_factory = sqlite3.Row
        old_connection.executescript(
            "BEGIN;\n" + MIGRATIONS[0] + "\nPRAGMA user_version = 1;\nCOMMIT;"
        )
        _seed(old_connection)
        old_connection.execute(
            "INSERT INTO products "
            "(reference, name, price_centimes, created_at) "
            "VALUES ('UPGRADE-001', 'Produit conservé', 12500, "
            "'2026-11-03T10:00:00+01:00')"
        )
        old_connection.close()

    def test_sauvegarde_la_base_ancienne_avant_migration_sans_perte_de_donnees(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "magasin.db"
            self._create_version_one_database(database)

            backup = backup_before_migrations(database)
            self.assertIsNotNone(backup)
            self.assertTrue(backup.is_file())

            backup_connection = sqlite3.connect(str(backup))
            try:
                self.assertEqual(
                    backup_connection.execute("PRAGMA user_version").fetchone()[0], 1
                )
                self.assertEqual(
                    backup_connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    backup_connection.execute(
                        "SELECT name FROM products WHERE reference = 'UPGRADE-001'"
                    ).fetchone()[0],
                    "Produit conservé",
                )
            finally:
                backup_connection.close()

            upgraded = connect(str(database))
            try:
                self.assertEqual(
                    upgraded.execute("PRAGMA user_version").fetchone()[0],
                    len(MIGRATIONS),
                )
                self.assertEqual(
                    upgraded.execute(
                        "SELECT name FROM products WHERE reference = 'UPGRADE-001'"
                    ).fetchone()[0],
                    "Produit conservé",
                )
            finally:
                upgraded.close()

    def test_application_cree_la_copie_avant_d_ouvrir_une_base_a_mettre_a_jour(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "magasin.db"
            self._create_version_one_database(database)

            def close_connection(conn, *_args):
                conn.close()
                return 0

            with (
                patch("app.database_path", return_value=str(database)),
                patch("app.lancer", side_effect=close_connection) as launch,
            ):
                from app import main

                self.assertEqual(main(), 0)

            backups = list((database.parent / "backups").glob("*-avant-migration-*.sqlite"))
            self.assertEqual(len(backups), 1)
            information = launch.call_args.args[2]
            self.assertIn("Copie avant mise à jour :", information)
            reported_backup = Path(information.rsplit("Copie avant mise à jour : ", 1)[1])
            self.assertEqual(reported_backup.resolve(), backups[0].resolve())
            upgraded = sqlite3.connect(str(database))
            try:
                self.assertEqual(
                    upgraded.execute("PRAGMA user_version").fetchone()[0],
                    len(MIGRATIONS),
                )
            finally:
                upgraded.close()

    def test_ne_cree_pas_de_sauvegarde_si_aucune_migration_n_est_necessaire(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "magasin.db"
            conn = connect(str(database))
            conn.close()

            self.assertIsNone(backup_before_migrations(database))
            self.assertFalse((Path(temporary_directory) / "backups").exists())

    def test_ne_cree_pas_de_sauvegarde_pour_une_base_absente(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nouveau.db"

            self.assertIsNone(backup_before_migrations(database))
            self.assertFalse(database.exists())

    def test_refuse_une_base_de_version_plus_recente_sans_la_migrer(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "future.db"
            conn = connect(str(database))
            conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
            conn.close()

            with self.assertRaisesRegex(
                sqlite3.DatabaseError, "version plus récente"
            ):
                connect(str(database))

            check = sqlite3.connect(str(database))
            try:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0],
                    len(MIGRATIONS) + 1,
                )
            finally:
                check.close()

    def test_affiche_une_erreur_si_la_preparation_de_la_base_echoue(self):
        from app import main

        with (
            patch("app.database_path", return_value="base.db"),
            patch(
                "app.backup_before_migrations",
                side_effect=sqlite3.DatabaseError("base verrouillée"),
            ),
            patch("app.QApplication") as application,
            patch("app.QMessageBox.critical") as show_error,
        ):
            self.assertEqual(main(), 1)

        application.instance.assert_called_once_with()
        show_error.assert_called_once()
        self.assertIn("base verrouillée", show_error.call_args.args[2])


if __name__ == "__main__":
    unittest.main()

"""Tests de sécurité du lanceur de démonstration."""

import sqlite3
import unittest
from unittest.mock import patch

import app_demo
from magasin.db import connect
from magasin.demo_data import remplir_demo


class AppDemoTests(unittest.TestCase):
    def test_utilise_une_base_isolee_et_la_ferme_apres_la_demo(self):
        connections = []

        def connect_spy(path):
            self.assertEqual(path, ":memory:")
            conn = connect(path)
            connections.append(conn)
            return conn

        def lancer_spy(conn, titre, info):
            self.assertEqual(titre, "Gestion de magasin — DÉMONSTRATION")
            self.assertIn("rien n'est enregistré", info)
            self.assertGreater(
                conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0
            )
            return 7

        with (
            patch("app_demo.connect", side_effect=connect_spy),
            patch("app_demo.remplir_demo", wraps=remplir_demo) as seed_demo,
            patch("app_demo.lancer", side_effect=lancer_spy),
        ):
            self.assertEqual(app_demo.main(), 7)

        seed_demo.assert_called_once_with(connections[0])
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")

    def test_ferme_la_base_si_le_lancement_echoue(self):
        conn = connect(":memory:")
        with (
            patch("app_demo.connect", return_value=conn),
            patch("app_demo.remplir_demo"),
            patch("app_demo.lancer", side_effect=RuntimeError("échec simulé")),
        ):
            with self.assertRaisesRegex(RuntimeError, "échec simulé"):
                app_demo.main()

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()

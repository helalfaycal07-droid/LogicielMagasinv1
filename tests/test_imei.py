import os
import sqlite3
import tempfile
import unittest

from magasin import db
from magasin.db import connect
from magasin.errors import ValidationError
from magasin.integrity import check_all
from magasin.products import create_product, update_product
from magasin.sales import Line, PaymentIn, create_sale
from magasin.serials import (add_units, complete_imei, count_in_stock, find_units, get_unit, luhn_ok, parse_imei_list,
                             remove_unit, trace, units_of, validate_imei)
from magasin.stock import inventory_count, stock_of
from tests.helpers import credit_customer, new_db, open_cash


def imei(n: int) -> str:
    """IMEI fictif valide (chiffre de contrôle calculé)."""
    return complete_imei(f"35999900{n:06d}")


class ControleImeiTests(unittest.TestCase):
    def test_exemples_connus(self):
        self.assertTrue(luhn_ok("490154203237518"))
        self.assertTrue(luhn_ok("356938035643809"))

    def test_une_faute_de_frappe_est_detectee(self):
        valide = imei(1)
        faux = valide[:-1] + str((int(valide[-1]) + 1) % 10)
        with self.assertRaises(ValidationError) as ctx:
            validate_imei(faux, "IMEI1")
        self.assertIn("chiffre de contrôle", str(ctx.exception))

    def test_longueur_et_caracteres(self):
        for texte in ("", "12345", "35999900000001", "3599990000000123", "35999900000000a"):
            with self.assertRaises(ValidationError):
                validate_imei(texte)

    def test_espaces_et_tirets_sont_ignores(self):
        valide = imei(2)
        self.assertEqual(validate_imei(f"{valide[:2]}-{valide[2:8]} {valide[8:]}"), valide)


class ListeColleeTests(unittest.TestCase):
    def test_lecture_d_une_liste_avec_separateurs_varies(self):
        a, b, c, d, e = (imei(n) for n in range(1, 6))
        texte = f"{a} {b}\n{c},{d}\n\n  {e}  \n"
        self.assertEqual(parse_imei_list(texte), [(a, b), (c, d), (e, None)])

    def test_la_ligne_fausse_est_indiquee(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_imei_list(f"{imei(1)} {imei(2)}\n{imei(3)} 12345")
        self.assertIn("Ligne 2", str(ctx.exception))

    def test_trop_de_numeros_ou_liste_vide(self):
        with self.assertRaises(ValidationError):
            parse_imei_list(f"{imei(1)} {imei(2)} {imei(3)}")
        with self.assertRaises(ValidationError):
            parse_imei_list("  \n  ")


class EntreeDesAppareilsTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.phone = create_product(self.conn, "P1", "Smartphone Test 128 Go", 3_200_000,
                                    cost_centimes=2_600_000, track_serial=True)

    def test_entree_de_deux_telephones_avec_imei1_et_imei2(self):
        add_units(self.conn, self.phone, [(imei(1), imei(2)), (imei(3), imei(4))])
        self.assertEqual(stock_of(self.conn, self.phone), 2)
        self.assertEqual(count_in_stock(self.conn, self.phone), 2)
        unite = get_unit(self.conn, imei(2))                 # retrouvé par son IMEI2
        self.assertEqual(unite["imei1"], imei(1))
        self.assertEqual(check_all(self.conn), [])

    def test_telephone_une_seule_sim(self):
        add_units(self.conn, self.phone, [(imei(1), None)])
        self.assertIsNone(get_unit(self.conn, imei(1))["imei2"])

    def test_un_imei_n_existe_qu_une_fois(self):
        add_units(self.conn, self.phone, [(imei(1), imei(2))])
        for essai in ([(imei(1), None)], [(imei(5), imei(1))], [(imei(2), imei(6))]):
            with self.assertRaises(ValidationError) as ctx:
                add_units(self.conn, self.phone, essai)
            self.assertIn("existe déjà", str(ctx.exception))
        self.assertEqual(stock_of(self.conn, self.phone), 1)

    def test_doublons_dans_la_meme_liste_et_imei1_egal_imei2(self):
        with self.assertRaises(ValidationError):
            add_units(self.conn, self.phone, [(imei(1), imei(2)), (imei(3), imei(2))])
        with self.assertRaises(ValidationError):
            add_units(self.conn, self.phone, [(imei(1), imei(1))])
        self.assertEqual(stock_of(self.conn, self.phone), 0)

    def test_tout_ou_rien_si_un_appareil_est_invalide(self):
        with self.assertRaises(ValidationError):
            add_units(self.conn, self.phone, [(imei(1), imei(2)), ("123", None)])
        self.assertEqual(stock_of(self.conn, self.phone), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_serials").fetchone()[0], 0)

    def test_produit_non_suivi_refuse(self):
        simple = create_product(self.conn, "C1", "Câble", 80_000)
        with self.assertRaises(ValidationError):
            add_units(self.conn, simple, [(imei(1), None)])

    def test_inventaire_interdit_pour_un_produit_suivi(self):
        with self.assertRaises(ValidationError):
            inventory_count(self.conn, self.phone, 3)

    def test_sortie_d_un_appareil_exige_une_raison(self):
        ids = add_units(self.conn, self.phone, [(imei(1), imei(2))])
        with self.assertRaises(ValidationError):
            remove_unit(self.conn, ids[0], "  ")
        remove_unit(self.conn, ids[0], "Écran cassé à la réception")
        self.assertEqual(stock_of(self.conn, self.phone), 0)
        self.assertEqual(get_unit(self.conn, imei(1))["status"], "sorti")
        self.assertEqual(check_all(self.conn), [])

    def test_changer_le_suivi_seulement_a_vide(self):
        add_units(self.conn, self.phone, [(imei(1), imei(2))])
        with self.assertRaises(ValidationError):
            update_product(self.conn, self.phone, track_serial=False)
        autre = create_product(self.conn, "X1", "Autre", 10_000)
        update_product(self.conn, autre, track_serial=True)

    def test_recherche_partielle(self):
        add_units(self.conn, self.phone, [(imei(1), imei(2))])
        self.assertEqual(len(find_units(self.conn, imei(2)[-6:])), 1)
        with self.assertRaises(ValidationError):
            find_units(self.conn, "12")


class VenteAvecImeiTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.phone = create_product(self.conn, "P1", "Smartphone Test 128 Go", 3_200_000, track_serial=True)
        add_units(self.conn, self.phone, [(imei(1), imei(2)), (imei(3), imei(4)), (imei(5), None)])
        open_cash(self.conn)

    def test_vente_d_un_telephone_par_son_imei(self):
        r = create_sale(self.conn, [Line(self.phone, 1, imeis=[imei(1)])], [PaymentIn("especes", 3_200_000)])
        self.assertEqual(stock_of(self.conn, self.phone), 2)
        unite = get_unit(self.conn, imei(1))
        self.assertEqual((unite["status"], unite["sale_id"]), ("vendu", r.sale_id))
        self.assertEqual(check_all(self.conn), [])

    def test_on_peut_scanner_l_imei2_a_la_place_de_l_imei1(self):
        create_sale(self.conn, [Line(self.phone, 1, imeis=[imei(4)])], [PaymentIn("especes", 3_200_000)])
        self.assertEqual(get_unit(self.conn, imei(3))["status"], "vendu")

    def test_vente_de_deux_telephones(self):
        create_sale(self.conn, [Line(self.phone, 2, imeis=[imei(1), imei(3)])], [PaymentIn("especes", 6_400_000)])
        self.assertEqual(count_in_stock(self.conn, self.phone), 1)
        self.assertEqual(stock_of(self.conn, self.phone), 1)

    def test_refus_sans_imei_ou_mauvais_nombre(self):
        for ligne in (Line(self.phone, 1), Line(self.phone, 2, imeis=[imei(1)])):
            with self.assertRaises(ValidationError):
                create_sale(self.conn, [ligne], [PaymentIn("especes", 9_600_000)])
        self.assertEqual(stock_of(self.conn, self.phone), 3)

    def test_refus_imei_inconnu_deja_vendu_ou_d_un_autre_produit(self):
        create_sale(self.conn, [Line(self.phone, 1, imeis=[imei(1)])], [PaymentIn("especes", 3_200_000)])
        autre = create_product(self.conn, "P2", "Autre téléphone", 2_000_000, track_serial=True)
        add_units(self.conn, autre, [(imei(9), None)])
        for numero in (imei(1), imei(77), imei(9)):
            with self.assertRaises(ValidationError):
                create_sale(self.conn, [Line(self.phone, 1, imeis=[numero])], [PaymentIn("especes", 3_200_000)])
        self.assertEqual(stock_of(self.conn, self.phone), 2)  # les refus n'ont rien écrit

    def test_une_vente_refusee_ne_marque_aucun_appareil_vendu(self):
        with self.assertRaises(Exception):
            create_sale(self.conn, [Line(self.phone, 1, imeis=[imei(1)])], [PaymentIn("especes", 100_000)])
        self.assertEqual(get_unit(self.conn, imei(1))["status"], "en_stock")

    def test_produit_non_suivi_avec_imei_refuse(self):
        simple = create_product(self.conn, "C1", "Câble", 80_000, initial_stock=5)
        with self.assertRaises(ValidationError):
            create_sale(self.conn, [Line(simple, 1, imeis=[imei(1)])], [PaymentIn("especes", 80_000)])

    def test_tracabilite_apres_vente_a_un_client(self):
        client = credit_customer(self.conn, "Client Test")
        create_sale(self.conn, [Line(self.phone, 1, imeis=[imei(3)])], [PaymentIn("especes", 1_000_000)],
                    customer_id=client)
        fiche = trace(self.conn, imei(4))                    # on retrouve par l'IMEI2
        self.assertEqual((fiche["status"], fiche["customer_name"], fiche["sale_number"]),
                         ("vendu", "Client Test", "VTE-000001"))
        self.assertEqual(len(units_of(self.conn, self.phone, "en_stock")), 2)
        self.assertIsNone(trace(self.conn, imei(99)))


class MigrationTests(unittest.TestCase):
    def test_une_base_de_la_version_1_est_mise_a_jour_sans_perte(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "ancienne.db")
            ancienne = sqlite3.connect(chemin, isolation_level=None)
            ancienne.row_factory = sqlite3.Row
            ancienne.executescript("BEGIN;\n" + db.MIGRATION_1 + "\nPRAGMA user_version = 1;\nCOMMIT;")
            db._seed(ancienne)
            ancienne.execute("INSERT INTO products (reference, name, price_centimes, created_at) "
                             "VALUES ('A', 'Ancien produit', 1000, '2026-10-01T10:00:00+01:00')")
            ancienne.close()
            conn = connect(chemin)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
            self.assertEqual(conn.execute("SELECT name FROM products").fetchone()["name"], "Ancien produit")
            conn.execute("SELECT COUNT(*) FROM product_serials")  # la nouvelle table existe
            conn.close()

    def test_une_base_de_la_version_3_recoit_la_migration_des_retours(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "version-3.db")
            ancienne = sqlite3.connect(chemin, isolation_level=None)
            ancienne.row_factory = sqlite3.Row
            ancienne.executescript(
                "BEGIN;\n" + db.MIGRATION_1 + "\nPRAGMA user_version = 1;\nCOMMIT;"
            )
            db._seed(ancienne)
            for version, migration in enumerate(db.MIGRATIONS[1:3], start=2):
                ancienne.executescript(
                    "BEGIN;\n" + migration
                    + f"\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            ancienne.execute(
                "INSERT INTO purchase_receipts "
                "(number, supplier, total_centimes, created_at) VALUES (?,?,?,?)",
                ("REC-OLD", "Ancien fournisseur", 5000, "2026-10-01T10:00:00+01:00"),
            )
            ancienne.close()

            conn = connect(chemin)

            self.assertEqual(
                conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS)
            )
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sale_returns'"
            ).fetchone())
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(installments)")
            }
            self.assertIn("cancelled_centimes", columns)
            sale_item_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(sale_items)")
            }
            self.assertIn("unit_cost_centimes", sale_item_columns)
            sale_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(sales)")
            }
            self.assertIn("quote_id", sale_columns)
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'quotes'"
            ).fetchone())
            supplier = conn.execute(
                "SELECT s.name, r.supplier_id FROM suppliers s "
                "JOIN purchase_receipts r ON r.supplier_id = s.id "
                "WHERE r.number = 'REC-OLD'"
            ).fetchone()
            self.assertEqual(supplier["name"], "Ancien fournisseur")
            audit_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")
            }
            self.assertIn("actor_user_id", audit_columns)
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""Tests de connexion, rôles et contrôle des actions sensibles."""

import unittest

from magasin.cash import close_session, open_session
from magasin.customers import create_customer
from magasin.errors import AuthenticationError, PermissionDenied, ValidationError
from magasin.products import create_product, update_product
from magasin.sales import Line, PaymentIn, create_sale
from magasin.users import (authenticate, create_first_admin, create_user,
                           reset_admin_account, update_user)
from tests.helpers import new_db


class UserPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "USR-001", "Produit rôles", 20_000, cost_centimes=10_000,
            initial_stock=5,
        )
        self.admin_id = create_first_admin(self.conn, "admin", "MotDePasseAdmin1")
        self.cashier_id = create_user(
            self.conn, self.admin_id, "caisse", "MotDePasseCaisse1", "caissier"
        )
        self.manager_id = create_user(
            self.conn, self.admin_id, "chef", "MotDePasseChef123", "responsable"
        )

    def test_authentification_et_mot_de_passe_hache(self):
        user = authenticate(self.conn, "CAISSE", "MotDePasseCaisse1")

        self.assertEqual((user["username"], user["role"]), ("caisse", "caissier"))
        row = self.conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (self.cashier_id,)
        ).fetchone()
        self.assertNotEqual(row["password_hash"], b"MotDePasseCaisse1")
        with self.assertRaises(AuthenticationError):
            authenticate(self.conn, "caisse", "mauvais mot de passe")

    def test_caissier_peut_vendre_mais_pas_remettre_ni_changer_les_prix(self):
        customer_id = create_customer(self.conn, "Client vendeur", "0555000004")
        sale = create_sale(
            self.conn, [Line(self.product_id)], [PaymentIn("ccp", 20_000)],
            customer_id=customer_id,
            actor_user_id=self.cashier_id,
        )
        actor = self.conn.execute(
            "SELECT actor_user_id FROM audit_log WHERE entity = 'sales' AND entity_id = ?",
            (sale.sale_id,),
        ).fetchone()
        self.assertEqual(actor["actor_user_id"], self.cashier_id)

        with self.assertRaises(PermissionDenied):
            create_sale(
                self.conn, [Line(self.product_id)], [], customer_id=customer_id,
                discount_centimes=100, actor_user_id=self.cashier_id,
            )
        with self.assertRaises(PermissionDenied):
            update_product(
                self.conn, self.product_id, price_centimes=25_000,
                actor_user_id=self.cashier_id,
            )

    def test_responsable_peut_gerer_les_prix_et_cloturer_mais_pas_les_comptes(self):
        changes = update_product(
            self.conn, self.product_id, price_centimes=25_000,
            actor_user_id=self.manager_id,
        )
        self.assertTrue(changes)
        session_id = open_session(
            self.conn, opening_centimes=10_000, actor_user_id=self.cashier_id
        )
        with self.assertRaises(PermissionDenied):
            close_session(
                self.conn, session_id, 10_000, actor_user_id=self.cashier_id
            )
        close_session(
            self.conn, session_id, 10_000, actor_user_id=self.manager_id
        )
        with self.assertRaises(PermissionDenied):
            create_user(
                self.conn, self.manager_id, "autre", "MotDePasseAutre1", "caissier"
            )

    def test_ne_peut_pas_supprimer_le_dernier_administrateur(self):
        with self.assertRaises(ValidationError):
            update_user(
                self.conn, self.admin_id, self.admin_id, active=False
            )

    def test_mot_de_passe_trop_court_est_refuse(self):
        with self.assertRaises(ValidationError):
            create_user(
                self.conn, self.admin_id, "court", "123", "caissier"
            )

    def test_recuperation_du_compte_administrateur(self):
        conn = new_db()
        recovery_code = "ABCD-EF01-2345-6789-ABCD-EF01-2345-6789"
        create_first_admin(
            conn, "ancien-admin", "MotDePasseAdmin1", recovery_code=recovery_code
        )

        with self.assertRaises(AuthenticationError):
            reset_admin_account(
                conn, "mauvais-code", "nouvel-admin", "NouveauMotDePasse1"
            )
        self.assertEqual(
            authenticate(conn, "ancien-admin", "MotDePasseAdmin1")["username"],
            "ancien-admin",
        )

        user = reset_admin_account(
            conn, recovery_code.lower(), "nouvel-admin", "NouveauMotDePasse1"
        )
        self.assertEqual(user["role"], "administrateur")
        self.assertEqual(
            authenticate(conn, "nouvel-admin", "NouveauMotDePasse1")["id"],
            user["id"],
        )
        with self.assertRaises(AuthenticationError):
            authenticate(conn, "ancien-admin", "MotDePasseAdmin1")

    def test_recuperation_refusee_sans_code_configure(self):
        with self.assertRaises(AuthenticationError):
            reset_admin_account(
                self.conn, "A" * 32, "nouvel-admin", "NouveauMotDePasse1"
            )


if __name__ == "__main__":
    unittest.main()

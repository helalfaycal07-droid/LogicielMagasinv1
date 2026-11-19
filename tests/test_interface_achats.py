import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.products import create_product
from magasin.stock import stock_of
from tests.helpers import new_db


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageAchatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.page_achats import PageAchats

        self.conn = new_db()
        self.product_id = create_product(
            self.conn, "ACH-001", "Produit réception", 100_000,
            cost_centimes=25_000, initial_stock=2
        )
        self.page = PageAchats(self.conn)

    def test_enregistrer_reception_met_a_jour_stock_et_historique(self):
        self.page.fournisseur.setText("Fournisseur Test")
        self.page.reference_fournisseur.setText("FAC-001")
        self.page.date_reception.setDate(QDate(2026, 11, 2))
        self.page.quantite.setValue(3)
        self.page.recherche.setText("ACH-001")
        self.page.ajouter_produit()
        self.assertEqual(self.page.panier[0]["quantity"], 3)
        self.assertEqual(self.page.panier[0]["unit_cost_centimes"], 25_000)
        self.page.tableau_panier.selectRow(0)
        self.page.charger_ligne_selectionnee()
        self.page.prix_vente.setText("1200")
        self.page.ajouter_produit()

        with (
            patch("interface.page_achats.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.enregistrer_reception()

        self.assertEqual(stock_of(self.conn, self.product_id), 5)
        self.assertEqual(self.page.panier, [])
        self.assertEqual(self.page.tableau_historique.rowCount(), 1)
        self.assertEqual(self.page.tableau_historique.item(0, 1).text(), "REC-000001")
        self.assertEqual(self.page.tableau_historique.item(0, 2).text(), "Fournisseur Test")
        self.assertEqual(self.page.tableau_historique.item(0, 3).text(), "FAC-001")
        self.assertEqual(self.page.tableau_historique.item(0, 5).text(), "3")
        self.assertEqual(
            self.conn.execute(
                "SELECT created_at FROM purchase_receipts WHERE number = 'REC-000001'"
            ).fetchone()["created_at"][:10],
            "2026-11-02",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT price_centimes FROM products WHERE id = ?", (self.product_id,)
            ).fetchone()["price_centimes"],
            120_000,
        )
        self.assertEqual(self.page.tableau_dettes.rowCount(), 1)
        self.assertEqual(self.page.tableau_dettes.item(0, 2).text(), "750,00 DA")
        self.assertEqual(self.page.tableau_dettes.item(0, 4).text(), "750,00 DA")

    def test_fournisseur_obligatoire_pour_enregistrer(self):
        self.page.panier = [{
            "product_id": self.product_id,
            "reference": "ACH-001",
            "name": "Produit réception",
            "quantity": 1,
            "imeis": None,
        }]
        with (
            patch("interface.page_achats.montrer_erreur") as montrer_erreur,
            patch("interface.page_achats.demander_confirmation") as confirmation,
        ):
            self.page.enregistrer_reception()

        montrer_erreur.assert_called_once()
        confirmation.assert_not_called()
        self.assertEqual(stock_of(self.conn, self.product_id), 2)

    def test_paiement_partiel_par_virement_cree_un_solde_a_regler(self):
        self.page.fournisseur.setText("Fournisseur Test")
        self.page.recherche.setText("ACH-001")
        self.page.ajouter_produit()
        self.page.paiement_maintenant.setText("100")
        self.page.moyen_paiement.setCurrentIndex(1)
        self.assertEqual(self.page.reste_a_payer.text(), "150,00 DA")

        with (
            patch("interface.page_achats.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.enregistrer_reception()

        self.assertEqual(self.page.tableau_dettes.rowCount(), 1)
        self.assertEqual(self.page.tableau_dettes.item(0, 3).text(), "100,00 DA")
        self.assertEqual(self.page.tableau_dettes.item(0, 4).text(), "150,00 DA")

    def test_modifier_ligne_met_a_jour_quantite_et_prix(self):
        self.page.recherche.setText("ACH-001")
        self.page.ajouter_produit()
        self.page.tableau_panier.selectRow(0)
        self.page.charger_ligne_selectionnee()
        self.page.quantite.setValue(4)
        self.page.cout_unitaire.setText("300")
        self.page.prix_vente.setText("1500")
        self.page.ajouter_produit()

        self.assertEqual(self.page.panier[0]["quantity"], 4)
        self.assertEqual(self.page.panier[0]["unit_cost_centimes"], 30_000)
        self.assertEqual(self.page.panier[0]["unit_price_centimes"], 150_000)

    def test_dette_globale_fournisseur_regroupe_plusieurs_receptions(self):
        for quantity in (1, 2):
            self.page.fournisseur.setText("Fournisseur global")
            self.page.quantite.setValue(quantity)
            self.page.recherche.setText("ACH-001")
            self.page.ajouter_produit()
            with (
                patch("interface.page_achats.demander_confirmation", return_value=True),
                patch.object(QMessageBox, "information"),
            ):
                self.page.enregistrer_reception()
        self.assertEqual(self.page.tableau_dettes.rowCount(), 1)
        self.assertEqual(self.page.tableau_dettes.item(0, 2).text(), "750,00 DA")
        self.assertEqual(self.page.tableau_dettes.item(0, 4).text(), "750,00 DA")

    def test_reglement_partiel_selectionne_met_a_jour_la_dette(self):
        self.page.fournisseur.setText("Fournisseur Test")
        self.page.recherche.setText("ACH-001")
        self.page.ajouter_produit()
        with (
            patch("interface.page_achats.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.enregistrer_reception()
        self.page.tableau_dettes.selectRow(0)

        with (
            patch(
                "interface.page_achats.QInputDialog.getText",
                return_value=("50", True),
            ),
            patch(
                "interface.page_achats.QInputDialog.getItem",
                return_value=("Chèque", True),
            ),
            patch.object(QMessageBox, "information"),
        ):
            self.page.regler_reception_selectionnee()

        self.assertEqual(self.page.tableau_dettes.item(0, 3).text(), "50,00 DA")
        self.assertEqual(self.page.tableau_dettes.item(0, 4).text(), "200,00 DA")

    def test_ajouter_produit_accepte_reference_sans_distinguer_majuscules(self):
        self.page.recherche.setText("ach-001")

        self.page.ajouter_produit()

        self.assertEqual(len(self.page.panier), 1)
        self.assertEqual(self.page.panier[0]["product_id"], self.product_id)

    def test_nouveau_produit_est_cree_et_ajoute_au_stock_a_la_reception(self):
        from unittest.mock import Mock

        valeurs = {
            "reference": "NOUV-001",
            "name": "Nouveau produit",
            "price_centimes": 125_000,
            "cost_centimes": 90_000,
            "barcode": None,
            "category": None,
            "brand": None,
            "tax_rate_bp": 1900,
            "price_includes_tax": True,
            "min_stock": 0,
            "initial_stock": 0,
            "track_serial": False,
        }
        dialogue = Mock()
        dialogue.Accepted = QDialog.DialogCode.Accepted
        dialogue.valeurs = valeurs
        self.page.recherche.setText("NOUV-001")
        self.page.quantite.setValue(4)

        with (
            patch("interface.page_achats.NouveauProduitDialogue", return_value=dialogue),
            patch("interface.page_achats.demander_confirmation", return_value=True),
            patch("interface.page_achats.ouvrir_dialogue", return_value=dialogue.Accepted),
        ):
            self.page.ajouter_produit()

        self.assertEqual(len(self.page.panier), 1)
        self.assertIsNone(self.page.panier[0]["product_id"])
        self.assertIsNotNone(self.page.panier[0]["new_product"])
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM products WHERE reference = 'NOUV-001'"
        ).fetchone())

        self.page.fournisseur.setText("Fournisseur Test")
        with (
            patch("interface.page_achats.demander_confirmation", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            self.page.enregistrer_reception()

        produit = self.conn.execute(
            "SELECT id FROM products WHERE reference = 'NOUV-001'"
        ).fetchone()
        self.assertIsNotNone(produit)
        self.assertEqual(stock_of(self.conn, produit["id"]), 4)
        self.assertEqual(
            self.conn.execute(
                "SELECT total_centimes FROM purchase_receipts WHERE number = 'REC-000001'"
            ).fetchone()["total_centimes"],
            360_000,
        )

    def test_validation_desactivee_si_fournisseur_manquant(self):
        self.page.recherche.setText("ACH-001")
        self.page.ajouter_produit()

        self.assertFalse(self.page.bouton_enregistrer.isEnabled())
        self.page.fournisseur.setText("Fournisseur Test")
        self.assertTrue(self.page.bouton_enregistrer.isEnabled())

    def test_annuler_creation_najoute_pas_de_produit(self):
        self.page.recherche.setText("REF-INCONNUE")
        with patch("interface.page_achats.demander_confirmation", return_value=False) as confirmer:
            self.page.ajouter_produit()

        confirmer.assert_called_once()
        self.assertEqual(self.page.panier, [])
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM products WHERE reference = 'REF-INCONNUE'"
        ).fetchone())


if __name__ == "__main__":
    unittest.main()

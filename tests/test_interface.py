"""Tests de l'écran Produits et stock (sans écran physique). Ignorés si PySide6 n'est pas installé."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog
    PYSIDE_OK = True
except ImportError:  # pragma: no cover
    PYSIDE_OK = False

from magasin.db import connect
from magasin.demo_data import remplir_demo


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class PageProduitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.fenetre_principale import FenetrePrincipale
        self.conn = connect(":memory:")
        remplir_demo(self.conn)
        self.fenetre = FenetrePrincipale(self.conn, "Test")
        self.page = self.fenetre.page_produits

    def test_la_liste_affiche_tous_les_produits(self):
        self.assertEqual(self.page.tableau.rowCount(), 6)

    def test_le_stock_negatif_reste_visible(self):
        textes = [self.page.tableau.item(r, 6).text() for r in range(self.page.tableau.rowCount())]
        self.assertIn("-1", textes)
        self.assertIn("négatif", self.page.resume.text())

    def test_recherche_par_nom_et_par_code_barres(self):
        self.page.recherche.setText("frigo")
        self.assertEqual(self.page.tableau.rowCount(), 0)
        self.page.recherche.setText("réfrigérateur")
        self.assertEqual(self.page.tableau.rowCount(), 1)
        self.page.recherche.setText("6130000000103")
        self.assertEqual(self.page.tableau.rowCount(), 1)

    def test_filtre_stock_bas(self):
        self.page.stock_bas.setChecked(True)
        references = {self.page.tableau.item(r, 0).text() for r in range(self.page.tableau.rowCount())}
        self.assertEqual(references, {"102", "105"})  # 101 a 3 en stock pour un minimum de 2 : pas bas

    def test_boutons_inactifs_sans_selection(self):
        self.assertFalse(self.page.bouton_inventaire.isEnabled())
        self.page.tableau.selectRow(0)
        self.assertTrue(self.page.bouton_inventaire.isEnabled())

    def test_le_menu_ne_propose_que_les_ecrans_termines(self):
        from PySide6.QtCore import Qt
        actifs = [self.fenetre.menu.item(i).text() for i in range(self.fenetre.menu.count())
                  if self.fenetre.menu.item(i).flags() & Qt.ItemIsEnabled]
        self.assertEqual(actifs, ["Produits et stock"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class BoutonsTests(unittest.TestCase):
    """Un clic sur chaque bouton ouvre bien sa fenêtre, au premier plan."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_les_boutons_ouvrent_leurs_fenetres(self):
        from PySide6.QtCore import Qt, QTimer
        from interface.fenetre_principale import FenetrePrincipale
        conn = connect(":memory:")
        remplir_demo(conn)
        fenetre = FenetrePrincipale(conn, "Test")
        fenetre.show()
        page = fenetre.page_produits
        page.tableau.selectRow(0)
        ouvertes = []

        def fermer():
            # (sans écran, Qt ne marque pas la fenêtre « active » : on cherche les dialogues visibles)
            visibles = [w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]
            for dialogue in visibles:
                ouvertes.append((type(dialogue).__name__, bool(dialogue.windowFlags() & Qt.WindowStaysOnTopHint)))
                dialogue.reject()
            if not visibles:
                ouvertes.append(("aucune", False))

        for bouton in (page.bouton_nouveau, page.bouton_inventaire, page.bouton_mouvements):
            QTimer.singleShot(150, fermer)
            bouton.click()
        self.assertEqual([nom for nom, _ in ouvertes],
                         ["NouveauProduitDialogue", "InventaireDialogue", "MouvementsDialogue"])
        self.assertTrue(all(premier_plan for _, premier_plan in ouvertes))


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class ModificationInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.fenetre_principale import FenetrePrincipale
        self.conn = connect(":memory:")
        remplir_demo(self.conn)
        self.fenetre = FenetrePrincipale(self.conn, "Test")  # à garder en mémoire pendant le test
        self.page = self.fenetre.page_produits

    def ligne_de(self, reference):
        for r in range(self.page.tableau.rowCount()):
            if self.page.tableau.item(r, 0).text() == reference:
                return r

    def test_corriger_un_prix_depuis_la_fenetre(self):
        from interface.dialogues import ModifierProduitDialogue
        self.page.tableau.selectRow(self.ligne_de("101"))
        produit = self.page._produit_selectionne()
        dialogue = ModifierProduitDialogue(self.page, produit)
        self.assertEqual(dialogue.prix.text(), "39000")          # pré-rempli
        self.assertEqual(dialogue.reference.text(), "101")
        self.assertFalse(dialogue.avec_stock_initial)             # le stock ne se change pas ici
        dialogue.prix.setText("38 000")
        dialogue._valider()
        self.assertEqual(dialogue.valeurs["price_centimes"], 3_800_000)
        self.page.appliquer_modification(produit["id"], dialogue.valeurs)
        ligne = self.ligne_de("101")
        self.assertEqual(self.page.tableau.item(ligne, 2).text(), "38 000,00 DA")
        self.assertEqual(self.page.tableau.item(ligne, 6).text(), "3")   # stock inchangé
        self.assertEqual(self.page._produit_selectionne()["reference"], "101")  # toujours sélectionné

    def test_le_bouton_modifier_ouvre_sa_fenetre(self):
        from PySide6.QtCore import QTimer
        self.page.tableau.selectRow(0)
        vues = []

        def fermer():
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible():
                    vues.append(type(w).__name__)
                    w.reject()
        QTimer.singleShot(150, fermer)
        self.page.bouton_modifier.click()
        self.assertEqual(vues, ["ModifierProduitDialogue"])


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class ImeiInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from interface.fenetre_principale import FenetrePrincipale
        self.conn = connect(":memory:")
        remplir_demo(self.conn)
        self.fenetre = FenetrePrincipale(self.conn, "Test")
        self.page = self.fenetre.page_produits

    def choisir(self, reference):
        for r in range(self.page.tableau.rowCount()):
            if self.page.tableau.item(r, 0).text() == reference:
                self.page.tableau.selectRow(r)
                return self.page._produit_selectionne()

    def test_boutons_selon_le_type_de_produit(self):
        self.choisir("106")                                   # smartphone suivi par IMEI
        self.assertTrue(self.page.bouton_appareils.isEnabled())
        self.assertTrue(self.page.bouton_inventaire.isEnabled())   # « Ajuster le stock » reste utilisable
        self.assertEqual(self.page.tableau.item(self.page.tableau.currentRow(), 7).text(), "IMEI")
        self.choisir("104")                                   # simple câble
        self.assertFalse(self.page.bouton_appareils.isEnabled())
        self.assertTrue(self.page.bouton_inventaire.isEnabled())

    def test_ajuster_le_stock_d_un_telephone_ouvre_la_saisie_des_imei(self):
        from PySide6.QtCore import QTimer
        self.choisir("106")
        vues = []

        def fermer():
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible():
                    vues.append(type(w).__name__)
                    w.reject()
        QTimer.singleShot(150, fermer)
        self.page.bouton_inventaire.click()
        self.assertEqual(vues, ["AppareilsDialogue"])

    def test_liste_collee_de_plusieurs_telephones(self):
        from interface.dialogues_imei import AppareilsDialogue
        from magasin.serials import complete_imei
        from magasin.stock import stock_of
        produit = self.choisir("106")
        avant = stock_of(self.conn, produit["id"])
        d = AppareilsDialogue(self.page, self.conn, produit)
        lignes = [f"{complete_imei(f'35666600{n:06d}')}\t{complete_imei(f'35666600{n + 100:06d}')}" for n in (1, 2, 3)]
        d.zone_liste.setPlainText("\n".join(lignes) + "\n\n")
        self.assertTrue(d.ajouter_liste_collee())
        self.assertEqual(d.bouton_enregistrer.text(), "Enregistrer 3 appareils")
        self.assertTrue(d.enregistrer())
        self.assertEqual(stock_of(self.conn, produit["id"]), avant + 3)

    def test_liste_collee_avec_une_ligne_fausse_n_ajoute_rien(self):
        from interface.dialogues_imei import AppareilsDialogue
        from magasin.serials import complete_imei
        produit = self.choisir("106")
        d = AppareilsDialogue(self.page, self.conn, produit)
        bonne = complete_imei("35666600000001")
        d.zone_liste.setPlainText(f"{bonne}\n12345 67890\n")
        self.assertFalse(d.ajouter_liste_collee())
        self.assertIn("Ligne 2", d.message_liste.text())
        self.assertEqual(d.en_attente, [])
        existant = complete_imei("35899900000001")            # déjà vendu dans la démonstration
        d.zone_liste.setPlainText(f"{bonne}\n{existant}")
        self.assertFalse(d.ajouter_liste_collee())
        self.assertIn("Ligne 2", d.message_liste.text())
        self.assertIn("existe déjà", d.message_liste.text())
        d.zone_liste.setPlainText(f"{bonne}\n{bonne}")           # doublon dans la liste elle-même
        self.assertFalse(d.ajouter_liste_collee())
        self.assertEqual(d.en_attente, [])

    def test_entree_de_deux_telephones_depuis_la_fenetre(self):
        from interface.dialogues_imei import AppareilsDialogue
        from magasin.serials import complete_imei
        from magasin.stock import stock_of
        produit = self.choisir("106")
        avant = stock_of(self.conn, produit["id"])
        d = AppareilsDialogue(self.page, self.conn, produit)
        a1, a2 = complete_imei("35777700000001"), complete_imei("35777700000002")
        d.imei1.setText(a1)
        d.imei2.setText(a2)
        self.assertTrue(d.ajouter_a_la_liste())
        d.une_sim.setChecked(True)                            # second téléphone : une seule SIM
        d.imei1.setText(complete_imei("35777700000003"))
        self.assertTrue(d.ajouter_a_la_liste())
        self.assertEqual(d.bouton_enregistrer.text(), "Enregistrer 2 appareils")
        self.assertTrue(d.enregistrer())
        self.assertEqual(stock_of(self.conn, produit["id"]), avant + 2)
        self.assertTrue(d.modifie)

    def test_les_fautes_de_saisie_sont_signalees_sans_fenetre_bloquante(self):
        from interface.dialogues_imei import AppareilsDialogue
        from magasin.serials import complete_imei
        produit = self.choisir("106")
        d = AppareilsDialogue(self.page, self.conn, produit)
        d.imei1.setText("123")
        self.assertFalse(d.ajouter_a_la_liste())
        self.assertIn("15 chiffres", d.message.text())
        existant = complete_imei("35899900000001")             # déjà vendu dans la démonstration
        d.imei1.setText(existant)
        d.imei2.setText(complete_imei("35777700000009"))
        self.assertFalse(d.ajouter_a_la_liste())
        self.assertIn("existe déjà", d.message.text())

    def test_recherche_d_un_imei_montre_le_client_et_la_vente(self):
        from interface.dialogues_imei import RechercheImeiDialogue
        from magasin.serials import complete_imei
        d = RechercheImeiDialogue(self.page, self.conn, complete_imei("35899900000501"))  # IMEI2 de l'appareil vendu
        self.assertEqual(len(d.lignes), 1)
        self.assertEqual((d.lignes[0]["customer_name"], d.lignes[0]["sale_number"], d.lignes[0]["status"]),
                         ("Client Démo", "VTE-000001", "vendu"))

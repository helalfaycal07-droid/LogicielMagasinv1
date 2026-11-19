"""Tests de l'écran Produits et stock (sans écran physique). Ignorés si PySide6 n'est pas installé."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QEventLoop, QThreadPool, QTimer
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

    def test_navigation_pagine_le_tableau_et_actualise_l_indicateur(self):
        from magasin.products import create_product

        for index in range(55):
            create_product(
                self.conn, f"PAGE-{index:03}", f"Article paginé {index:03}", 100_000
            )
        self.page.page_size = 10
        self.page.rafraichir()

        self.assertEqual(self.page.tableau.rowCount(), 10)
        self.assertIn("Page 1 / 7", self.page.indicateur_page.text())
        self.assertFalse(self.page.bouton_precedent.isEnabled())
        self.assertTrue(self.page.bouton_suivant.isEnabled())

        self.page.page_suivante()

        self.assertEqual(self.page.tableau.rowCount(), 10)
        self.assertIn("Page 2 / 7", self.page.indicateur_page.text())
        self.assertTrue(self.page.bouton_precedent.isEnabled())

    def test_base_fichier_est_chargee_hors_du_thread_interface(self):
        from interface.page_produits import PageProduits
        from magasin.products import create_product, stock_report_page

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pagination.db"
            conn = connect(str(path))
            create_product(conn, "ASYNC-1", "Produit asynchrone", 100_000)

            def slow_report(*args, **kwargs):
                time.sleep(0.1)
                return stock_report_page(*args, **kwargs)

            with patch("interface.page_produits.stock_report_page", slow_report):
                page = PageProduits(conn)
                self.assertEqual(page.indicateur_page.text(), "Chargement…")
                self.assertEqual(len(page._chargements), 1)

                loop = QEventLoop()
                timeout = QTimer()
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)
                polling = QTimer()
                polling.timeout.connect(
                    lambda: loop.quit()
                    if page.indicateur_page.text() != "Chargement…"
                    else None
                )
                polling.start(10)
                timeout.start(3000)
                loop.exec()
                polling.stop()

            try:
                self.assertNotEqual(page.indicateur_page.text(), "Chargement…")
                self.assertIn("Page 1 / 1", page.indicateur_page.text())
                self.assertEqual(page.tableau.rowCount(), 1)
                self.assertTrue(QThreadPool.globalInstance().waitForDone(3000))
            finally:
                page.close()
                page.deleteLater()
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)
                conn.close()

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

    def test_le_menu_active_les_ecrans_termines(self):
        from PySide6.QtCore import Qt
        actifs = [self.fenetre.menu.item(i).text() for i in range(self.fenetre.menu.count())
                  if self.fenetre.menu.item(i).flags() & Qt.ItemIsEnabled]
        self.assertEqual(
            actifs,
            ["Produits et stock", "Vente rapide", "Retours", "Caisse", "Clients et crédit",
             "Achats", "Rapports", "Sauvegarde", "Utilisateurs", "Fournisseurs", "Devis"],
        )

    def test_role_caissier_masque_les_ecrans_et_remises_reserves(self):
        from PySide6.QtCore import Qt
        from interface.fenetre_principale import FenetrePrincipale
        from magasin.users import authenticate, create_first_admin, create_user

        admin_id = create_first_admin(self.conn, "admin-ui", "MotDePasseAdminUI")
        cashier_id = create_user(
            self.conn, admin_id, "caisse-ui", "MotDePasseCaisseUI", "caissier"
        )
        user = authenticate(self.conn, "caisse-ui", "MotDePasseCaisseUI")
        self.assertEqual(user["id"], cashier_id)
        fenetre = FenetrePrincipale(self.conn, "Test rôle", current_user=user)
        actifs = {
            fenetre.menu.item(i).text()
            for i in range(fenetre.menu.count())
            if fenetre.menu.item(i).flags() & Qt.ItemIsEnabled
        }

        self.assertEqual(
            actifs,
            {"Produits et stock", "Vente rapide", "Caisse", "Clients et crédit", "Devis"},
        )
        self.assertFalse(fenetre.page_vente_rapide.remise.isEnabled())

    def test_vente_rapide_affiche_la_page_associee(self):
        from interface.page_vente_rapide import PageVenteRapide
        self.fenetre.menu.setCurrentRow(1)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageVenteRapide)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_vente_rapide)

    def test_caisse_affiche_la_page_associee(self):
        from interface.page_caisse import PageCaisse
        self.fenetre.menu.setCurrentRow(3)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageCaisse)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_caisse)

    def test_clients_affiche_la_page_associee(self):
        from interface.page_clients import PageClients
        self.fenetre.menu.setCurrentRow(4)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageClients)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_clients)

    def test_achats_affiche_la_page_associee(self):
        from interface.page_achats import PageAchats
        self.fenetre.menu.setCurrentRow(5)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageAchats)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_achats)

    def test_fournisseurs_affiche_la_page_associee(self):
        from interface.page_fournisseurs import PageFournisseurs
        self.fenetre.menu.setCurrentRow(9)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageFournisseurs)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_fournisseurs)

    def test_devis_affiche_la_page_associee(self):
        from interface.page_devis import PageDevis
        self.fenetre.menu.setCurrentRow(10)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageDevis)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_devis)

    def test_rapports_affiche_la_page_associee(self):
        from interface.page_rapports import PageRapports
        self.fenetre.menu.setCurrentRow(6)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageRapports)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_rapports)

    def test_sauvegarde_affiche_la_page_associee(self):
        from interface.page_sauvegarde import PageSauvegarde
        self.fenetre.menu.setCurrentRow(7)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageSauvegarde)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_sauvegarde)

    def test_restauration_recree_les_ecrans_pour_eviter_les_formulaires_perimes(self):
        page_precedente = self.fenetre.page_vente_rapide
        self.fenetre.menu.setCurrentRow(7)

        self.fenetre._apres_restauration()

        self.assertIsNot(self.fenetre.page_vente_rapide, page_precedente)
        self.assertEqual(self.fenetre.pile.count(), self.fenetre.menu.count())
        self.assertEqual(self.fenetre.pile.currentIndex(), 0)

    def test_retours_affiche_la_page_associee(self):
        from interface.page_retours import PageRetours
        self.fenetre.menu.setCurrentRow(2)
        self.assertIsInstance(self.fenetre.pile.currentWidget(), PageRetours)
        self.assertIs(self.fenetre.pile.currentWidget(), self.fenetre.page_retours)


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
        dialogue = ModifierProduitDialogue(self.page, produit, self.conn)
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


@unittest.skipUnless(PYSIDE_OK, "PySide6 n'est pas installé")
class SuppressionInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import interface.page_produits as module
        from interface.fenetre_principale import FenetrePrincipale
        self.module = module
        self.ancienne_confirmation = module.demander_confirmation
        self.questions = []
        module.demander_confirmation = lambda parent, titre, texte: (self.questions.append((titre, texte)) or True)
        self.conn = connect(":memory:")
        remplir_demo(self.conn)
        self.fenetre = FenetrePrincipale(self.conn, "Test")
        self.page = self.fenetre.page_produits

    def tearDown(self):
        self.module.demander_confirmation = self.ancienne_confirmation

    def choisir(self, reference):
        for r in range(self.page.tableau.rowCount()):
            if self.page.tableau.item(r, 0).text() == reference:
                self.page.tableau.selectRow(r)
                return

    def references(self):
        return {self.page.tableau.item(r, 0).text() for r in range(self.page.tableau.rowCount())}

    def test_un_produit_sans_historique_est_supprime_pour_de_bon(self):
        self.choisir("102")                                   # téléviseur sans vente
        self.page.bouton_supprimer.click()
        self.assertEqual(self.questions[0][0], "Supprimer le produit")
        self.assertNotIn("102", self.references())
        self.page.voir_archives.setChecked(True)               # il n'est pas non plus dans les archivés
        self.assertEqual(self.references(), set())

    def test_un_produit_vendu_est_archive_puis_restaure(self):
        self.choisir("106")                                   # smartphone déjà vendu dans la démonstration
        self.page.bouton_supprimer.click()
        self.assertEqual(self.questions[0][0], "Retirer de la liste")
        self.assertIn("historique", self.questions[0][1])
        self.assertNotIn("106", self.references())
        self.page.voir_archives.setChecked(True)
        self.assertEqual(self.references(), {"106"})
        self.assertEqual(self.page.bouton_supprimer.text(), "Restaurer")
        self.choisir("106")
        self.assertFalse(self.page.bouton_modifier.isEnabled())   # archivé : consultation seulement
        self.assertTrue(self.page.bouton_mouvements.isEnabled())
        self.page.bouton_supprimer.click()                      # Restaurer
        self.assertEqual(self.references(), set())
        self.page.voir_archives.setChecked(False)
        self.assertIn("106", self.references())

    def test_refus_de_la_confirmation_ne_change_rien(self):
        self.module.demander_confirmation = lambda *a, **k: False
        self.choisir("102")
        self.page.bouton_supprimer.click()
        self.assertIn("102", self.references())

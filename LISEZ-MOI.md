# Logiciel de gestion de magasin (version 0.3.2)

Le **cœur du logiciel** (hors ligne, Python seul) et une **première fenêtre** : l'écran Produits et stock.
Les fenêtres demandent PySide6 (installé automatiquement par test_fenetre.py).

## Ce qui est déjà fait et testé (107 tests)
- Montants exacts en centimes, affichage « 39 000,00 DA », répartition des mensualités
  (la dernière absorbe l'arrondi), calcul HT / TVA / TTC, majoration et marge séparées.
- Base SQLite locale avec migrations, numéros lisibles (VTE-000001, REC-000001, DOS-00001...).
- **Stock = somme des mouvements** (jamais un champ modifié à la main), stock négatif visible,
  réglage « avertir / bloquer / autoriser » quand le stock est insuffisant.
- **Ventes** en une seule transaction : vente, lignes, stock, caisse, paiements, rendu de monnaie.
- **Caisse** : session obligatoire pour les espèces, ouverture reprise de la clôture précédente,
  dépenses, écart exigeant une raison écrite, piste d'audit avec solde avant et après.
- **Clients et crédit** : dette calculée (jamais saisie), plafond, statuts, reprise de dette,
  « Qui me doit de l'argent ? ».
- **Échéancier** de 1 à N mensualités, imputation sur les échéances les plus anciennes, retards calculés.
- **Chèques** : un par mensualité, statuts (en dossier, remis à la banque, encaissé, impayé,
  à restituer, restitué). L'encaissement crée le versement ; un chèque impayé met le client « en retard ».
- **Suivi des smartphones par IMEI1 et IMEI2** : contrôle du numéro (15 chiffres + chiffre de contrôle),
  un IMEI ne peut exister qu'une fois, entrée des appareils un par un, vente d'un appareil précis
  (scan de l'IMEI1 ou de l'IMEI2), recherche d'un IMEI (produit, état, vente, client), sortie du stock avec raison,
  **collage d'une liste d'IMEI** (une ligne par téléphone, depuis Excel ou un message du fournisseur).
  Le bouton « Ajuster le stock » ouvre l'entrée des téléphones pour un produit suivi par IMEI.
  Le stock d'un produit suivi par IMEI égale toujours le nombre d'appareils en stock.
- Contrôle d'intégrité (stock, caisse, IMEI).
- **Écran Produits et stock** : liste avec recherche, stock bas ou négatif en couleur, nouveau produit
  (prix TTC/HT, majoration et marge en direct), **modification d'un produit** (chaque changement est
  écrit au journal ; le stock se corrige par l'inventaire), inventaire, mouvements de stock, suppression d'un produit créé par erreur (définitive), ou retrait de la liste (archivage, restaurable) si
  le produit a déjà un historique.

## Ce qui n'est pas encore fait
Écrans vente rapide, caisse, clients et crédit, achats et réceptions, retours et avoirs, documents imprimables (ticket, facture,
devis), étiquette de dossier, sauvegarde et restauration, import de l'Excel actuel, suivi IMEI.

## Lancer avec VS Code (bouton ▶ sur le fichier)
- `app_demo.py` : le logiciel avec des données FICTIVES, rien n'est enregistré (à essayer en premier).
- `app.py` : le logiciel avec vos données (base dans `%LOCALAPPDATA%\LogicielMagasin\magasin.db`).
- `lancer_tests.py` : lance les 107 tests.  `demo.py` : scénario en texte.  `test_fenetre.py` : test de PySide6.

## Lancer avec PowerShell (facultatif)
```powershell
cd "$HOME\Desktop\LogicielMagasin"
python --version
python -m unittest discover -s tests -v
python demo.py
```
Si `python` n'est pas reconnu, essayez `py` à la place (`py -m unittest discover -s tests -v`).
Version de Python conseillée : 3.11 ou 3.12, en 64 bits.

## Organisation
- `magasin/` : le code métier (un fichier par sujet : money, db, stock, products, customers, cash, sales, credit, integrity).
- `tests/` : les tests, tirés des cas chiffrés de l'essai MekaStore.
- `demo.py` : rejoue un scénario complet avec des données fictives.

## Règles à retenir
- Tous les montants sont des entiers en centimes (1 DA = 100 centimes).
- L'heure utilisée est toujours celle d'Algérie (UTC+1), même si le fuseau de Windows est mal réglé.
- Aucune donnée réelle dans les tests ni dans la démonstration.

## En cas d'erreur
Une erreur dans un bouton affiche maintenant un message à l'écran et s'écrit dans
`%LOCALAPPDATA%\LogicielMagasin\journal_erreurs.txt`. Envoyez ce texte à Claude.

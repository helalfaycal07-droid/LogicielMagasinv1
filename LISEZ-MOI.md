# Logiciel de gestion de magasin (version 0.3.3)

Le logiciel fonctionne hors ligne avec une base SQLite locale. Les fenêtres demandent PySide6.

## Fonctionnalités déjà réalisées et testées
- Montants exacts en centimes, affichage « 39 000,00 DA », répartition des mensualités
  (la dernière absorbe l'arrondi), calcul HT / TVA / TTC, majoration et marge séparées.
- Base SQLite locale avec migrations, numéros lisibles (VTE-000001, REC-000001, DOS-00001...).
- **Stock = somme des mouvements** (jamais un champ modifié à la main), stock négatif visible,
  réglage « avertir / bloquer / autoriser » quand le stock est insuffisant.
- **Ventes** en une seule transaction : vente, lignes, stock, caisse, paiements, rendu de monnaie.
- **Devis** : devis enregistrés valables 30 jours, prix et TVA conservés, impression A4,
  annulation ou chargement en caisse. Ils ne réservent pas le stock ; la conversion en vente
  est contrôlée et ne peut se faire qu'une fois.
- **Caisse** : session obligatoire pour les espèces, ouverture reprise de la clôture précédente,
  dépenses, écart exigeant une raison écrite, piste d'audit avec solde avant et après.
  Le bouton « Réinitialiser le solde à 0 » prépare une nouvelle ouverture à zéro sans effacer
  les clôtures précédentes.
- **Clients et crédit** : dette calculée (jamais saisie), plafond, statuts, reprise de dette,
  « Qui me doit de l'argent ? ». Une fiche client sans historique peut être supprimée ; si elle
  est liée à des ventes, paiements ou crédits, elle est archivée pour préserver la comptabilité.
  Impression d'étiquette de dossier 80 × 50 mm avec le nom, le numéro de dossier et le téléphone.
- **Fidélité client** : une vente validée associée à un client rapporte 1 point par tranche complète
  de 100 DA nette ; 1 point vaut 1 DA à la caisse. Les points gagnés expirent après 18 mois.
  Les points utilisés sont déduits dans la même transaction que la vente ; un retour annule les
  points gagnés au prorata et restitue les points utilisés si ceux-ci n'ont pas expiré.
  Le solde apparaît dans la fiche client et à la caisse. Les ventes antérieures à l'activation ne
  reçoivent aucun point rétroactif ; la migration ajoute uniquement le journal de fidélité et le
  compteur de points utilisés, sans réécrire les stocks ni les historiques existants.
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
  le produit a déjà un historique. La liste est paginée par 50 produits, et les chargements SQLite
  sont exécutés en arrière-plan pour éviter de bloquer la fenêtre.
  La fenêtre « Modifier le produit » permet aussi de choisir un fournisseur existant ou de retirer
  l'association ; les changements au produit et au fournisseur sont enregistrés atomiquement.
- **Import Excel** : aperçu de la feuille `SUIVI_STOCK` des classeurs `.xlsx` ou `.xlsm`, avec
  exclusion visible des doublons, des références déjà présentes et des lignes incomplètes. Le stock
  restant devient un stock de départ ; les ventes passées ne sont pas recréées. Les prix sont TTC
  selon la TVA par défaut du logiciel. Les fournisseurs, dates, IMEI et notes ne sont pas importés.
  Les macros ne sont jamais exécutées et le classeur source n'est jamais modifié.
- **Rattachement fournisseurs-produits** : action distincte de l'import Excel classique, à partir
  des colonnes `CODE PRODUIT` et `FOURNISSEUR` de `SUIVI_STOCK`. L'aperçu signale les références
  inconnues, doublons et fiches fournisseurs manquantes ou archivées. Seules les références exactes
  et les fiches fournisseurs déjà créées sont associées après confirmation. Cette opération ne
  modifie ni les produits, ni les stocks, ni les ventes, ni les réceptions passées. Les associations
  sont ensuite visibles dans la colonne « Fournisseur(s) » de la liste des produits.
- **Achats — réceptions fournisseur** : saisie du fournisseur, de sa référence de pièce et de la
  date de réception, ajout des quantités au stock avec un numéro REC. Les produits existants peuvent
  être sélectionnés par référence, code-barres ou désignation ; le coût d'achat et le prix de vente
  de la fiche sont actualisés depuis la réception. Le coût de chaque réception reste conservé
  séparément pour calculer son dû. Les lignes du panier peuvent être modifiées ou supprimées avant
  validation ; une réception enregistrée peut être corrigée ou annulée avec ajustement du stock.
  Les appareils suivis par IMEI restent tracés individuellement. Une référence inconnue permet de créer la fiche
  produit directement pendant la réception ; le produit et sa quantité ne sont enregistrés qu'à sa
  validation. Les montants versé et restant dû sont calculés avant validation. Les dettes sont
  regroupées par fournisseur ; les versements ultérieurs en espèces, virement ou chèque sont répartis
  sur les réceptions les plus anciennes. Les acomptes des réceptions annulées deviennent un crédit
  fournisseur. Les paiements en espèces sont inscrits
  comme dépenses de caisse et nécessitent une session ouverte. Les reçus créés avant l'activation du
  suivi des soldes fournisseurs restent consultables, mais leur ancien coût n'est pas reconstitué.
- **Répertoire fournisseurs** : fiches avec téléphone, e-mail, adresse et notes, historique des
  réceptions et synthèse des achats, paiements et dettes/crédits nets. Les fournisseurs archivés restent
  liés à leurs écritures ; les réceptions existantes sont rattachées lors de la migration.
- **Rapports essentiels** : chiffres d'affaires et nombre de ventes par période, produits vendus,
  paiements nets par moyen, retours de la période et chiffre d'affaires net, valorisation du stock
  actuel et clôtures de caisse. Bénéfice brut regroupé par mois ou année, calculé avec le coût
  d'achat enregistré au moment de la vente et ajusté selon les retours remis en stock. Les ventes
  historiques sans coût connu sont signalées comme incomplètes ; charges et impôts ne sont pas inclus.
  Chaque tableau peut être exporté en CSV pour Excel.
- **Sauvegarde et restauration** : copie cohérente de la base SQLite, contrôle d'intégrité et de
  compatibilité avant restauration, copie automatique de l'état précédent et confirmation avant remplacement.
  Depuis cet écran, un administrateur peut aussi réinitialiser la caisse et l'historique commercial
  après confirmation et saisie de son mot de passe. Les ventes, retours, paiements, créances,
  dettes, points de fidélité et mouvements de caisse sont effacés ; les produits, leur stock,
  les clients, les fournisseurs et les utilisateurs sont conservés.
  Au démarrage, une base existante est également sauvegardée et vérifiée avant une migration de schéma ;
  les copies préalables sont conservées dans `backups` à côté de la base et les mises à jour refusent
  une base issue d'une version plus récente du logiciel. À la fermeture, l'administrateur peut choisir
  de créer une sauvegarde et son emplacement, de quitter sans sauvegarder ou de rester dans le logiciel.
  Si la sauvegarde échoue, le logiciel reste ouvert ; les comptes sans permission de sauvegarde ne
  voient pas cette demande.
- **Retours, remboursements et avoirs** : retours partiels ou totaux, remboursement en espèces
  (avec caisse ouverte) ou émission d'un avoir client. Un retour sur une vente à crédit réduit d'abord
  la dette et les échéances ; seul le solde est remboursé ou crédité. Les articles ne sont remis en
  stock que s'ils sont déclarés revendables ; les IMEI retournés sont vérifiés. Un échange se fait
  comme un retour suivi d'une nouvelle vente.
- **Utilisateurs et permissions** : création du premier compte administrateur à l'ouverture, connexion
  par identifiant et mot de passe haché, puis gestion des comptes Caissier / Responsable /
  Administrateur. Les actions sensibles sont contrôlées dans le métier et attribuées à l'utilisateur
  dans le journal ; le dernier administrateur actif ne peut pas être désactivé. À la première
  ouverture, créez un identifiant et un mot de passe d'au moins 10 caractères ; aucun accès par
  défaut n'est installé. Un code de secours est affiché après la création du premier administrateur :
  conservez-le hors du logiciel. Depuis la fenêtre de connexion, « Identifiant ou mot de passe oublié ? »
  permet de remplacer l'identifiant et le mot de passe administrateur avec ce code. Un administrateur
  connecté peut générer un nouveau code dans « Utilisateurs et permissions ». Pour une base ancienne
  sans code de secours, connectez-vous et créez-en un avant d'en avoir besoin ; sans code configuré,
  la récupération depuis l'écran de connexion n'est pas possible.

La vente rapide permet d'imprimer un ticket, une facture A4 ou un bon de livraison après validation
de la vente. Une facture proforma peut être imprimée depuis le panier avant validation ; son
destinataire peut être saisi librement, sans créer de fiche client, et elle ne crée ni vente ni
mouvement de stock. Les documents reprennent les coordonnées d'établissement communiquées par
l'utilisateur. Vérifiez leur exactitude avant impression.

## Lancer avec VS Code (bouton ▶ sur le fichier)
- `app_demo.py` : le logiciel avec des données FICTIVES, rien n'est enregistré (à essayer en premier).
- `app.py` : le logiciel avec vos données (base dans `%LOCALAPPDATA%\LogicielMagasin\magasin.db`).
- `lancer_tests.py` : lance toute la suite de tests.  `demo.py` : scénario en texte.
  `test_fenetre.py` : test de PySide6.
- Dépendances : `python -m pip install -r requirements.txt`.

## Créer une version Windows installable
Sur un poste Windows de développement, depuis la racine du projet :

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

Le dossier `dist\LogicielMagasin` contient l'application autonome, qui peut aussi être
copiée telle quelle pour un premier essai portable. Pour générer l'installateur `.exe`,
installez Inno Setup 6 puis exécutez :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -CreateInstaller
```

L'installateur 64 bits est créé dans `dist\LogicielMagasin-Setup-0.3.3.exe`.
La base et les journaux restent dans `%LOCALAPPDATA%\LogicielMagasin`, séparés du programme :
une désinstallation ne supprime pas automatiquement les données. Gardez une sauvegarde
avant de remplacer une ancienne installation. Testez le paquet sur un PC Windows sans
Python avant de le distribuer ; l'installation de production et le matériel d'impression
doivent également être vérifiés sur la machine cible.

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

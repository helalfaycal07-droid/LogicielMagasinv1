import json
import urllib.request
import webbrowser
from PySide6.QtWidgets import QMessageBox

# Version actuelle de votre application installée localement
CURRENT_VERSION = "0.3.3" 

# Remplacez par le lien de votre dépôt GitHub (format brut / raw)
VERSION_JSON_URL = "https://raw.githubusercontent.com/VOTRE_PSEUDO/LogicielMagasin/main/version.json"

def check_for_updates(parent_window=None, silent=True):
    try:
        # Télécharger le fichier version.json distant
        req = urllib.request.Request(
            VERSION_JSON_URL,
            headers={'User-Agent': 'LogicielMagasin-Updater'}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_version = data.get("version")
            release_url = data.get("url")
            
            # Comparaison des versions
            if latest_version and latest_version != CURRENT_VERSION:
                msg = QMessageBox(parent_window)
                msg.setIcon(QMessageBox.Information)
                msg.setWindowTitle("Mise à jour disponible")
                # CORRECTION ICI : Ajout du 'f' au début et du guillemet manquant
                msg.setText(
                    f"Une nouvelle version ({latest_version}) est disponible !\n\n"
                    f"Vous utilisez actuellement la version {CURRENT_VERSION}.\n"
                    "Voulez-vous télécharger la mise à jour ?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                
                if msg.exec() == QMessageBox.Yes:
                    webbrowser.open(release_url)
            elif not silent:
                msg = QMessageBox(parent_window)
                QMessageBox.information(parent_window, "À jour", "Votre application est déjà à jour !")
                
    except Exception as e:
        if not silent:
            QMessageBox.warning(parent_window, "Erreur", f"Impossible de vérifier les mises à jour : {e}")
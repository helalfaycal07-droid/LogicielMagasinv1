import json
import urllib.request
import webbrowser
from PySide6.QtWidgets import QMessageBox

# Version actuelle de votre application installée localement
CURRENT_VERSION = "0.3.3"

# URL brute (raw) de version.json sur la branche main
VERSION_JSON_URL = "https://raw.githubusercontent.com/helalfaycal07-droid/LogicielMagasinv1/main/version.json"


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Convertit une chaîne comme '0.3.3' ou 'v0.3.4' en tuple numérique (0, 3, 3)."""
    cleaned = version_str.strip().lstrip("vV")
    parts = []
    for p in cleaned.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_updates(parent_window=None, silent=True):
    try:
        req = urllib.request.Request(
            VERSION_JSON_URL,
            headers={"User-Agent": "LogicielMagasin-Updater"},
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            latest_version = data.get("version")
            release_url = data.get("url")

            if latest_version and _parse_version(str(latest_version)) > _parse_version(CURRENT_VERSION):
                msg = QMessageBox(parent_window)
                msg.setIcon(QMessageBox.Information)
                msg.setWindowTitle("Mise à jour disponible")
                msg.setText(
                    f"Une nouvelle version ({latest_version}) est disponible !\n\n"
                    f"Vous utilisez actuellement la version {CURRENT_VERSION}.\n"
                    "Voulez-vous télécharger la mise à jour ?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

                if msg.exec() == QMessageBox.Yes and release_url:
                    webbrowser.open(release_url)
            elif not silent:
                QMessageBox.information(
                    parent_window,
                    "À jour",
                    "Votre application est déjà à jour !",
                )

    except Exception as e:
        if not silent:
            QMessageBox.warning(
                parent_window,
                "Erreur",
                f"Impossible de vérifier les mises à jour : {e}",
            )
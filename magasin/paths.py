"""Où se trouvent les données : dans le dossier utilisateur de Windows, jamais dans le dossier du programme."""

import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    folder = Path(base) / "LogicielMagasin" if base else Path.home() / ".logiciel_magasin"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def database_path() -> str:
    return str(data_dir() / "magasin.db")

"""Opérations sûres à effectuer avant le démarrage de l'application."""

import sqlite3
from pathlib import Path

from .clock import now
from .db import MIGRATIONS


def backup_before_migrations(database_path: str | Path) -> Path | None:
    """Sauvegarde une base existante avant toute migration de son schéma."""
    database = Path(database_path).expanduser().resolve()
    if not database.exists():
        return None
    if not database.is_file():
        raise ValueError(f"Le chemin de la base n'est pas un fichier : {database}")
    if database.stat().st_size == 0:
        return None

    source = None
    destination = None
    staging_path = None
    try:
        source = sqlite3.connect(str(database), timeout=5)
        version = source.execute("PRAGMA user_version").fetchone()[0]
        latest_version = len(MIGRATIONS)
        if version >= latest_version:
            return None

        backup_directory = database.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_directory / (
            f"{database.stem}-avant-migration-v{version}-vers-v{latest_version}-"
            f"{timestamp}.sqlite"
        )
        staging_path = backup_path.with_suffix(".sqlite.tmp")
        destination = sqlite3.connect(str(staging_path), timeout=5)
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        backup_version = destination.execute("PRAGMA user_version").fetchone()[0]
        if integrity != "ok" or backup_version != version:
            raise sqlite3.DatabaseError(
                "La vérification de la sauvegarde préalable à la migration a échoué."
            )
        destination.close()
        destination = None
        staging_path.replace(backup_path)
        return backup_path
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if staging_path is not None and staging_path.exists():
            staging_path.unlink()

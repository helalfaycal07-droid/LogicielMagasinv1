"""Création et restauration de sauvegardes SQLite."""

import sqlite3
from pathlib import Path

from .db import MIGRATIONS
from .errors import ValidationError
from .users import require_permission


def _database_path(conn: sqlite3.Connection) -> Path | None:
    filename = conn.execute("PRAGMA database_list").fetchone()["file"]
    return Path(filename).resolve() if filename else None


def _requested_path(path: str | Path) -> Path:
    if not str(path).strip():
        raise ValidationError("Choisissez un fichier de sauvegarde.")
    target = Path(path).expanduser()
    if not target.parent.is_dir():
        raise ValidationError("Le dossier choisi n'existe pas.")
    return target.resolve()


def backup_database(conn: sqlite3.Connection, path: str | Path, *,
                    actor_user_id: int | None = None) -> Path:
    """Copie la base ouverte vers un fichier SQLite cohérent, même en mode WAL."""
    require_permission(conn, actor_user_id, "backups")
    target = _requested_path(path)
    if target == _database_path(conn):
        raise ValidationError("La sauvegarde doit être un fichier différent de la base utilisée.")

    destination = None
    try:
        destination = sqlite3.connect(str(target))
        conn.backup(destination)
    except sqlite3.Error as exc:
        raise ValidationError(f"Impossible de créer la sauvegarde : {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
    return target


def _validate_backup(source: sqlite3.Connection) -> None:
    result = source.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise ValidationError(f"La sauvegarde est endommagée : {result}.")

    version = source.execute("PRAGMA user_version").fetchone()[0]
    if version != len(MIGRATIONS):
        raise ValidationError(
            "Cette sauvegarde n'est pas compatible avec la version actuelle du logiciel."
        )

    required_tables = {"products", "sales", "stock_movements", "cash_sessions", "users"}
    tables = {
        row["name"] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = required_tables - tables
    if missing:
        raise ValidationError("Le fichier choisi n'est pas une sauvegarde de ce logiciel.")
    if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValidationError("La sauvegarde contient des liens de données incohérents.")


def restore_database(conn: sqlite3.Connection, path: str | Path, *,
                     actor_user_id: int | None = None) -> Path:
    """Valide puis remplace la base ouverte à partir d'une sauvegarde compatible."""
    require_permission(conn, actor_user_id, "backups")
    source_path = _requested_path(path)
    if not source_path.is_file():
        raise ValidationError("Le fichier de sauvegarde est introuvable.")
    if source_path == _database_path(conn):
        raise ValidationError("Choisissez une sauvegarde différente de la base utilisée.")

    source = None
    try:
        source = sqlite3.connect(str(source_path))
        source.row_factory = sqlite3.Row
        _validate_backup(source)
        source.backup(conn)
    except sqlite3.Error as exc:
        raise ValidationError(f"Impossible de restaurer cette sauvegarde : {exc}") from exc
    finally:
        if source is not None:
            source.close()
    return source_path

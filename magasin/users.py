"""Comptes locaux, mots de passe et permissions par rôle."""

import hashlib
import hmac
import secrets
import sqlite3

from .clock import now_iso
from .db import log_audit, transaction
from .errors import AuthenticationError, PermissionDenied, ValidationError

ROLES = {
    "caissier": "Caissier",
    "responsable": "Responsable",
    "administrateur": "Administrateur",
}

ROLE_PERMISSIONS = {
    "caissier": frozenset({"sell", "cash_open", "customers"}),
    "responsable": frozenset({
        "sell", "return", "cash_open", "cash_expense", "cash_close", "customers",
        "manage_products", "inventory", "approve_discount", "purchases", "reports",
    }),
    "administrateur": frozenset({
        "sell", "return", "cash_open", "cash_expense", "cash_close", "customers",
        "manage_products", "inventory", "delete_product", "purchases", "reports",
        "approve_discount", "backups", "manage_users",
    }),
}

_HASH_ROUNDS = 310_000
_MIN_PASSWORD_LENGTH = 10
_RECOVERY_SALT_KEY = "admin_recovery_salt"
_RECOVERY_HASH_KEY = "admin_recovery_hash"
_RECOVERY_USER_KEY = "admin_recovery_user_id"


def has_users(conn) -> bool:
    return conn.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0] == 1


def list_users(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT id, username, role, active, created_at, last_login_at "
        "FROM users ORDER BY username COLLATE NOCASE"
    )]


def can(user: dict | None, permission: str) -> bool:
    return bool(user and permission in ROLE_PERMISSIONS.get(user["role"], ()))


def require_permission(conn, actor_user_id: int | None, permission: str) -> None:
    if not has_users(conn):
        return
    if actor_user_id is None:
        raise PermissionDenied("Connectez-vous pour effectuer cette opération.")
    user = conn.execute(
        "SELECT role, active FROM users WHERE id = ?", (actor_user_id,)
    ).fetchone()
    if user is None or not user["active"]:
        raise PermissionDenied("Ce compte est désactivé ou n'existe plus.")
    if permission not in ROLE_PERMISSIONS[user["role"]]:
        raise PermissionDenied("Votre rôle ne permet pas cette opération.")


def _password_parts(password: str) -> tuple[bytes, bytes]:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
        raise ValidationError("Le mot de passe doit contenir au moins 10 caractères.")
    if len(password) > 256:
        raise ValidationError("Le mot de passe ne peut pas dépasser 256 caractères.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ROUNDS)
    return salt, digest


def _insert_user(conn, username: str, password: str, role: str, at: str) -> int:
    username = (username or "").strip()
    if len(username) < 3 or len(username) > 50:
        raise ValidationError("L'identifiant doit contenir entre 3 et 50 caractères.")
    if role not in ROLES:
        raise ValidationError("Choisissez un rôle valide.")
    salt, digest = _password_parts(password)
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_salt, password_hash, role, created_at) "
            "VALUES (?,?,?,?,?)",
            (username, salt, digest, role, at),
        )
    except sqlite3.IntegrityError as exc:
        raise ValidationError("Cet identifiant existe déjà.") from exc
    return cursor.lastrowid


def generate_recovery_code() -> str:
    value = secrets.token_hex(16).upper()
    return "-".join(value[index:index + 4] for index in range(0, len(value), 4))


def _normalize_recovery_code(recovery_code: str) -> str:
    return "".join((recovery_code or "").split()).replace("-", "").upper()


def _store_recovery_code(conn, user_id: int, recovery_code: str) -> None:
    normalized = _normalize_recovery_code(recovery_code)
    if len(normalized) != 32 or any(char not in "0123456789ABCDEF" for char in normalized):
        raise ValidationError("Le code de secours est invalide.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", normalized.encode("utf-8"), salt, _HASH_ROUNDS
    )
    settings = {
        _RECOVERY_SALT_KEY: salt.hex(),
        _RECOVERY_HASH_KEY: digest.hex(),
        _RECOVERY_USER_KEY: str(user_id),
    }
    conn.executemany(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        settings.items(),
    )


def create_first_admin(conn, username: str, password: str, *,
                       recovery_code: str | None = None,
                       at: str | None = None) -> int:
    at = at or now_iso()
    with transaction(conn):
        if has_users(conn):
            raise ValidationError("Un compte existe déjà ; utilisez la connexion.")
        user_id = _insert_user(conn, username, password, "administrateur", at)
        if recovery_code:
            _store_recovery_code(conn, user_id, recovery_code)
        log_audit(conn, "administrateur_initial_cree", "users", user_id,
                  (username or "").strip(), at, user_id)
    return user_id


def set_admin_recovery_code(conn, actor_user_id: int, recovery_code: str) -> None:
    require_permission(conn, actor_user_id, "manage_users")
    user = conn.execute(
        "SELECT role, active FROM users WHERE id = ?", (actor_user_id,)
    ).fetchone()
    if user is None or not user["active"] or user["role"] != "administrateur":
        raise PermissionDenied("Seul un administrateur actif peut gérer le code de secours.")
    if not recovery_code:
        raise ValidationError("Le code de secours est invalide.")
    at = now_iso()
    with transaction(conn):
        _store_recovery_code(conn, actor_user_id, recovery_code)
        log_audit(conn, "code_secours_administrateur_modifie", "users",
                  actor_user_id, "code de secours renouvelé", at, actor_user_id)


def reset_admin_account(conn, recovery_code: str, username: str, password: str,
                        *, at: str | None = None) -> dict:
    username = (username or "").strip()
    if len(username) < 3 or len(username) > 50:
        raise ValidationError("L'identifiant doit contenir entre 3 et 50 caractères.")
    salt, digest = _password_parts(password)
    normalized = _normalize_recovery_code(recovery_code)
    if len(normalized) != 32 or any(char not in "0123456789ABCDEF" for char in normalized):
        raise AuthenticationError("Le code de secours est incorrect.")
    at = at or now_iso()

    with transaction(conn):
        settings = dict(conn.execute(
            "SELECT key, value FROM app_settings WHERE key IN (?, ?, ?)",
            (_RECOVERY_SALT_KEY, _RECOVERY_HASH_KEY, _RECOVERY_USER_KEY),
        ).fetchall())
        if len(settings) != 3:
            raise AuthenticationError(
                "Aucun code de secours n'a été configuré pour ce compte."
            )
        candidate = hashlib.pbkdf2_hmac(
            "sha256", normalized.encode("utf-8"),
            bytes.fromhex(settings[_RECOVERY_SALT_KEY]), _HASH_ROUNDS,
        )
        expected = bytes.fromhex(settings[_RECOVERY_HASH_KEY])
        if not hmac.compare_digest(candidate, expected):
            raise AuthenticationError("Le code de secours est incorrect.")

        user_id = int(settings[_RECOVERY_USER_KEY])
        target = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None or target["role"] != "administrateur":
            raise ValidationError("Le compte administrateur de récupération est introuvable.")
        try:
            conn.execute(
                "UPDATE users SET username = ?, password_salt = ?, password_hash = ?, "
                "active = 1 WHERE id = ?",
                (username, salt, digest, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Cet identifiant existe déjà.") from exc
        log_audit(conn, "compte_administrateur_recupere", "users", user_id,
                  f"identifiant réinitialisé : {username}", at, user_id)
        return {"id": user_id, "username": username, "role": "administrateur"}


def authenticate(conn, username: str, password: str, *, at: str | None = None) -> dict:
    username = (username or "").strip()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    salt = row["password_salt"] if row is not None else b"\0" * 16
    raw_password = password if isinstance(password, str) else ""
    candidate = hashlib.pbkdf2_hmac(
        "sha256", raw_password.encode("utf-8"), salt, _HASH_ROUNDS
    )
    if row is None or not row["active"] or not hmac.compare_digest(
        candidate, row["password_hash"] if row is not None else b"\1" * 32
    ):
        raise AuthenticationError("Identifiant ou mot de passe incorrect.")
    at = at or now_iso()
    with transaction(conn):
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (at, row["id"]))
        log_audit(conn, "connexion", "users", row["id"], row["username"], at, row["id"])
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def create_user(conn, actor_user_id: int, username: str, password: str, role: str,
                *, at: str | None = None) -> int:
    if not has_users(conn):
        raise ValidationError("Créez d'abord le compte administrateur initial.")
    require_permission(conn, actor_user_id, "manage_users")
    at = at or now_iso()
    with transaction(conn):
        user_id = _insert_user(conn, username, password, role, at)
        log_audit(conn, "utilisateur_cree", "users", user_id,
                  f"{username.strip()} — {ROLES[role]}", at, actor_user_id)
    return user_id


def update_user(conn, actor_user_id: int, user_id: int, *, role: str | None = None,
                active: bool | None = None, password: str | None = None,
                at: str | None = None) -> None:
    require_permission(conn, actor_user_id, "manage_users")
    if role is not None and role not in ROLES:
        raise ValidationError("Choisissez un rôle valide.")
    if actor_user_id == user_id and (
        (role is not None and role != "administrateur") or active is False
    ):
        raise ValidationError("Utilisez un autre compte administrateur pour modifier ce compte.")
    at = at or now_iso()
    with transaction(conn):
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise ValidationError("Utilisateur introuvable.")
        new_role = role or target["role"]
        new_active = target["active"] if active is None else int(active)
        if target["role"] == "administrateur" and target["active"] and (
            new_role != "administrateur" or not new_active
        ):
            other_admin = conn.execute(
                "SELECT 1 FROM users WHERE id <> ? AND role = 'administrateur' AND active = 1",
                (user_id,),
            ).fetchone()
            if other_admin is None:
                raise ValidationError("Le dernier administrateur actif ne peut pas être rétrogradé.")

        updates = []
        values = []
        details = []
        if role is not None and role != target["role"]:
            updates.append("role = ?")
            values.append(role)
            details.append(f"rôle {target['role']} -> {role}")
        if active is not None and int(active) != target["active"]:
            updates.append("active = ?")
            values.append(int(active))
            details.append(f"actif {target['active']} -> {int(active)}")
        if password is not None:
            salt, digest = _password_parts(password)
            updates.extend(("password_salt = ?", "password_hash = ?"))
            values.extend((salt, digest))
            details.append("mot de passe réinitialisé")
        if updates:
            values.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", values)
            log_audit(conn, "utilisateur_modifie", "users", user_id,
                      "; ".join(details), at, actor_user_id)

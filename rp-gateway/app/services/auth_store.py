"""Gateway users, sessions, and managed provider API keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.core.config import Settings


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt_b64}${digest_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: str
    status: str
    created_at: str
    updated_at: str
    last_login_at: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
        }


@dataclass(frozen=True)
class ProviderApiKey:
    id: str
    label: str
    provider: str
    base_url: str
    secret_value: str
    is_default: bool
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "base_url": self.base_url,
            "is_default": self.is_default,
            "secret_hint": self.secret_value[-4:] if self.secret_value else "",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AuthStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        if settings.auth_enabled:
            self.ensure_bootstrap_admin()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_api_keys (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    secret_value TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def ensure_bootstrap_admin(self) -> None:
        username = self.normalize_username(self.settings.bootstrap_admin_username)
        existing = self.get_user_by_username(username)
        if existing:
            if existing.role != "admin" or existing.status != "active":
                timestamp = now_iso()
                with self.connect() as connection:
                    connection.execute(
                        "UPDATE users SET role = 'admin', status = 'active', updated_at = ? WHERE id = ?",
                        (timestamp, existing.id),
                    )
            return
        password = self.settings.bootstrap_admin_password
        if not password:
            if self.settings.app_env == "production":
                raise RuntimeError("GATEWAY_BOOTSTRAP_ADMIN_PASSWORD is required for first production admin user")
            password = "admin"
        self.create_user(username, password, role="admin")

    def user_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"] if row else 0)

    def default_owner_user_id(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1").fetchone()
        return str(row["id"]) if row else None

    def list_users(self) -> list[AuthUser]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self.user_from_row(row) for row in rows]

    def create_user(self, username: str, password: str, role: str = "user") -> AuthUser:
        username = self.normalize_username(username)
        if role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        if len(password) < 6:
            raise ValueError("password must be at least 6 characters")
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users(id, username, password_hash, role, status, created_at, updated_at)
                    VALUES(?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (user_id, username, hash_password(password), role, timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already exists") from exc
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> AuthUser:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError(f"user not found: {user_id}")
        return self.user_from_row(row)

    def get_user_by_username(self, username: str) -> AuthUser | None:
        username = self.normalize_username(username)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self.user_from_row(row) if row else None

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        username = self.normalize_username(username)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            return None
        user = self.user_from_row(row)
        if user.status != "active" or not verify_password(password, row["password_hash"]):
            return None
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, user.id))
        return self.get_user(user.id)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        created_at = now_iso()
        expires_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + self.settings.auth_session_ttl_seconds),
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, user_id, token_hash, created_at, expires_at) VALUES(?, ?, ?, ?, ?)",
                (session_id, user_id, token_hash(token), created_at, expires_at),
            )
        return token

    def user_for_session(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        self.prune_expired_sessions()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND sessions.expires_at > ?
                  AND users.status = 'active'
                """,
                (token_hash(token), now_iso()),
            ).fetchone()
        return self.user_from_row(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def prune_expired_sessions(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))

    def set_password(self, user_id: str, password: str) -> AuthUser:
        if len(password) < 6:
            raise ValueError("password must be at least 6 characters")
        timestamp = now_iso()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), timestamp, user_id),
            ).rowcount
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if updated == 0:
            raise ValueError(f"user not found: {user_id}")
        return self.get_user(user_id)

    def set_user_status(self, user_id: str, status: str) -> AuthUser:
        if status not in {"active", "disabled"}:
            raise ValueError("status must be active or disabled")
        timestamp = now_iso()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, timestamp, user_id),
            ).rowcount
            if status != "active":
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if updated == 0:
            raise ValueError(f"user not found: {user_id}")
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> None:
        with self.connect() as connection:
            deleted = connection.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount
        if deleted == 0:
            raise ValueError(f"user not found: {user_id}")

    def list_provider_api_keys(self) -> list[ProviderApiKey]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM provider_api_keys ORDER BY is_default DESC, label").fetchall()
        return [self.provider_key_from_row(row) for row in rows]

    def create_provider_api_key(
        self,
        label: str,
        secret_value: str,
        provider: str = "nvidia",
        base_url: str | None = None,
        is_default: bool = True,
    ) -> ProviderApiKey:
        label = " ".join(label.split())[:120] or "Provider key"
        secret_value = secret_value.strip()
        if not secret_value:
            raise ValueError("api key is required")
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        with self.connect() as connection:
            if is_default:
                connection.execute("UPDATE provider_api_keys SET is_default = 0 WHERE provider = ?", (provider,))
            connection.execute(
                """
                INSERT INTO provider_api_keys(id, label, provider, base_url, secret_value, is_default, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    label,
                    provider,
                    base_url or self.settings.nvidia_api_base,
                    secret_value,
                    1 if is_default else 0,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_provider_api_key(key_id)

    def update_provider_api_key(
        self,
        key_id: str,
        label: str | None = None,
        secret_value: str | None = None,
        is_default: bool | None = None,
    ) -> ProviderApiKey:
        current = self.get_provider_api_key(key_id)
        timestamp = now_iso()
        next_label = " ".join((label if label is not None else current.label).split())[:120] or current.label
        next_secret = secret_value.strip() if secret_value is not None else current.secret_value
        if not next_secret:
            raise ValueError("api key is required")
        next_default = current.is_default if is_default is None else is_default
        with self.connect() as connection:
            if next_default:
                connection.execute("UPDATE provider_api_keys SET is_default = 0 WHERE provider = ?", (current.provider,))
            connection.execute(
                """
                UPDATE provider_api_keys
                SET label = ?, secret_value = ?, is_default = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_label, next_secret, 1 if next_default else 0, timestamp, key_id),
            )
        return self.get_provider_api_key(key_id)

    def delete_provider_api_key(self, key_id: str) -> None:
        with self.connect() as connection:
            deleted = connection.execute("DELETE FROM provider_api_keys WHERE id = ?", (key_id,)).rowcount
        if deleted == 0:
            raise ValueError(f"api key not found: {key_id}")

    def get_provider_api_key(self, key_id: str) -> ProviderApiKey:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM provider_api_keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            raise ValueError(f"api key not found: {key_id}")
        return self.provider_key_from_row(row)

    def default_provider_secret(self, base_url: str | None = None, provider: str = "nvidia") -> str | None:
        params: tuple[Any, ...]
        sql = "SELECT * FROM provider_api_keys WHERE provider = ? AND is_default = 1"
        params = (provider,)
        if base_url:
            sql += " AND base_url = ?"
            params = (provider, base_url)
        sql += " ORDER BY updated_at DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
            if row is None and base_url:
                row = connection.execute(
                    "SELECT * FROM provider_api_keys WHERE provider = ? AND is_default = 1 ORDER BY updated_at DESC LIMIT 1",
                    (provider,),
                ).fetchone()
        return str(row["secret_value"]) if row else None

    def user_from_row(self, row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            id=row["id"],
            username=row["username"],
            role=row["role"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )

    def provider_key_from_row(self, row: sqlite3.Row) -> ProviderApiKey:
        return ProviderApiKey(
            id=row["id"],
            label=row["label"],
            provider=row["provider"],
            base_url=row["base_url"],
            secret_value=row["secret_value"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def normalize_username(self, username: str) -> str:
        username = " ".join(username.strip().split()).lower()
        if len(username) < 2 or len(username) > 80:
            raise ValueError("username must be 2-80 characters")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-@")
        if any(char not in allowed for char in username):
            raise ValueError("username may contain latin letters, digits, dot, dash, underscore, or @")
        return username

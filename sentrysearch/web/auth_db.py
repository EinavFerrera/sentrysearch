"""SQLite persistence for SSO users, roles, and auth toggle."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..store import get_data_root

ROLES = ("viewer", "user", "admin")
ROLE_RANK = {"viewer": 0, "user": 1, "admin": 2}


def _db_path() -> Path:
    root = get_data_root()
    if root:
        return root / "auth.db"
    return Path.home() / ".sentrysearch" / "auth.db"


@contextmanager
def _conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_auth_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                role TEXT NOT NULL CHECK(role IN ('viewer','user','admin')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('auth_enabled', 'false');
            """
        )


def _get_setting(key: str, default: str) -> str:
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def _set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_auth_enabled() -> bool:
    return _get_setting("auth_enabled", "false").lower() in ("1", "true", "yes")


def set_auth_enabled(enabled: bool) -> None:
    _set_setting("auth_enabled", "true" if enabled else "false")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_count() -> int:
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, email, role, created_at FROM users ORDER BY email COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_by_email(email: str) -> dict | None:
    email = normalize_email(email)
    with _conn() as c:
        row = c.execute(
            "SELECT id, email, role, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, email, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def add_user(email: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ValueError("invalid email")
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users (email, role) VALUES (?, ?)",
                (email, role),
            )
            uid = int(cur.lastrowid)
    except sqlite3.IntegrityError as e:
        raise ValueError("email already registered") from e
    return get_user_by_id(uid)  # type: ignore


def update_user_role(user_id: int, role: str) -> dict | None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    with _conn() as c:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if c.total_changes == 0:
            return None
    return get_user_by_id(user_id)


def delete_user(user_id: int) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return c.total_changes > 0


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 999)

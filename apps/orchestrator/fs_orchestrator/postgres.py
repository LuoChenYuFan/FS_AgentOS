"""PostgreSQL connection for run checkpoints. Credentials stay in the environment."""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

_DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MIGRATED = False


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_local_env() -> None:
    """Fill unset variables from the gitignored .env file. Never logs values."""
    path = repo_root() / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def postgres_configured() -> bool:
    load_local_env()
    return bool(os.environ.get("POSTGRES_USER") and os.environ.get("POSTGRES_PASSWORD"))


def database_name() -> str:
    load_local_env()
    name = os.environ.get("POSTGRES_DB", "fs_agentos")
    if not _DB_NAME.match(name):
        raise ValueError("POSTGRES_DB must be a plain identifier")
    return name


def _conninfo(dbname: str) -> str:
    load_local_env()
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


def connect(dbname: str | None = None) -> psycopg.Connection:
    target = dbname or database_name()
    if target != "postgres":
        _ensure_database(target)
    return psycopg.connect(_conninfo(target), row_factory=dict_row)


def _ensure_database(target: str) -> None:
    admin = psycopg.connect(_conninfo("postgres"), autocommit=True)
    try:
        found = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
        if found is None:
            admin.execute(f'CREATE DATABASE "{target}"')
    finally:
        admin.close()


def migrate(conn: psycopg.Connection) -> None:
    """Apply migration files that are not recorded yet. Later calls do nothing."""
    global _MIGRATED
    if _MIGRATED:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    runs = conn.execute("SELECT to_regclass('public.runs') AS name").fetchone()
    if runs and runs["name"] and "0001_layer0_contracts.sql" not in applied:
        conn.execute(
            "INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            ("0001_layer0_contracts.sql",),
        )
        applied.add("0001_layer0_contracts.sql")
    for path in sorted((repo_root() / "deploy" / "migrations").glob("*.sql")):
        if path.name in applied:
            continue
        for statement in _sql_statements(path.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (path.name,),
        )
    conn.commit()
    _MIGRATED = True


def _sql_statements(script: str) -> list[str]:
    lines = []
    for raw in script.splitlines():
        if raw.strip().startswith("--"):
            continue
        lines.append(raw)
    body = "\n".join(lines)
    return [part.strip() for part in body.split(";") if part.strip()]

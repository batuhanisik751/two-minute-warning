"""Apply the Drizzle migrations (``web/drizzle/*.sql``) to a LOCAL database, from Python.

For local development and the postgres-marked tests only; the public (Neon) database is
migrated with Drizzle's own tool and the owner's connection (``npm --prefix web run
db:migrate``, docs/deploy.md). This does what ``drizzle-orm``'s migrator does (checked
against drizzle-orm 0.45.2, ``pg-core/dialect.js`` and ``migrator.js``), so the two can be
used on the same database: it keeps the ledger table ``drizzle.__drizzle_migrations`` (id,
hash = sha256 of the SQL file's text, created_at = the journal's ``when``), applies, in one
transaction, every journal entry newer than the newest ledger row, statement by statement
(the files separate statements with ``--> statement-breakpoint``), and records each one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BREAKPOINT = "--> statement-breakpoint"
LEDGER_SCHEMA = "drizzle"
LEDGER_TABLE = "__drizzle_migrations"


def migrations_dir() -> Path:
    from twm.config import ROOT

    return ROOT / "web" / "drizzle"


@dataclass(frozen=True)
class Migration:
    tag: str
    when: int  # the journal's timestamp (ms), Drizzle's created_at
    sql: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def statements(self) -> list[str]:
        return [s for s in self.sql.split(BREAKPOINT) if s.strip()]


def read_migrations(folder: Path | None = None) -> list[Migration]:
    """The journal's migrations in order (``meta/_journal.json``)."""
    folder = folder if folder is not None else migrations_dir()
    journal = folder / "meta" / "_journal.json"
    if not journal.exists():
        raise FileNotFoundError(f"no Drizzle journal at {journal}; run `npm run db:generate`")
    entries = json.loads(journal.read_text())["entries"]
    out = []
    for e in entries:
        path = folder / f"{e['tag']}.sql"
        # newline='' keeps the file's exact text: the hash must equal Drizzle's
        with path.open(encoding="utf-8", newline="") as f:
            out.append(Migration(tag=e["tag"], when=int(e["when"]), sql=f.read()))
    return out


def apply_migrations(conn, folder: Path | None = None) -> list[str]:
    """Apply the pending migrations on ``conn`` (a psycopg connection, not in a transaction)
    in one transaction. Returns the tags applied (empty when the database is up to date)."""
    from psycopg import sql

    migrations = read_migrations(folder)
    ledger = sql.Identifier(LEDGER_SCHEMA, LEDGER_TABLE)
    applied: list[str] = []
    with conn.transaction():
        schema = sql.Identifier(LEDGER_SCHEMA)
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
        conn.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} (id SERIAL PRIMARY KEY, hash text NOT NULL, "
                "created_at bigint)"
            ).format(ledger)
        )
        row = conn.execute(
            sql.SQL("SELECT created_at FROM {} ORDER BY created_at DESC LIMIT 1").format(ledger)
        ).fetchone()
        last = None if row is None or row[0] is None else int(row[0])
        for m in migrations:
            if last is not None and last >= m.when:
                continue
            for statement in m.statements:
                conn.execute(statement)
            conn.execute(
                sql.SQL("INSERT INTO {} (hash, created_at) VALUES (%s, %s)").format(ledger),
                [m.hash, m.when],
            )
            applied.append(m.tag)
    return applied

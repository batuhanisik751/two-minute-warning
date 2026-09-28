"""Create (or update) the public database's roles twm_web and twm_job: runs
scripts/neon/roles.sql as the database owner (docs/deploy.md, step 5).

    uv run python scripts/neon/apply_roles.py

It asks for three things and shows none of them: the OWNER connection string (copied from the
Neon console) and the two new passwords (each typed twice). Nothing is written to disk,
nothing is printed but host names and grants. The connection always verifies the server's
certificate (sslmode=verify-full with a CA bundle from twm.publish.target.CA_BUNDLES: on the
Mac /etc/ssl/cert.pem, the setting the owner's F1 app verified for psycopg on this Mac),
whatever the pasted string says.

Make each password with `openssl rand -base64 32 | tr '/+' '_-' | tr -d '='` and keep it in
your password manager: 43 characters of letters, digits, '_' and '-' (URL-safe, so it can go
into a connection string as it is; Neon requires at least 60 bits of entropy).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import psycopg

from twm.publish.target import CA_BUNDLES, is_local_host, parse, redact

HERE = Path(__file__).resolve().parent
ROLES = ("twm_web", "twm_job")


def _password(label: str) -> str:
    first = getpass.getpass(f"{label} password (hidden): ")
    again = getpass.getpass(f"{label} password again: ")
    if first != again:
        sys.exit(f"the two {label} passwords differ; nothing was changed")
    if len(first) < 16:
        sys.exit(f"the {label} password is shorter than 16 characters; nothing was changed")
    return first


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--allow-local", action="store_true", help="allow a local database (for trying it out)"
    )
    args = ap.parse_args()
    url = getpass.getpass("Owner connection string (hidden): ").strip()
    info = parse(url)
    hosts = [h for h in (info.get("host") or "").split(",") if h]
    local = not hosts or all(is_local_host(h) for h in hosts)
    if local and not args.allow_local:
        sys.exit("that connection string points at this computer; pass --allow-local to use it")
    tls: dict[str, str] = {}
    if not local:
        bundle = next((b for b in CA_BUNDLES if Path(b).exists()), None)
        if bundle is None:
            sys.exit(f"no CA bundle found ({', '.join(CA_BUNDLES)}); cannot verify the server")
        tls = {"sslmode": "verify-full", "sslrootcert": bundle}
    web_pw = _password("twm_web (the site)")
    job_pw = _password("twm_job (the publish job)")
    if web_pw == job_pw:
        sys.exit("use two different passwords; nothing was changed")
    host = ",".join(hosts) or "local socket"
    print(f"connecting to database '{info.get('dbname', '?')}' on {host} as the owner ...")
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=20, **tls) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('twm.web_password', %s, true)", [web_pw])
                conn.execute("SELECT set_config('twm.job_password', %s, true)", [job_pw])
                conn.execute((HERE / "roles.sql").read_text())
            rows = conn.execute(
                "SELECT r.rolname, t.tablename, "
                "has_table_privilege(r.rolname, format('public.%%I', t.tablename), 'SELECT'), "
                "has_table_privilege(r.rolname, format('public.%%I', t.tablename), 'INSERT') "
                "FROM pg_roles r CROSS JOIN pg_tables t "
                "WHERE r.rolname = ANY(%s) AND t.schemaname = 'public' ORDER BY 1, 2",
                [list(ROLES)],
            ).fetchall()
    except psycopg.Error as e:
        print(f"failed, nothing was changed: {redact(str(e), url, web_pw, job_pw)}")
        return 1
    for role in ROLES:
        mine = [r for r in rows if r[0] == role]
        reads = sum(1 for r in mine if r[2])
        writes = sum(1 for r in mine if r[3])
        print(f"{role}: can read {reads} of {len(mine)} tables, can insert into {writes}")
    db = info.get("dbname", "<database>")
    print("\nConnection strings to build (put the passwords in yourself; never paste them here):")
    print(
        f"  site, Vercel (node-postgres; the -pooler host of the same endpoint):\n"
        f"    postgresql://twm_web:<twm_web password>@<pooler host>/{db}?sslmode=verify-full"
    )
    print(
        "  job, GitHub secret DATABASE_URL and .env (psycopg; the direct host; `twm publish`\n"
        "  adds the CA bundle itself):\n"
        f"    postgresql://twm_job:<twm_job password>@{host}/{db}?sslmode=verify-full"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

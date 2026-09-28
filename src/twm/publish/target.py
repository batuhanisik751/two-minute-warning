"""Where a publish goes, and the guard rails around it (step E2).

Two targets, each with its own connection string (docs/deploy.md):

- ``local``: ``TWM_LOCAL_DATABASE_URL`` (default: the Docker container of
  ``docker-compose.yml``, local development only). Refused unless every host is this computer.
- ``remote``: ``DATABASE_URL``, the public database's **writer** role (Neon). Refused when
  unset or when any host is this computer: a publish meant for the public site must never land
  in a local database by accident (the owner's F1 app once did exactly that and reported
  success while the real database stayed empty).

Values come from the environment first, then from ``.env`` (read with python-dotenv, never
``source``d: a URL's ``&`` would become shell job control and print the password). A
connection string is never printed or logged: messages name the host, port and database
only, and :func:`redact` scrubs the URL and password from any error text before it is shown
or stored.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

TargetName = Literal["local", "remote"]
TARGETS: tuple[TargetName, ...] = ("local", "remote")
LOCAL_ENV = "TWM_LOCAL_DATABASE_URL"
REMOTE_ENV = "DATABASE_URL"
# docker-compose.yml's container (local development only; not a secret)
DEFAULT_LOCAL_URL = "postgresql://twm:twm@127.0.0.1:5434/twm"
LOCAL_NAMES = frozenset({"localhost", "host.docker.internal", "twm-postgres"})
# CA bundles for verifying the public database's certificate with psycopg (libpq). The Mac's
# /etc/ssl/cert.pem is the one the owner's F1 app verified for psycopg on this Mac
# (`sslrootcert=system` failed there); the Debian/Ubuntu path is for the GitHub Actions
# runner and is NOT verified yet (step E4 checks it on the runner).
CA_BUNDLES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt")


class TargetError(ValueError):
    """The target is refused or cannot be resolved (nothing was connected to)."""


@dataclass(frozen=True)
class Target:
    """A resolved target. ``url`` is secret: never print it (``describe()`` is safe)."""

    name: TargetName
    url: str
    hosts: tuple[str, ...]
    port: str
    dbname: str
    source: str  # where the URL came from: "environment", ".env" or "default"
    # extra libpq settings for psycopg (the CA bundle when the URL names none)
    connect_args: tuple[tuple[str, str], ...] = ()

    def describe(self) -> str:
        hosts = ",".join(self.hosts) or "local socket"
        port = f":{self.port}" if self.port else ""
        tls = dict(self.connect_args).get("sslrootcert")
        return (
            f"{self.name} database '{self.dbname or '?'}' on {hosts}{port} "
            f"(connection string from {self.source}; not shown"
            + (f"; certificate checked against {tls}" if tls else "")
            + ")"
        )

    def __repr__(self) -> str:  # never leak the URL through a repr
        return f"Target({self.describe()})"


def is_local_host(host: str) -> bool:
    """True for this computer: loopback addresses, ``localhost`` (and ``*.localhost``), a
    Unix socket directory, the compose service name."""
    h = host.strip().strip("[]").lower()
    if not h or h.startswith("/") or h.startswith("@"):
        return True  # a Unix-domain socket: always this computer
    if h in LOCAL_NAMES or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def default_env_file() -> Path:
    """The project's ``.env`` (read only; it may not exist)."""
    from twm.config import ROOT

    return ROOT / ".env"


def _env_value(key: str, env: Mapping[str, str], env_file: Path | None) -> tuple[str, str]:
    """(value, source): the environment first, then the .env file ('' when absent)."""
    value = env.get(key, "").strip()
    if value:
        return value, "environment"
    if env_file is not None and env_file.exists():
        from dotenv import dotenv_values

        value = (dotenv_values(env_file).get(key) or "").strip()
        if value:
            return value, ".env"
    return "", ""


def parse(url: str) -> dict[str, str]:
    """libpq's view of a connection string (URL or key=value), without connecting. The
    error never repeats the string."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        return {k: str(v) for k, v in conninfo_to_dict(url).items() if v is not None}
    except Exception as e:  # psycopg.ProgrammingError; the message could echo parts
        raise TargetError(
            "the connection string is not valid (it is not shown here); check its format: "
            "postgresql://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=..."
        ) from e


def resolve(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> Target:
    """The target's connection string and hosts, after the safety checks (see the module
    docstring). ``env`` defaults to ``os.environ``; ``env_file`` to the project's ``.env``."""
    if name not in TARGETS:
        raise TargetError(f"--target must be one of {', '.join(TARGETS)}, not {name!r}")
    if env is None:
        env = os.environ
    if env_file is None:
        env_file = default_env_file()
    key = LOCAL_ENV if name == "local" else REMOTE_ENV
    url, source = _env_value(key, env, env_file)
    if not url:
        if name == "remote":
            raise TargetError(
                f"{REMOTE_ENV} is not set (environment or .env): it holds the public "
                "database's writer connection (docs/deploy.md). Nothing was published."
            )
        url, source = DEFAULT_LOCAL_URL, "default"
    info = parse(url)
    raw_hosts = [h for h in (info.get("host") or "").split(",") if h.strip()]
    raw_hosts += [h for h in (info.get("hostaddr") or "").split(",") if h.strip()]
    if not raw_hosts and env.get("PGHOST"):
        raw_hosts = [h for h in env["PGHOST"].split(",") if h.strip()]
    hosts = tuple(dict.fromkeys(h.strip() for h in raw_hosts))
    local = [h for h in hosts if is_local_host(h)]
    remote = [h for h in hosts if not is_local_host(h)]
    target = Target(
        name=name,  # type: ignore[arg-type]
        url=url,
        hosts=hosts,
        port=(info.get("port") or "").split(",")[0],
        dbname=info.get("dbname", ""),
        source=source,
    )
    if name == "remote":
        if not hosts:
            raise TargetError(
                f"{REMOTE_ENV} names no host, so it would connect to this computer; the remote "
                "target is the public database. Nothing was published."
            )
        if local:
            raise TargetError(
                f"refusing --target remote: {REMOTE_ENV} points at this computer "
                f"({', '.join(local)}). The remote target is the public (Neon) database; for "
                "the local Docker database use --target local. Nothing was published."
            )
        if info.get("sslmode") != "verify-full":
            raise TargetError(
                f"{REMOTE_ENV} must end in ?sslmode=verify-full (the server's certificate is "
                "then checked, so nobody can pose as the database); it says "
                f"{info.get('sslmode') or 'nothing'}. See docs/deploy.md. Nothing was published."
            )
        if not info.get("sslrootcert"):
            bundle = next((b for b in CA_BUNDLES if Path(b).exists()), None)
            if bundle is None:
                raise TargetError(
                    f"no CA bundle found to check the server's certificate ({', '.join(CA_BUNDLES)}"
                    f"); add sslrootcert=<path> to {REMOTE_ENV}. Nothing was published."
                )
            target = replace(target, connect_args=(("sslrootcert", bundle),))
    elif remote:
        raise TargetError(
            f"refusing --target local: {LOCAL_ENV} points at another computer "
            f"({', '.join(remote)}). --target local only writes to this computer's database; "
            "the public database is --target remote. Nothing was published."
        )
    return target


def redact(text: str, *urls: str) -> str:
    """``text`` with every given connection string and its password replaced by '***'."""
    out = str(text)
    for url in urls:
        if not url:
            continue
        out = out.replace(url, "***")
        try:
            password = parse(url).get("password", "")
        except TargetError:
            password = ""
        # a password of 1-3 characters cannot be a real secret, and replacing it would garble
        # ordinary words of the message
        if len(password) >= 4:
            out = out.replace(password, "***")
            from urllib.parse import quote

            out = out.replace(quote(password, safe=""), "***")
    return out

"""My League's switch and ESPN credentials (PROJECT_SPEC 4.3, 8.3; step F2).

My League runs only when ``ENABLE_MY_LEAGUE`` is true and ``ESPN_LEAGUE_ID`` and ``ESPN_YEAR``
are set; a private league also needs both cookies, ``ESPN_S2`` and ``ESPN_SWID``. Values come
from the environment first, then from ``.env`` (read with python-dotenv, never ``source``d).
Otherwise :func:`resolve` raises :class:`LeagueUnavailableError` with one plain line that names the
keys, never their values, and every ``twm league`` command exits with :data:`EXIT_UNAVAILABLE`.

The cookies stay in memory: they are never printed, logged, stored in data/league.duckdb or
put in an exception message (:func:`redact` scrubs them from any error text first).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

EXIT_UNAVAILABLE = 2  # My League is off, not configured, or ESPN cannot be reached
ENABLE_KEY = "ENABLE_MY_LEAGUE"
LEAGUE_KEYS = ("ESPN_LEAGUE_ID", "ESPN_YEAR")  # the same keys twm doctor checks
COOKIE_KEYS = ("ESPN_S2", "ESPN_SWID")
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
SETUP_HINT = (
    "set ENABLE_MY_LEAGUE=true, ESPN_LEAGUE_ID and ESPN_YEAR (plus ESPN_S2 and ESPN_SWID for a "
    "private league) in .env yourself"
)


class LeagueUnavailableError(RuntimeError):
    """My League cannot run; the message is one line without any value."""


@dataclass(frozen=True)
class LeagueEnv:
    league_id: int
    year: int
    espn_s2: str = field(default="", repr=False)
    swid: str = field(default="", repr=False)

    @property
    def private(self) -> bool:
        return bool(self.espn_s2 and self.swid)

    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.espn_s2, self.swid) if s)

    def __repr__(self) -> str:  # never leak a cookie (or the league id) through a repr
        return f"LeagueEnv(year={self.year}, cookies={'set' if self.private else 'none'})"


def default_env_file() -> Path:
    """The project's ``.env`` (read only; it may not exist)."""
    from twm.config import ROOT

    return ROOT / ".env"


def _values(env: Mapping[str, str], env_file: Path | None) -> dict[str, str]:
    keys = (ENABLE_KEY, *LEAGUE_KEYS, *COOKIE_KEYS)
    out = {k: (env.get(k) or "").strip() for k in keys}
    if env_file is not None and env_file.exists() and not all(out.values()):
        from dotenv import dotenv_values

        file_values = dotenv_values(env_file)
        for k in keys:
            if not out[k]:
                out[k] = (file_values.get(k) or "").strip()
    return out


def resolve(env: Mapping[str, str] | None = None, env_file: Path | None = None) -> LeagueEnv:
    """My League's settings, or :class:`LeagueUnavailableError` (one line, key names only)."""
    v = _values(os.environ if env is None else env, env_file)
    if v[ENABLE_KEY].lower() not in TRUE_WORDS:
        raise LeagueUnavailableError(
            f"My League is off (ENABLE_MY_LEAGUE is not true): {SETUP_HINT}."
        )
    missing = [k for k in LEAGUE_KEYS if not v[k]]
    if missing:
        keys = ", ".join(missing)
        raise LeagueUnavailableError(f"My League is on but {keys} is not set: {SETUP_HINT}.")
    bad = [k for k in LEAGUE_KEYS if not v[k].isdigit()]
    if bad:
        raise LeagueUnavailableError(
            f"{' and '.join(bad)} must be a whole number (the league id is the number after "
            "leagueId= in your league's ESPN address)."
        )
    cookies = [k for k in COOKIE_KEYS if v[k]]
    if len(cookies) == 1:
        raise LeagueUnavailableError(
            f"only {cookies[0]} is set: a private league needs both ESPN_S2 and ESPN_SWID, a "
            "public league neither."
        )
    return LeagueEnv(int(v["ESPN_LEAGUE_ID"]), int(v["ESPN_YEAR"]), v["ESPN_S2"], v["ESPN_SWID"])


def redact(text: object, *secrets: str) -> str:
    """``text`` with every secret (raw, URL-quoted, and a SWID without its braces) as '***'."""
    out = str(text)
    for s in secrets:
        if len(s) < 4:  # too short to be a real cookie; replacing it would garble the message
            continue
        for form in {s, quote(s, safe=""), s.strip("{}")}:
            if len(form) >= 4:
                out = out.replace(form, "***")
    return out

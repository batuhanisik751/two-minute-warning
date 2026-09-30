"""The `twm league` commands behind src/twm/cli.py (which imports this module lazily, so the
rest of the app never loads twm.league; tests/test_publish.py checks it).

Exit codes: 0 done; 2 My League is off or not configured (one line naming the keys) or nothing
has been synced yet; 1 an ESPN call or the sync failed (one redacted line). Output is counts
only: no team, owner or player name, never a cookie.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from twm.league.env import (
    EXIT_UNAVAILABLE,
    LeagueEnv,
    LeagueUnavailableError,
    default_env_file,
    redact,
    resolve,
)
from twm.league.espn_client import EspnError, connect

EXIT_FAILED = 1
REPORT = Path("reports/league/unmatched_players.md")
LABELS = {"league_settings": "settings", "league_scoring": "scoring items", "league_teams":
          "teams", "league_rosters": "roster spots", "league_free_agents": "free agents",
          "league_matchups": "matchup sides", "league_box_scores": "box-score lines",
          "league_activity": "activity rows", "league_player_map": "players"}  # fmt: skip


def _env(echo: Callable[[str], None]) -> LeagueEnv | None:
    try:
        return resolve(env_file=default_env_file())
    except LeagueUnavailableError as e:
        echo(str(e))
        return None


def paths() -> tuple[Path, Path, Path]:
    """(data/league.duckdb, the warehouse, the unmatched-players report): tests replace it."""
    from twm.config import ROOT, settings
    from twm.league import store

    return store.default_path(), ROOT / settings().paths.warehouse, ROOT / REPORT


def _rel(p: Path) -> str:
    from twm.config import ROOT

    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def run_sync(week: int | None, echo: Callable[[str], None] = typer.echo) -> int:
    from twm.league.sync import sync

    env = _env(echo)
    if env is None:
        return EXIT_UNAVAILABLE
    path, warehouse, report = paths()
    try:
        client = connect(env)
        res = sync(client, path, league_id=env.league_id, week=week, report=report,
                   warehouse=warehouse)  # fmt: skip
    except EspnError as e:
        echo(f"My League: {redact(e, *env.secrets())}")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001 - one redacted line, never a traceback with values
        echo(redact(f"My League: the sync failed ({type(e).__name__}: {e})", *env.secrets()))
        return EXIT_FAILED
    echo(f"My League: synced {res.season} week {res.week} into {_rel(path)}")
    echo("  " + ", ".join(f"{LABELS[t]} {n}" for t, n in res.rows.items() if t in LABELS))
    echo("  your team: " + ("found" if res.my_team_found else
                            "not identified (ESPN_SWID matched no team owner)"))  # fmt: skip
    echo(f"  players without a gsis_id: {res.unmatched}"
         + (f" (list: {_rel(report)})" if res.unmatched else ""))  # fmt: skip
    for n in res.notes:
        echo(f"  note: {redact(n, *env.secrets())}")
    return 0


def run_settings_diff(echo: Callable[[str], None] = typer.echo) -> int:
    from twm.config import league, scoring
    from twm.league import store
    from twm.league.settings_diff import load, report

    if _env(echo) is None:
        return EXIT_UNAVAILABLE
    path = paths()[0]
    synced = None
    if path.exists():
        con = store.connect(path, read_only=True)
        try:
            synced = load(con)
        finally:
            con.close()
    if synced is None:
        echo("My League: nothing synced yet: run `uv run twm league sync` first.")
        return EXIT_UNAVAILABLE
    lines, _ = report(synced, scoring(), league())
    for line in lines:
        echo(line)
    return 0

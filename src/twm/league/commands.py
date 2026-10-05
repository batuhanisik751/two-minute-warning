"""The `twm league` commands behind src/twm/cli.py (which imports this module lazily, so the
rest of the app never loads twm.league; tests/test_publish.py checks it).

Exit codes: 0 done; 2 My League is off or not configured (one line naming the keys) or nothing
has been synced yet; 1 an ESPN call or the sync failed (one redacted line). Output is counts
only: no team, owner or player name, never a cookie.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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


def lists_paths() -> tuple[Path, Path | None]:
    """(the predictions store, the pins file: None = config/production_models.yaml); tests
    replace it."""
    from twm import predictions as pr

    return pr.default_path(), None


def _synced_db(echo: Callable[[str], None]) -> Path | None:
    """The league store, or None (one line printed) when My League is off or nothing is synced."""
    if _env(echo) is None:
        return None
    path = paths()[0]
    if not path.exists():
        echo("My League: nothing synced yet: run `uv run twm league sync` first.")
        return None
    return path


def run_radar(week: int | None, limit: int, echo: Callable[[str], None] = typer.echo) -> int:
    """`twm league radar`: the personalized Radar and streamer lists and the drop candidates."""
    from twm.league import store
    from twm.league.personal import PersonalUnavailableError, build, radar_text, scoring_note

    path = _synced_db(echo)
    if path is None:
        return EXIT_UNAVAILABLE
    predictions, pins = lists_paths()
    con = store.connect(path, read_only=True)
    try:
        res = build(con, predictions, week=week, limit=limit, pins_path=pins,
                    warehouse=paths()[1])  # fmt: skip
        note = scoring_note(con)
    except PersonalUnavailableError as e:
        echo(str(e))
        return EXIT_UNAVAILABLE
    finally:
        con.close()
    for line in radar_text(res, note, limit):
        echo(line)
    return 0


def run_regret(season: int | None, echo: Callable[[str], None] = typer.echo) -> int:
    """`twm league regret`: lineup regret per past week of ``season`` (default: the latest)."""
    from twm.league import store
    from twm.league.lineup import starting_slots
    from twm.league.regret import RegretUnavailableError, load_season, season_text

    path = _synced_db(echo)
    if path is None:
        return EXIT_UNAVAILABLE
    con = store.connect(path, read_only=True)
    try:
        res = load_season(con, season, paths()[1])
        part = store.latest(con, "league_settings", res.season)
        slots = starting_slots(store.slot_counts(store.settings_of(
            con, part.league_id, res.season)))[0] if part else {}  # fmt: skip
    except RegretUnavailableError as e:
        echo(str(e))
        return EXIT_UNAVAILABLE
    finally:
        con.close()
    for line in season_text(res, "slots: " + ", ".join(f"{s} {n}" for s, n in slots.items())):
        echo(line)
    return 0


def report_dir() -> Path:
    """reports/league/ (git-ignored): the folder of the unmatched-players list, so tests that
    replace :func:`paths` redirect the report too."""
    return paths()[2].parent


def run_report(
    week: int | None, echo: Callable[[str], None] = typer.echo, now: datetime | None = None
) -> int:
    """`twm league report`: the weekly HTML report into reports/league/<season>-W<nn>.html."""
    from twm.league import store
    from twm.league.personal import PersonalUnavailableError
    from twm.league.report import gather, write

    path = _synced_db(echo)
    if path is None:
        return EXIT_UNAVAILABLE
    predictions, pins = lists_paths()
    con = store.connect(path, read_only=True)
    try:
        data = gather(con, predictions, paths()[1], week=week, pins_path=pins)
        from twm.modules.startsit.league import for_report  # feature #2 (local only)

        data.startsit = for_report(con, paths()[1], now)
    except PersonalUnavailableError as e:
        echo(str(e))
        return EXIT_UNAVAILABLE
    finally:
        con.close()
    generated = (now or datetime.now(UTC)).replace(tzinfo=None)
    out = write(data, report_dir(), generated)
    r = data.radar
    echo(f"My League: wrote {_rel(out)} (the week {r.week} lists on the sync of ESPN week "
         f"{r.sync_week})")  # fmt: skip
    if data.weeks is not None and data.weeks.warning:
        echo(f"  warning: {data.weeks.warning}")
    return 0


def run_trade(
    give: list[str], get: list[str], week: int | None, as_json: bool = False,
    echo: Callable[[str], None] = typer.echo,
) -> int:  # fmt: skip
    """`twm league trade`: the trade checker (twm.league.trade; reads only, writes nothing)."""
    from twm.league import store
    from twm.league.personal import PersonalUnavailableError, scoring_note
    from twm.league.trade import TradeError, run, text
    from twm.league.trade import as_json as to_json

    path = _synced_db(echo)
    if path is None:
        return EXIT_UNAVAILABLE
    predictions, pins = lists_paths()
    con = store.connect(path, read_only=True)
    try:
        res = run(con, predictions, paths()[1], give=give, get=get, week=week, pins_path=pins)
        note = scoring_note(con)
    except (TradeError, PersonalUnavailableError) as e:
        echo(str(e))
        return EXIT_UNAVAILABLE
    finally:
        con.close()
    if as_json:
        echo(to_json(res.check, res.data, res.sched))
        return 0
    for line in text(res.check, res.data, res.sched, note):
        echo(line)
    return 0


def run_weekly(week: int | None, limit: int, echo: Callable[[str], None] = typer.echo) -> int:
    """`twm league weekly`: ingest, full build, the due week's lists scored into the local
    predictions store, sync, report and the radar summary (twm.league.weekly)."""
    from twm.league.weekly import default_parts, run

    if _env(echo) is None:
        return EXIT_UNAVAILABLE
    return run(week, limit, echo, default_parts())


def run_journal(
    season: int | None, week: int | None, echo: Callable[[str], None] = typer.echo
) -> int:
    """`twm league journal`: "You vs the model", your adds, drops and lineups against what the
    app said at the time (twm.league.journal; reads only, writes nothing)."""
    from twm.league import store
    from twm.league.journal import JournalUnavailableError, build, text

    path = _synced_db(echo)
    if path is None:
        return EXIT_UNAVAILABLE
    predictions, pins = lists_paths()
    con = store.connect(path, read_only=True)
    try:
        res = build(con, predictions, paths()[1], season=season, week=week, pins_path=pins)
    except JournalUnavailableError as e:
        echo(str(e))
        return EXIT_UNAVAILABLE
    finally:
        con.close()
    for line in text(res):
        echo(line)
    return 0

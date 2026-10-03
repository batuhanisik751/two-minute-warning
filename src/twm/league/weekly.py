"""`twm league weekly`: the owner's weekly routine in one command, on the owner's Mac (step LW).

`twm league radar`, `report` and `trade` read the lists from the owner's LOCAL predictions store
(``data/predictions.duckdb``); the scheduled job makes its lists on GitHub and publishes them to
the database only, so the Mac's store goes stale without this routine. The steps, in order,
each printing one line; any failure stops the routine with one clear line naming the step's log:

1. **ingest**: ``twm ingest --start <season> --force``: the current season's nflverse files and
   the one-file datasets (the nightly job's warm-cache ingest). A cold cache (a historical file
   missing) stops before any download: the full build needs it (``twm ingest --end <season-1>``).
2. **build**: ``twm build --start <seasons.pbp_start> --end <season>``: ALWAYS the full range.
   ``twm build`` replaces the warehouse with the seasons it is given, so a shorter range would
   leave the Mac's warehouse partial (the job's 2012 start is for its throwaway runner only).
3. **score**: the due week's lists (``--week``, default the latest week whose Tuesday as-of has
   passed) into the predictions store with the approved models, the runner's commands: ``twm
   radar score --pinned``, ``twm streamer score``, ``twm regression score``. A module whose
   approved version(s) already have rows for that week is skipped: a stored list is never
   scored again (the store would replace it), and a partly stored one stops the routine. The
   scorers decide live vs reconstructed themselves (after week N+1's first kickoff a list is
   stored as 'backtest', never as 'live'). Their reports and logs go to
   ``reports/league/weekly/<run>/`` (git-ignored), never over the committed weekly reports.
4. **sync**: `twm league sync` (ESPN's current week).
5. **report**: `twm league report --week N`, then the radar summary (`twm league radar --week N
   --limit <limit>`).

Exit codes: 0 done; 1 a step failed; 2 My League is off or not configured, or no week is due
(before the season's first as-of); 3 the week's data has not fully arrived (a scorer's exit 3).
Nothing here publishes or reads ``.env`` itself (the sync's own loader does).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EXIT_FAILED = 1
EXIT_UNAVAILABLE = 2
EXIT_NOT_READY = 3  # the scorers' "the week's data has not arrived"
# Per-command limits (seconds): the job's, with room for a busy laptop.
LIMITS = {"ingest": 30 * 60, "build": 30 * 60, "score": 20 * 60}
# (label, the `twm` command, its pin keys, extra arguments): the runner's order.
SCORERS = (
    ("Radar", ["radar", "score"], ("waiver_radar",), ["--pinned"]),
    ("streamer", ["streamer", "score"], ("streamer_k", "streamer_dst"), []),
    ("Regression Watch", ["regression", "score"], ("regression_watch",), []),
)
Echo = Callable[[str], None]
Executor = Callable[[str, list[str], Path, float], int]  # stage, args, log, limit -> exit code


@dataclass
class Parts:
    """What the routine runs and reads (tests replace them; :func:`default_parts`)."""

    execute: Executor  # one `twm` command in its own process, output to the log file
    missing_cache: Callable[[int], list[str]]  # historical cache files missing (cold cache)
    windows: Callable[[int], list]  # the season's weeks (pipeline.schedule.WeekWindow)
    pins: Callable[[], dict[str, tuple[int, str]]]  # pin key -> (season, model_version)
    stored: Callable[[int, int], set[str]]  # model versions with rows for (season, week)
    sync: Callable[[Echo], int]  # `twm league sync`
    report: Callable[[int, Echo], int]  # `twm league report --week N`
    radar: Callable[[int, int, Echo], int]  # `twm league radar --week N --limit L`
    out: Path  # reports/league/weekly/ (each run gets a folder)
    pbp_start: int
    season: int


def due_week(windows: list, now: datetime) -> int | None:
    """The latest regular-season week whose Tuesday as-of has passed at ``now`` (aware UTC):
    the list `twm league radar` wants (``windows``: :func:`twm.pipeline.schedule.week_windows`)."""
    passed = [w for w in windows if w.as_of <= now]
    return max(passed, key=lambda w: w.as_of).week if passed else None


def stored_versions(predictions: Path, season: int, week: int) -> set[str]:
    """The model versions with rows for (``season``, ``week``) in the predictions store, read
    only (none when the store does not exist yet)."""
    if not predictions.exists():
        return set()
    import duckdb

    con = duckdb.connect(str(predictions), read_only=True)
    try:
        rows = con.execute("SELECT DISTINCT model_version FROM predictions WHERE season = ? "
                           "AND week = ?", [season, week]).fetchall()  # fmt: skip
    finally:
        con.close()
    return {str(r[0]) for r in rows}


def default_parts() -> Parts:
    """The real routine: `twm` commands as the scheduled runner runs them (its executor: own
    process, output scrubbed of connection strings into the log, killed at the limit)."""
    import os

    from twm.config import settings
    from twm.league import commands
    from twm.league.personal import pinned_versions
    from twm.pipeline import runner
    from twm.pipeline import schedule as sc

    s = settings()
    predictions, pins_path = commands.lists_paths()
    return Parts(
        execute=runner.subprocess_executor(runner.secrets_from(os.environ), echo=False),
        missing_cache=runner.missing_cache,
        windows=lambda season: sc.week_windows(commands.paths()[1], season),
        pins=lambda: pinned_versions(pins_path),
        stored=lambda season, week: stored_versions(predictions, season, week),
        sync=lambda echo: commands.run_sync(None, echo),
        report=lambda week, echo: commands.run_report(week, echo),
        radar=lambda week, limit, echo: commands.run_radar(week, limit, echo),
        out=commands.report_dir() / "weekly",
        pbp_start=int(s.seasons["pbp_start"]),
        season=int(s.current_season),
    )


def _rel(p: Path) -> str:
    from twm.config import ROOT

    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def _tail(log: Path, n: int = 3) -> list[str]:
    try:
        lines = [x.rstrip() for x in log.read_text().splitlines() if x.strip()]
    except OSError:
        return []
    return [f"    {x}" for x in lines[-n:]]


def _secs(t0: float) -> str:
    return f"{time.perf_counter() - t0:.0f} s"


class _StopError(Exception):
    def __init__(self, code: int):
        self.code = code


@dataclass
class _Run:
    parts: Parts
    echo: Echo
    folder: Path  # this run's logs and score reports

    def cli(self, stage: str, args: list[str], log_name: str, ok: tuple[int, ...] = (0,)) -> int:
        """One `twm` command; an exit code outside ``ok`` stops the routine."""
        log = self.folder / f"{log_name}.log"
        code = self.parts.execute(stage, args, log, LIMITS[stage])
        if code not in ok:
            why = f"killed after the {LIMITS[stage] // 60} min limit" if code == 124 else \
                f"exited with {code}"  # fmt: skip
            self.stop(stage, f"`twm {' '.join(args)}` {why} (log: {_rel(log)})", log)
        return code

    def stop(self, stage: str, text: str, log: Path | None = None, code: int = EXIT_FAILED):
        self.echo(f"{stage}: stopped: {text}")
        for line in _tail(log) if log is not None else []:
            self.echo(line)
        raise _StopError(code)

    def ingest(self, season: int) -> None:
        t0 = time.perf_counter()
        missing = self.parts.missing_cache(season)
        if missing:
            self.stop("ingest", f"the cache misses {len(missing)} historical file(s) (e.g. "
                      f"{missing[0]}): run `uv run twm ingest --end {season - 1}` first, the "
                      "full warehouse needs them")  # fmt: skip
        self.cli("ingest", ["ingest", "--start", str(season), "--force"], "ingest")
        self.echo(f"ingest: the {season} nflverse files and the one-file datasets refreshed "
                  f"({_secs(t0)})")  # fmt: skip

    def build(self, season: int) -> None:
        t0, first = time.perf_counter(), self.parts.pbp_start
        self.cli("build", ["build", "--start", str(first), "--end", str(season)], "build")
        self.echo(f"build: the full warehouse {first}-{season} rebuilt ({_secs(t0)})")

    def score(self, season: int, week: int) -> None:
        """Each module's list of ``week`` with the approved models, unless already stored."""
        t0, pins, done = time.perf_counter(), self.parts.pins(), []
        have = self.parts.stored(season, week)
        for label, cmd, keys, extra in SCORERS:
            versions = [pins[k][1] for k in keys if k in pins and pins[k][0] == season]
            stored = [v for v in versions if v in have]
            if stored and len(stored) == len(keys):
                done.append(f"{label} already stored")
                continue
            if stored:
                self.stop("score", f"the {label}'s week {week} list is stored for "
                          f"{', '.join(stored)} only: not scored again (that would replace "
                          "the stored list)")  # fmt: skip
            t1, report = time.perf_counter(), self.folder / f"{cmd[0]}-{season}-W{week:02d}.md"
            args = [*cmd, "--season", str(season), "--week", str(week), *extra, "--out",
                    str(report)]  # fmt: skip
            log = self.folder / f"score-{cmd[0]}.log"
            if self.cli("score", args, log.stem, (0, EXIT_NOT_READY)) == EXIT_NOT_READY:
                self.stop("score", f"week {week}'s data has not fully arrived yet (the "
                          f"{label}: `twm {' '.join(cmd)}` exit {EXIT_NOT_READY}; log: "
                          f"{_rel(log)}): run again later", log, EXIT_NOT_READY)  # fmt: skip
            done.append(f"{label} scored in {_secs(t1)}")
        self.echo(f"score: week {week}: {', '.join(done)} ({_secs(t0)})")

    def league(self, stage: str, call: Callable[[Echo], int]) -> None:
        """`twm league sync` / `report` in-process: their lines folded into one."""
        t0, lines = time.perf_counter(), []
        code = call(lines.append)
        text = "; ".join(x.strip().removeprefix("My League: ") for x in lines if x.strip())
        if code != 0:
            self.stop(stage, text or f"exit {code}", code=code)
        self.echo(f"{stage}: {text} ({_secs(t0)})")


def run(week: int | None, limit: int, echo: Echo, parts: Parts, now: datetime | None = None) -> int:
    """The routine (module docstring); returns the exit code."""
    t0, season = time.perf_counter(), parts.season
    now = now or datetime.now(UTC)
    r = _Run(parts, echo, parts.out / now.strftime("%Y%m%dT%H%M%SZ"))
    try:
        r.ingest(season)
        r.build(season)
        if week is None:
            week = due_week(parts.windows(season), now)
            if week is None:
                r.stop("score", f"no list is due yet: no week of {season} has passed its "
                       "Tuesday as-of", code=EXIT_UNAVAILABLE)  # fmt: skip
        assert week is not None
        r.score(season, week)
        r.league("sync", parts.sync)
        r.league("report", lambda e: parts.report(week, e))
    except _StopError as e:
        return e.code
    echo("")
    code = parts.radar(week, limit, echo)
    if code != 0:
        return code
    echo("")
    echo(f"My League weekly: done in {_secs(t0)} (logs and score reports: {_rel(r.folder)}/)")
    return code

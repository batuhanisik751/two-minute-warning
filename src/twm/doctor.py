"""`twm doctor`: is this checkout ready to run? (PROJECT_SPEC 9: "checks data freshness, ID
coverage, env".)

Each check prints one plain-English line with a status and, below it, the details:

- **PASS** all good;
- **WARN** something is missing or stale, the app still works (a fresh clone with no data
  yet, a warehouse older than the cache ...); the line says what to run;
- **FAIL** something is broken and a command will fail or give wrong results (a config file
  that does not validate, a dangling data symlink, hidden ``.pth`` files that break
  ``import twm``, an unreadable predictions store). ``twm doctor`` exits with code 1 when
  any check fails.

The checks, in order: config files load and validate (and the league shape derived from
them); ``.env`` (key names only: a value is never read into the output); hidden ``.pth``
files in the virtualenv (the macOS/iCloud problem, docs/progress.md); the local-storage
symlinks (scripts/local_storage.sh); nflreadpy's version against the schema snapshots; the raw
cache's seasons against each dataset's coverage; cache freshness in season (file ages, the
latest week with results against the calendar); the warehouse (built? built after the cache
last changed?); player-id coverage; the predictions store and the current production model.

Every check reads its paths from a :class:`Context` (:func:`default_context` for the real
checkout), so tests can point each one at a synthetic setup.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import sysconfig
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

Status = Literal["PASS", "WARN", "FAIL"]
STATUSES: tuple[Status, ...] = ("PASS", "WARN", "FAIL")

# .env keys the app knows (.env.example); values are never printed.
ENV_KEYS = ("DATABASE_URL", "TWM_LOCAL_DATABASE_URL", "ENABLE_MY_LEAGUE", "ESPN_LEAGUE_ID",
            "ESPN_YEAR", "ESPN_S2", "ESPN_SWID")  # fmt: skip
ESPN_REQUIRED = ("ESPN_LEAGUE_ID", "ESPN_YEAR")
ESPN_PRIVATE = ("ESPN_S2", "ESPN_SWID")  # cookies of a private league (the owner's is private)
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
# Paths scripts/local_storage.sh keeps outside a cloud-synced checkout (links in the project).
LOCAL_STORAGE_LINKS = (".venv", "data/raw", "data/waiver_radar", "data/predictions.duckdb",
                       "data/warehouse.duckdb", "models")  # fmt: skip
# In season, a current-season cache file older than this means the weekly ingest was missed.
STALE_CURRENT_DAYS = 7
# A week "should have results" this long after its last game's estimated end (kickoff + 4 h):
# `twm ingest` refreshes current-season files older than 12 h.
RESULT_GRACE = timedelta(hours=4 + 12)
# Player-id coverage below this share of QB/RB/WR/TE snap rows is worth a look.
ID_COVERAGE_WARN = 0.99


@dataclass
class Check:
    status: Status
    title: str
    details: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [f"{self.status}  {self.title}", *(f"      {d}" for d in self.details)]


@dataclass
class Context:
    """Every path and fact a check reads (tests build their own)."""

    root: Path
    config_dir: Path
    env_file: Path
    env_example: Path
    gitignore: Path
    site_packages: Path | None
    links: dict[str, Path]
    cloud_synced: bool
    raw_dir: Path
    schema_dir: Path
    warehouse: Path
    store: Path
    models_dir: Path
    dataset: Path
    current_season: int | None
    now: datetime  # aware UTC


def _venv_site_packages(root: Path) -> Path | None:
    found = sorted((root / ".venv").glob("lib/python*/site-packages"))
    if found:
        return found[0]
    purelib = sysconfig.get_paths().get("purelib")
    return Path(purelib) if purelib else None


def is_cloud_synced(root: Path) -> bool:
    """macOS iCloud Drive syncs ~/Desktop and ~/Documents (and ~/Library/Mobile Documents)."""
    if sys.platform != "darwin":
        return False
    home = Path.home()
    synced = (home / "Desktop", home / "Documents", home / "Library" / "Mobile Documents")
    return any(p == s or s in p.parents for p in (root, root.resolve()) for s in synced)


def default_context(now: datetime | None = None) -> Context:
    """The real checkout: paths from config/settings.yaml (the config must load)."""
    from twm.config import ROOT, config_dir, settings
    from twm.sources import nflverse as nv

    s = settings()
    return Context(
        root=ROOT,
        config_dir=config_dir(),
        env_file=ROOT / ".env",
        env_example=ROOT / ".env.example",
        gitignore=ROOT / ".gitignore",
        site_packages=_venv_site_packages(ROOT),
        links={name: ROOT / name for name in LOCAL_STORAGE_LINKS},
        cloud_synced=is_cloud_synced(ROOT),
        raw_dir=nv.raw_dir(),
        schema_dir=nv.schema_dir(),
        warehouse=s.path("warehouse"),
        store=s.path("predictions"),
        models_dir=s.path("models") / "waiver_radar",  # production.models_dir(), no import
        dataset=ROOT / "data" / "waiver_radar" / "dataset.parquet",
        current_season=s.current_season,
        now=now or datetime.now(UTC),
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _span(seasons: Sequence[int]) -> str:
    """[1999, 2000, 2001, 2005] -> '1999-2001, 2005'."""
    out: list[str] = []
    s = sorted(seasons)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append(str(s[i]) if i == j else f"{s[i]}-{s[j]}")
        i = j + 1
    return ", ".join(out)


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} d"


# --------------------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------------------


def check_config(ctx: Context) -> Check:
    """settings.yaml, scoring.yaml and league.yaml load and validate; the league shape."""
    import yaml
    from pydantic import ValidationError

    from twm.config import League, Scoring, Settings

    models = {"settings.yaml": Settings, "scoring.yaml": Scoring, "league.yaml": League}
    problems: list[str] = []
    loaded: dict[str, object] = {}
    for name, model in models.items():
        path = ctx.config_dir / name
        try:
            raw = yaml.safe_load(path.read_text())
            loaded[name] = model(**(raw or {}))
        except FileNotFoundError:
            problems.append(f"{name}: not found in {ctx.config_dir}")
        except (yaml.YAMLError, ValidationError, ValueError, TypeError) as e:
            first = str(e).strip().splitlines()
            problems.append(f"{name}: " + " ".join(x.strip() for x in first[:4]))
    where = _rel(ctx.config_dir, ctx.root)
    if problems:
        return Check("FAIL", f"Config: {len(problems)} of 3 files in {where}/ do not load",
                     problems)  # fmt: skip
    lg = loaded["league.yaml"]
    sc = loaded["scoring.yaml"]
    assert isinstance(lg, League) and isinstance(sc, Scoring)
    catch = sc.receiving.get("receptions", 0)
    details = [f"league: {line}" for line in lg.shape.describe()]
    details.append(
        f"scoring: {catch:g} point per catch, fumbles lost: {sc.options.fumbles_lost_scope}"
    )
    return Check("PASS", f"Config: settings.yaml, scoring.yaml and league.yaml load and "
                 f"validate ({where}/)", details)  # fmt: skip


def _env_bindings(path: Path) -> tuple[dict[str, str], list[int]]:
    """(key -> value, line numbers that do not parse). Values stay in memory only."""
    from dotenv.parser import parse_stream

    values: dict[str, str] = {}
    bad: list[int] = []
    with path.open(encoding="utf-8") as f:
        for b in parse_stream(f):
            if b.error:
                bad.append(b.original.line)
            elif b.key is not None:
                values[b.key] = b.value or ""
    return values, bad


def _ignored_by_git(ctx: Context) -> bool:
    if not ctx.gitignore.exists():
        return False
    lines = [x.strip() for x in ctx.gitignore.read_text().splitlines()]
    return ".env" in lines or "/.env" in lines or ".env*" in lines


def check_env(ctx: Context) -> Check:
    """.env parses; which keys are set (names only, never values); My League's keys."""
    if not ctx.env_file.exists():
        return Check(
            "WARN",
            ".env: not found (not needed until the publish step and My League; "
            "`cp .env.example .env`, then fill it in yourself)",
        )
    if not _ignored_by_git(ctx):
        return Check("FAIL", ".env exists but .gitignore does not list it: secrets could be "
                     "committed; add a `.env` line to .gitignore")  # fmt: skip
    values, bad = _env_bindings(ctx.env_file)
    is_set = sorted(k for k, v in values.items() if v.strip())
    known = [k for k in ENV_KEYS if k in is_set]
    empty = [k for k in ENV_KEYS if k not in is_set]
    other = sorted(k for k in values if k not in ENV_KEYS)
    details = [
        "set: " + (", ".join(known) or "none"),
        "empty or absent: " + (", ".join(empty) or "none"),
    ]
    if other:
        details.append("other keys: " + ", ".join(other))
    status: Status = "PASS"
    if bad:
        status = "WARN"
        details.append("lines that do not parse (KEY=value expected): " + ", ".join(map(str, bad)))
    on = values.get("ENABLE_MY_LEAGUE", "").strip().lower() in TRUE_WORDS
    if on:
        missing = [k for k in ESPN_REQUIRED if k not in is_set]
        cookies = [k for k in ESPN_PRIVATE if k not in is_set]
        details.append("My League: ENABLE_MY_LEAGUE is on")
        if missing:
            status = "WARN"
            details.append("My League needs " + ", ".join(missing))
        if cookies:
            status = "WARN"
            details.append(
                "a private league also needs "
                + ", ".join(cookies)
                + " (copy them from your browser's cookies into .env yourself)"
            )
    else:
        details.append("My League: off (ENABLE_MY_LEAGUE is not true); the app runs without ESPN")
    return Check(status, ".env parsed (values are never shown)", details)


def check_hidden_pth(ctx: Context) -> Check:
    """Python 3.13 skips .pth files flagged hidden (iCloud sets the flag on dot-folders), which
    breaks `import twm` at random (docs/progress.md, Known issues)."""
    sp = ctx.site_packages
    if sp is None or not sp.exists():
        return Check("WARN", "Virtualenv: no site-packages found (run `uv sync --all-extras`)")
    hidden = []
    for p in sorted(sp.glob("*.pth")):
        try:
            flags = getattr(os.lstat(p), "st_flags", 0)
        except OSError:
            continue
        if flags & getattr(stat, "UF_HIDDEN", 0):
            hidden.append(p.name)
    where = _rel(sp, ctx.root)
    if hidden:
        return Check(
            "FAIL",
            f"Virtualenv: {len(hidden)} hidden .pth file(s) that Python skips, so imports "
            "break (fix: `chflags -R nohidden .venv`, or re-run scripts/local_storage.sh)",
            [f"{where}/{n}" for n in hidden],
        )
    return Check("PASS", "Virtualenv: no hidden .pth files (the macOS/iCloud import problem)")


def check_links(ctx: Context) -> Check:
    """The local-storage symlinks (scripts/local_storage.sh) point at something that exists."""
    dangling, linked, local = [], [], []
    for name, p in ctx.links.items():
        if p.is_symlink():
            target = Path(os.readlink(p))
            target = target if target.is_absolute() else p.parent / target
            (linked if target.exists() else dangling).append(f"{name} -> {target}")
        elif p.exists():
            local.append(name)
    if dangling:
        return Check(
            "FAIL",
            "Local storage: symlink target(s) missing (re-run scripts/local_storage.sh, or "
            "restore the folder it pointed at)",
            dangling,
        )
    details = linked + ([f"in the project folder: {', '.join(local)}"] if local else [])
    if local and ctx.cloud_synced:
        return Check(
            "WARN",
            "Local storage: the checkout is in a cloud-synced folder and some heavy paths are "
            "not linked out of it (run scripts/local_storage.sh once)",
            details,
        )
    return Check("PASS", "Local storage: every symlink points at an existing target", details)


def check_nflreadpy(ctx: Context) -> Check:
    """The installed nflreadpy against the version the schema snapshots were captured with."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("nflreadpy")
    except PackageNotFoundError:
        return Check("FAIL", "nflreadpy is not installed (run `uv sync --all-extras`)")
    seen: dict[str, list[str]] = {}
    for p in sorted(ctx.schema_dir.glob("*.json")):
        try:
            v = json.loads(p.read_text()).get("nflreadpy_version") or "unknown"
        except (OSError, ValueError):
            v = "unreadable"
        seen.setdefault(str(v), []).append(p.stem)
    if not seen:
        return Check("WARN", f"nflreadpy {installed}: no schema snapshots in "
                     f"{_rel(ctx.schema_dir, ctx.root)}")  # fmt: skip
    n = sum(len(v) for v in seen.values())
    if set(seen) == {installed}:
        return Check("PASS", f"nflreadpy {installed} = the version all {n} schema snapshots "
                     "were captured with")  # fmt: skip
    return Check(
        "WARN",
        f"nflreadpy {installed} is installed but schema snapshots were captured with "
        + ", ".join(sorted(seen))
        + ": check for upstream changes (`uv run pytest -m network`) before refreshing them "
        "(`uv run twm snapshot <dataset>` or scripts/refresh_snapshots.py)",
        [f"{v}: {len(names)} snapshots" for v, names in sorted(seen.items())],
    )


def _expected_seasons(ds, current: int) -> list[int]:
    from twm.sources import nflverse as nv

    first = ds.first_season or current
    last = current - 1 if ds.name in nv.POST_SEASON_ONLY else current
    return list(range(first, last + 1))


def check_cache_coverage(ctx: Context) -> Check:
    """Seasons missing from data/raw against each dataset's coverage (first season to now)."""
    from twm.sources import nflverse as nv

    if ctx.current_season is None:
        return Check("WARN", "Raw cache: skipped (settings.yaml did not load)")
    if not ctx.raw_dir.exists() or not any(ctx.raw_dir.iterdir()):
        return Check("WARN", f"Raw cache: empty ({_rel(ctx.raw_dir, ctx.root)}); run "
                     "`uv run twm ingest` (or a quick start: README)")  # fmt: skip
    missing: list[str] = []
    n_files = 0
    for name, ds in nv.DATASETS.items():
        if not ds.per_season:
            if (ctx.raw_dir / name / "all.parquet").exists():
                n_files += 1
            else:
                missing.append(f"{name} (one file)")
            continue
        have = set(_cached(ctx.raw_dir, name, ds.first_season))
        n_files += len(have)
        gap = [s for s in _expected_seasons(ds, ctx.current_season) if s not in have]
        if gap:
            missing.append(f"{name}: {_span(gap)}")
    if missing:
        return Check(
            "WARN",
            f"Raw cache: {len(missing)} dataset(s) miss seasons of their coverage (fine if you "
            "only need recent seasons; `uv run twm ingest [DATASET] --start YEAR` fills them)",
            missing,
        )
    return Check("PASS", f"Raw cache: every dataset covers all its seasons up to "
                 f"{ctx.current_season} ({n_files} files)")  # fmt: skip


def _cached(raw: Path, name: str, first: int | None) -> list[int]:
    """Like nflverse.cached_seasons, for any raw folder (0-row files do not count)."""
    import pyarrow.parquet as pq

    out = []
    for p in (raw / name).glob("*.parquet"):
        if not p.stem.isdigit() or (first is not None and int(p.stem) < first):
            continue
        try:
            if pq.read_metadata(p).num_rows == 0:
                continue
        except Exception:  # an unreadable file is not a usable season
            continue
        out.append(int(p.stem))
    return sorted(out)


def _schedule(ctx: Context):
    import polars as pl

    path = ctx.raw_dir / "schedules" / f"{ctx.current_season}.parquet"
    if not path.exists():
        return None
    return pl.read_parquet(path)


def season_calendar(schedule, now: datetime) -> dict[str, object]:
    """From a season's schedule: the first kickoff, the last regular-season game end, the latest
    regular-season week with every result in, and the latest week that should have them by
    ``now`` (its last game ended, estimated kickoff + 4 h, more than 12 h ago)."""
    from twm.warehouse.weeks import kickoff_utc

    games = schedule.filter(schedule["game_type"] == "REG")
    rows = games.select("week", "gameday", "gametime", "weekday", "result").to_dicts()
    if not rows:
        return {}
    ends: dict[int, datetime] = {}
    firsts: list[datetime] = []
    done: dict[int, bool] = {}
    for r in rows:
        ko, _ = kickoff_utc(str(r["gameday"]), r["gametime"], r["weekday"])
        ko = ko.replace(tzinfo=UTC)
        firsts.append(ko)
        w = int(r["week"])
        ends[w] = max(ends.get(w, ko), ko)
        done[w] = done.get(w, True) and r["result"] is not None
    have = [w for w in sorted(done) if done[w]]
    due = [w for w in sorted(ends) if ends[w] + RESULT_GRACE <= now]
    return {
        "first_kickoff": min(firsts),
        "season_end": max(ends.values()) + timedelta(hours=4),
        "results_week": max(have) if have else 0,
        "due_week": max(due) if due else 0,
        "due_at": {w: ends[w] + RESULT_GRACE for w in ends},
    }


def check_freshness(ctx: Context) -> Check:
    """In season: the age of every current-season cache file and the latest week with results
    against the calendar."""
    from twm.sources import nflverse as nv

    if ctx.current_season is None:
        return Check("WARN", "Cache freshness: skipped (settings.yaml did not load)")
    season = ctx.current_season
    sched = _schedule(ctx)
    if sched is None:
        return Check("WARN", f"Cache freshness: no {season} schedule in the cache; run "
                     f"`uv run twm ingest --start {season}`")  # fmt: skip
    cal = season_calendar(sched, ctx.now)
    if not cal:
        return Check("WARN", f"Cache freshness: the {season} schedule has no regular-season games")
    in_season = cal["first_kickoff"] <= ctx.now <= cal["season_end"] + timedelta(days=45)
    ages: list[tuple[str, timedelta]] = []
    absent: list[str] = []
    for name, ds in nv.DATASETS.items():
        if not ds.per_season or season not in _expected_seasons(ds, season):
            continue
        p = ctx.raw_dir / name / f"{season}.parquet"
        if p.exists():
            mtime = datetime.fromtimestamp(p.stat().st_mtime, UTC)
            ages.append((name, ctx.now - mtime))
        else:
            absent.append(name)
    stale = [n for n, a in ages if a > timedelta(days=STALE_CURRENT_DAYS)]
    oldest = max(ages, key=lambda x: x[1]) if ages else None
    details = []
    if ages:
        newest = min(ages, key=lambda x: x[1])
        details.append(
            f"{season} files: {len(ages)}, newest {newest[0]} ({_age(newest[1])} old), oldest "
            f"{oldest[0]} ({_age(oldest[1])} old)"  # type: ignore[index]
        )
    results, due = int(cal["results_week"]), int(cal["due_week"])
    details.append(
        f"schedule: results through week {results}; by the calendar week {due} should have "
        "them (last game + 16 h)"
    )
    problems = []
    if in_season:
        if stale:
            problems.append(f"older than {STALE_CURRENT_DAYS} days: " + ", ".join(stale))
        if absent:
            problems.append("no current-season file: " + ", ".join(absent))
        if results < due:
            problems.append(f"week {due}'s results are missing from the cached schedule")
    if problems:
        return Check(
            "WARN",
            f"Cache freshness: the {season} cache is behind; run `uv run twm ingest --start "
            f"{season}` then `uv run twm build --start 1999 --end {season}`",
            problems + details,
        )
    when = "in season" if in_season else "out of season (ages not checked)"
    return Check("PASS", f"Cache freshness: {season} is up to date ({when})", details)


def check_warehouse(ctx: Context) -> tuple[Check, Path | None]:
    """Built? Readable? Built after the raw cache last changed? (+ the table list)"""
    import duckdb

    from twm.warehouse import build as wb

    db = ctx.warehouse
    rel = _rel(db, ctx.root)
    if not db.exists():
        season = ctx.current_season or datetime.now(UTC).year
        return Check("WARN", f"Warehouse: not built ({rel}); run `twm build {season - 1} "
                     f"{season}` (or --start 1999)"), None  # fmt: skip
    try:
        counts = wb.table_counts(db)
        con = wb.connect(db, read_only=True)
    except duckdb.Error:
        return Check("WARN", f"Warehouse: {rel} is locked by another process (close notebooks "
                     "/ open read-only)"), None  # fmt: skip
    try:
        man = con.execute(
            "SELECT max(built_at), max(seasons) FILTER (WHERE table_name = 'fact_game') "
            "FROM build_manifest"
        ).fetchone()
    except duckdb.Error:
        man = None
    finally:
        con.close()
    tables = [f"{name:22s} {n:>12,} rows" for name, n in counts]
    if not man or man[0] is None:
        return Check("WARN", f"Warehouse: {rel} has no build manifest (built by an old version); "
                     "rebuild it with `twm build`", tables), db  # fmt: skip
    built = man[0].replace(tzinfo=UTC)
    seasons = [int(s) for s in json.loads(man[1] or "[]")]
    newer = _cache_changes_after(ctx, seasons, built)
    head = (
        f"Warehouse: {len(counts)} tables, seasons {_span(seasons) or '-'}, built "
        f"{built:%Y-%m-%d %H:%M} UTC"
    )
    if newer:
        n, examples = newer
        return Check(
            "WARN",
            head + f"; the raw cache changed after it ({n} file(s) re-downloaded since): run "
            f"`uv run twm build --start {min(seasons, default=1999)} --end "
            f"{max(seasons, default=ctx.current_season or 0)}`",
            examples + tables,
        ), db
    return Check("PASS", head + ", after the last cache change", tables), db


def _cache_changes_after(
    ctx: Context, seasons: Sequence[int], built: datetime
) -> tuple[int, list[str]] | None:
    """Cache files the build reads that were written after ``built`` (count, examples).
    ``built_at`` is stored in whole seconds, so a file time is compared in whole seconds too
    (a file written in the build's last second counts as older)."""
    from twm.warehouse import schema as sc

    newer: list[tuple[datetime, str]] = []
    for name in sc.SOURCE_DATASETS:
        folder = ctx.raw_dir / name
        files = [folder / "all.parquet"]
        files += [folder / f"{s}.parquet" for s in seasons]
        for p in files:
            if p.exists():
                m = datetime.fromtimestamp(p.stat().st_mtime, UTC).replace(microsecond=0)
                if m > built:
                    newer.append((m, f"{name}/{p.name} written {m:%Y-%m-%d %H:%M} UTC"))
    if not newer:
        return None
    newer.sort(reverse=True)
    return len(newer), [x for _, x in newer[:3]]


def check_ids(ctx: Context, db: Path | None) -> Check | None:
    """The id coverage of the last build (the existing one-line summary)."""
    import duckdb

    from twm import ids
    from twm.warehouse import build as wb

    if db is None:
        return None
    try:
        con = wb.connect(db, read_only=True)
    except duckdb.Error:
        return None
    try:
        line = ids.doctor_line(con)
        if line is None:
            return Check("WARN", "Player ids: the warehouse has no id report (built before B3); "
                         "rebuild it with `twm build`")  # fmt: skip
        low = con.execute(
            "SELECT min(match_rate) FROM report_id_coverage "
            "WHERE dataset = 'fact_snaps' AND scope = 'fantasy'"
        ).fetchone()[0]
    except duckdb.Error:
        return None
    finally:
        con.close()
    if low is not None and low < ID_COVERAGE_WARN:
        return Check("WARN", f"Player ids: a season has under {ID_COVERAGE_WARN:.0%} of QB/RB/WR/"
                     "TE snap rows matched (see `uv run twm ids`)", [line])  # fmt: skip
    return Check("PASS", "Player ids: the last build's id coverage", [line])


def check_store(ctx: Context) -> Check:
    """The predictions store and the current season's production model (its file too)."""
    import duckdb

    from twm.modules.waiver_radar import production as prod

    rel = _rel(ctx.store, ctx.root)
    season = ctx.current_season
    details = []
    if ctx.dataset.exists():
        details.append(f"dataset: {_rel(ctx.dataset, ctx.root)}")
    else:
        details.append("dataset: not built (`uv run twm radar dataset`)")
    if not ctx.store.exists():
        return Check("WARN", f"Predictions store: none yet ({rel}); `uv run twm backtest "
                     "waiver_radar` fills it", details)  # fmt: skip
    if season is None:
        return Check("WARN", "Predictions store: skipped (settings.yaml did not load)", details)
    try:
        cur = prod.current_production(ctx.store, season)
    except duckdb.IOException as e:
        if "lock" in str(e).lower():
            return Check("WARN", f"Predictions store: {rel} is locked by another process",
                         details)  # fmt: skip
        return Check("FAIL", f"Predictions store: {rel} cannot be read ({e})", details)
    except (duckdb.Error, ValueError) as e:
        return Check("FAIL", f"Predictions store: {rel} cannot be read ({e})", details)
    if cur is None:
        return Check(
            "WARN",
            f"Predictions store: no production model for {season} yet; `uv run twm train "
            "waiver_radar` trains it (or the first `twm radar score` of the season)",
            details,
        )
    version = cur["model_version"]
    path = prod.model_path(version, ctx.models_dir)
    seasons = json.loads(cur["training_seasons"])
    details.insert(0, f"{version}: trained on {_span(seasons)}, file {_rel(path, ctx.root)}")
    if not path.exists():
        return Check(
            "WARN",
            f"Predictions store: the {season} production model {version} is recorded but its "
            "file is missing; `uv run twm train waiver_radar` rebuilds it",
            details,
        )
    return Check("PASS", f"Predictions store: the {season} production model is recorded and "
                 "its file exists", details)  # fmt: skip


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------


def _safe(name: str, fn: Callable[[], Check | None]) -> Check | None:
    """Run one check; a check that crashes is reported as a FAIL line, never a traceback."""
    try:
        return fn()
    except Exception as e:  # the doctor must always finish and say what it found
        first = (str(e).strip().splitlines() or [""])[0]
        return Check("FAIL", f"{name}: the check itself failed ({type(e).__name__}: {first})")


def run_checks(ctx: Context | None = None) -> list[Check]:
    """Every check, in order. Without a context, the real checkout's (needs a loadable
    settings.yaml; if it does not load, only the config, .env and virtualenv checks run).
    When the config fails, the predictions-store check is skipped (it needs the league)."""
    from twm.config import ROOT, config_dir

    if ctx is None:
        try:
            ctx = default_context()
        except Exception:  # settings.yaml does not load: say why and stop there
            bare = Context(
                root=ROOT, config_dir=config_dir(), env_file=ROOT / ".env",
                env_example=ROOT / ".env.example", gitignore=ROOT / ".gitignore",
                site_packages=_venv_site_packages(ROOT), links={}, cloud_synced=False,
                raw_dir=ROOT / "data" / "raw", schema_dir=ROOT / "data" / "schemas",
                warehouse=ROOT / "data" / "warehouse.duckdb",
                store=ROOT / "data" / "predictions.duckdb", models_dir=ROOT / "models",
                dataset=ROOT / "data" / "waiver_radar" / "dataset.parquet",
                current_season=None, now=datetime.now(UTC),
            )  # fmt: skip
            first = (
                _safe("Config", lambda: check_config(bare)),
                _safe(".env", lambda: check_env(bare)),
                _safe("Virtualenv", lambda: check_hidden_pth(bare)),
            )
            return [x for x in first if x is not None]
    c = ctx
    checks: list[Check] = []
    config = _safe("Config", lambda: check_config(c))
    assert config is not None
    checks.append(config)
    steps: list[tuple[str, Callable[[], Check | None]]] = [
        (".env", lambda: check_env(c)),
        ("Virtualenv", lambda: check_hidden_pth(c)),
        ("Local storage", lambda: check_links(c)),
        ("nflreadpy", lambda: check_nflreadpy(c)),
        ("Raw cache", lambda: check_cache_coverage(c)),
        ("Cache freshness", lambda: check_freshness(c)),
    ]
    for name, fn in steps:
        out = _safe(name, fn)
        if out is not None:
            checks.append(out)
    db: list[Path | None] = [None]

    def warehouse() -> Check:
        chk, db[0] = check_warehouse(c)
        return chk

    for name, fn in (("Warehouse", warehouse), ("Player ids", lambda: check_ids(c, db[0]))):
        out = _safe(name, fn)
        if out is not None:
            checks.append(out)
    if config.status == "FAIL":
        checks.append(Check("WARN", "Predictions store: skipped until the config loads"))
    else:
        out = _safe("Predictions store", lambda: check_store(c))
        if out is not None:
            checks.append(out)
    return checks


def summary(checks: Sequence[Check]) -> str:
    n = {s: sum(c.status == s for c in checks) for s in STATUSES}
    word = "FAILED" if n["FAIL"] else "ok"
    return f"{word}: {n['PASS']} passed, {n['WARN']} warning(s), {n['FAIL']} failed"

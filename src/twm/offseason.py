"""`twm offseason status` (step I6b): where the yearly routine of docs/offseason.md stands.
Read-only: it reads the pins (config/production_models.yaml), the warehouse (the last season
whose Super Bowl is in it, the next season's week-1 kickoff), the owner's departure labels and
the predictions store (a stored live board), and changes nothing.

Per pinned module: the season it is approved for against the season due (the season after the
last finished one), whether its frozen history covers the finished season (the board's history
is in snapshot seasons: snapshot S makes the board of S + 1), and the step that follows. The
first step not done yet is printed as "next", in the checklist's order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

# The pinned modules in the checklist's order (docs/offseason.md step 2) and how each is
# re-approved on the owner's Mac (the commands of docs/deploy.md "The approved model ...").
MODULES: dict[str, tuple[str, str]] = {
    "waiver_radar": ("Waiver Radar", "uv run twm radar backtest; uv run twm radar evaluate; "
                     "uv run twm model pin waiver_radar --version <new version>"),
    "streamer_k": ("K streamer", "uv run twm streamer backtest --store <store>; "
                   "uv run twm streamer pin --store <store>"),
    "streamer_dst": ("D/ST streamer", "uv run twm streamer pin --store <store> --pos DST"),
    "regression_watch": ("Regression Watch", "uv run twm regression stability; uv run twm "
                         "regression backtest; uv run twm regression pin"),
    "decisions": ("Decision Report Card", "uv run twm decisions wp-backtest / "
                  "submodels-backtest --season S; grade; clock; uv run twm decisions pin"),
    "hot_seat": ("Hot-Seat Meter", "uv run twm hotseat backtest --labels verified; "
                 "uv run twm hotseat pin"),
    "board": ("Cliff board", "uv run twm board backtest --snapshot preseason; "
              "uv run twm board pin"),
    "questionable": ("Questionable outcomes", "uv run twm questionable build; "
                     "uv run twm questionable pin"),
    "startsit": ("Start/sit odds (local)", "uv run twm league startsit-pin"),
}  # fmt: skip
HISTORY_LAG = {"board": 1}  # the board's history is in snapshot seasons (the board of S + 1)
LABELLED_KINDS = ("in_season", "offseason")  # schedule candidates that need a label row


@dataclass(frozen=True)
class ModuleStatus:
    module: str
    pinned: int | None  # the season it is approved for (None: no pin)
    due: int | None  # the season after the last finished one
    history: str  # the frozen history's seasons, e.g. "2014-2025"
    covers: bool | None  # the history covers the finished season
    step: str  # "ok" or what to do

    @property
    def current(self) -> bool:
        return self.pinned is not None and self.pinned == self.due and bool(self.covers)


@dataclass(frozen=True)
class Labels:
    season: int
    rows: int  # rows of the season in coach_departures.csv
    verified: int
    missing: int  # schedule candidates of the season without a row

    @property
    def done(self) -> bool:
        return self.rows > 0 and self.verified == self.rows and self.missing == 0


@dataclass
class Status:
    finished: int | None  # the last season whose Super Bowl is in the warehouse
    current_season: int  # config/settings.yaml
    modules: list[ModuleStatus] = field(default_factory=list)
    labels: Labels | None = None
    board: str = ""  # the due season's live board (its window, or stored)
    next_step: str = ""


def history_end(seasons: str) -> int | None:
    """The last season of a pin's ``backtest.seasons`` ("2014-2025" -> 2025)."""
    tail = seasons.strip().split("-")[-1]
    return int(tail) if tail.isdigit() else None


def module_status(module: str, pin: Any | None, finished: int | None) -> ModuleStatus:
    """One module's line (``pin``: a :class:`twm.pins.Pin` or None)."""
    due = None if finished is None else finished + 1
    if pin is None:
        return ModuleStatus(module, None, due, "", None, f"not pinned: {MODULES[module][1]}")
    end = history_end(pin.backtest_seasons)
    covers = (
        None if end is None or finished is None else end + HISTORY_LAG.get(module, 0) >= finished
    )
    if due is None:
        step = "unknown: no finished season in the warehouse (twm ingest; twm build)"
    elif pin.season < due or not covers:
        step = f"re-approve for {due} on the Mac: {MODULES[module][1]}; review; commit"
    elif pin.season > due:
        step = f"ok (approved ahead, for {pin.season})"
    else:
        step = "ok"
    return ModuleStatus(module, pin.season, due, pin.backtest_seasons, covers, step)


def labels_status(labels: pl.DataFrame | None, candidates: pl.DataFrame | None,
                  season: int) -> Labels:  # fmt: skip
    """The departures of ``season`` in the owner's file (data/manual/coach_departures.csv) and
    the schedule candidates of that season without a row (docs/labeling_coaches.md)."""
    if labels is None or labels.height == 0:
        mine = pl.DataFrame(
            {"candidate_id": [], "verified_by_owner": []},
            schema={"candidate_id": pl.String, "verified_by_owner": pl.String},
        )
    else:
        mine = labels.filter(pl.col("last_season").cast(pl.Int64) == season)
    verified = mine.filter(pl.col("verified_by_owner").cast(pl.String).str.strip_chars() == "y")
    missing = 0
    if candidates is not None and candidates.height:
        want = candidates.filter((pl.col("last_season").cast(pl.Int64) == season)
                                 & pl.col("change_kind").is_in(LABELLED_KINDS))  # fmt: skip
        missing = len(set(want["candidate_id"].to_list()) - set(mine["candidate_id"].to_list()))
    return Labels(int(season), mine.height, verified.height, missing)


def build_status(
    pins: Mapping[str, Any],
    *,
    finished: int | None,
    current_season: int,
    labels: Labels | None,
    board: str = "",
    today: date | None = None,
    started: bool = False,
) -> Status:
    """The routine's state (module docstring) and the first step not done, in the checklist's
    order: (1) data of the finished season, current_season, its departure labels; (2) every
    module re-approved for the season due; (4) the checks; (5) the preseason live board.
    ``started``: the season due has kicked off (the weekly job's season)."""
    st = Status(finished, int(current_season), labels=labels, board=board)
    st.modules = [module_status(m, pins.get(m), finished) for m in MODULES]
    today = today or date.today()
    due = None if finished is None else finished + 1
    stale = [m for m in st.modules if not m.current and not m.step.startswith("ok")]
    late = today >= date(int(current_season) + 1, 2, 20)  # its Super Bowl should be in by then
    if due is None or due < current_season or (due == current_season and late):
        missing = current_season if due == current_season else current_season - 1
        st.next_step = (f"1. refresh the data: the {missing} Super Bowl is not in the warehouse "
                        "yet (uv run twm ingest; uv run twm build)")  # fmt: skip
    elif current_season < due:
        st.next_step = (f"1. set current_season: {due} in config/settings.yaml (the {finished} "
                        "season is over; commit it with the new pins, step 4)")  # fmt: skip
    elif labels is not None and not labels.done:
        st.next_step = (
            f"1. label the {labels.season} coach departures (uv run twm hotseat "
            "candidates; docs/labeling_coaches.md; uv run twm hotseat check-labels)"
        )
    elif stale:
        st.next_step = f"2. {st.modules.index(stale[0]) + 1}/7 {stale[0].module}: {stale[0].step}"
    elif started:
        st.next_step = (f"the {due} season is under way: the weekly job runs; the routine "
                        "starts again after its Super Bowl (step 1)")  # fmt: skip
    else:
        st.next_step = (
            "4. every module is approved for the season due: uv run twm model check "
            "all; uv run twm timemachine verify; then 5 (the preseason live board): "
            + (board or "uv run twm offseason status shows its window")
        )
    return st


def finished_season(db: Path | str) -> int | None:
    """The last season whose Super Bowl (with a result) is in the warehouse (read-only)."""
    import duckdb

    if not Path(db).exists():
        return None
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute("SELECT max(season) FROM fact_game WHERE game_type = 'SB' "
                          "AND result IS NOT NULL").fetchone()  # fmt: skip
    finally:
        con.close()
    return None if not row or row[0] is None else int(row[0])


def board_text(db: Path | str, store: Path | str, season: int, now: Any) -> tuple[str, bool]:
    """(the due season's live board: stored, or its window (:mod:`.modules.board.live_publish`);
    whether the season has kicked off)."""
    from twm.modules.board import live as bl
    from twm.modules.board import live_publish as lp

    w = lp.window(db, season, now)
    started = w.kickoff is not None and now >= w.kickoff
    kept = bl.live_rows(store, season)
    if kept:
        return f"the {season} live board is stored ({kept} rows): done", started
    return f"the {season} live board: {w.reason}", started


def _csv(path: Path) -> pl.DataFrame | None:
    return pl.read_csv(path, infer_schema=False) if path.exists() else None


def gather(now: Any) -> Status:
    """The status from the project's own files (config paths; everything opened read-only)."""
    from twm import pins as pn
    from twm import predictions as pr
    from twm.config import settings
    from twm.modules.hot_seat.cli import CANDIDATES_CSV, LABELS_CSV

    s = settings()
    db, manual = s.path("warehouse"), s.path("manual")
    finished = finished_season(db)
    labels = None if finished is None else labels_status(
        _csv(manual / LABELS_CSV), _csv(manual / CANDIDATES_CSV), finished)  # fmt: skip
    due = s.current_season if finished is None else max(finished + 1, s.current_season)
    try:
        board, started = board_text(db, pr.default_path(), due, now)
    except Exception as e:  # informative only (e.g. a warehouse without fact_game)
        board, started = f"the {due} live board: unknown ({e})", False
    return build_status(pn.read_pins(), finished=finished, current_season=s.current_season,
                        labels=labels, board=board, today=now.date(), started=started)  # fmt: skip


def status_text(st: Status) -> str:
    """The printed report: one line per module, the labels, the live board, then "next"."""
    due = None if st.finished is None else st.finished + 1
    lines = [f"finished season (Super Bowl in the warehouse): {st.finished or 'none'}; season "
             f"due: {due or '?'}; config current_season: {st.current_season}", ""]  # fmt: skip
    lines.append(f"{'module':<17} {'pinned':>6} {'due':>5}  {'history':<10} covers  step")
    for m in st.modules:
        cov = "?" if m.covers is None else ("yes" if m.covers else "NO")
        lines.append(f"{m.module:<17} {m.pinned or '-':>6} {m.due or '?':>5}  "
                     f"{m.history or '-':<10} {cov:<6}  {m.step}")  # fmt: skip
    if st.labels is not None:
        lb = st.labels
        lines += ["", f"coach departures of {lb.season}: {lb.rows} rows, {lb.verified} verified, "
                  f"{lb.missing} schedule candidates without a row: "
                  + ("labelled" if lb.done else "NOT labelled yet")]  # fmt: skip
    if st.board:
        lines += ["", st.board]
    lines += ["", f"next: {st.next_step}", "(docs/offseason.md has the whole routine)"]
    return "\n".join(lines)

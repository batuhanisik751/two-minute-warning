"""`twm radar lead-time` (feature #8, docs/lead_time.md): the study's aggregate tables in the
terminal. Reads the warehouse (read-only) and the approved Radar pin; writes nothing. Registered
on the Radar's sub-app by :mod:`twm.cli`. Prints aggregates only (the same frames a publish
carries); the per-player rows stay in :class:`twm.modules.lead_time.study.Study`."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import typer

from twm.modules.lead_time import PRIMARY, THRESHOLDS

SIGNAL_NAMES = {"must_add": "must-add", "spec_plus": "must-add or speculative",
                "listed": "listed (top 25)", "momentum": "momentum, +10 points"}  # fmt: skip
STATE_NAMES = {"already": "already rostered", "added_later": "added later", "never": "never added"}
H2H_ORDER = ("both", "radar_only", "momentum_only", "neither")


def pct(x: float | None) -> str:
    """A share as '61.2%' ('-': suppressed, fewer than 5 cases)."""
    return "-" if x is None else f"{100 * x:.1f}%"


def num(x: float | None) -> str:
    """A lead in periods: '3', '6.25', '-' (suppressed or none)."""
    if x is None:
        return "-"
    return f"{x:.2f}".rstrip("0").rstrip(".") if x != int(x) else f"{int(x)}"


def rows_text(head: list[str], rows: list[list[str]]) -> list[str]:
    """Left-aligned first column, right-aligned others, two spaces apart."""
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    out = []
    for r in [head, *rows]:
        rest = [c.rjust(w) for c, w in zip(r[1:], widths[1:], strict=True)]
        cells = [r[0].ljust(widths[0]), *rest]
        out.append("  " + "  ".join(cells).rstrip())
    return out


def coverage_lines(cov: pl.DataFrame) -> list[str]:
    rows = []
    for r in cov.iter_rows(named=True):
        span = (f"{r['first_in_season']} to {r['last_in_season']} ({r['in_season_days']} days)"
                if r["in_season_days"] else "none in season")  # fmt: skip
        status = ("complete" if r["complete"] and r["in_study"] else
                  "partial" if r["in_study"] else "excluded")  # fmt: skip
        adds = "-" if r["adds_50"] is None else f"{r['adds_50']} / {r['adds_25']}"
        rows.append([str(r["season"]), span, f"{r['baseline_date']} ({r['baseline_period']})",
                     adds, status])  # fmt: skip
    head = ["season", "ESPN % in season", "baseline (period)", "adds 50% / 25%", "status"]
    return ["Coverage (seasons with ESPN roster % in the warehouse):", *rows_text(head, rows)]


def summary_lines(s: pl.DataFrame, threshold: float, cov: pl.DataFrame) -> list[str]:
    full = s.filter((pl.col("threshold") == threshold) & (pl.col("scope") == "complete"))
    if full.height == 0:
        return [f"No crowd adds at {threshold:.0f}%."]
    n = int(full.get_column("n_adds").max())  # type: ignore[arg-type]
    full_seasons = cov.filter(pl.col("complete") & pl.col("in_study")).get_column("season")
    span = f"{full_seasons.min()}-{full_seasons.max()}" if full_seasons.len() else "none"
    rows = []
    for sig in SIGNAL_NAMES:
        for r in full.filter(pl.col("signal") == sig).iter_rows(named=True):
            iqr = f"{num(r['lead_q1'])} to {num(r['lead_q3'])}"
            rows.append([SIGNAL_NAMES[sig], *(pct(r[f"share_{c}"]) for c in
                         ("before", "same", "after", "never")), f"{num(r['lead_median'])} ({iqr})",
                         num(r["nearest_median"])])  # fmt: skip
    head = ["signal", "before", "same", "after", "never", "lead median (Q1-Q3)", "nearest"]
    return [f"Crowd at {threshold:.0f}%, complete seasons {span} pooled ({n} crowd adds):",
            *rows_text(head, rows)]  # fmt: skip


def season_lines(s: pl.DataFrame, threshold: float, cov: pl.DataFrame) -> list[str]:
    by = s.filter((pl.col("threshold") == threshold) & (pl.col("scope") == "season"))
    seasons = sorted(set(by.get_column("season").to_list()))
    rows = []
    for sig in SIGNAL_NAMES:
        one = dict(by.filter(pl.col("signal") == sig).select("season", "share_before").iter_rows())
        rows.append([SIGNAL_NAMES[sig], *(pct(one.get(y)) for y in seasons)])
    head = ["flagged before, by season", *(str(y) for y in seasons)]
    part = cov.filter(pl.col("in_study") & ~pl.col("complete"))
    notes = [f"  ({r['season']} is partial: its baseline is waiver period {r['baseline_period']}; "
             "never pooled)" for r in part.iter_rows(named=True)]  # fmt: skip
    return [*rows_text(head, rows), *notes]


def hist_lines(h: pl.DataFrame, s: pl.DataFrame, threshold: float) -> list[str]:
    out = ["Lead in waiver periods (complete seasons; -6 = six or more after, 10 = ten or more "
           "before):"]  # fmt: skip
    for sig in ("listed", "must_add", "momentum"):
        one = h.filter((pl.col("threshold") == threshold) & (pl.col("signal") == sig))
        bins = " ".join(f"{r['lead']}:{r['n']}" for r in one.iter_rows(named=True)
                        if r["lead"] is not None)  # fmt: skip
        never = s.filter((pl.col("threshold") == threshold) & (pl.col("signal") == sig)
                         & (pl.col("scope") == "complete")).get_column("n_never")  # fmt: skip
        tail = f"; never {never[0]}" if never.len() and never[0] is not None else ""
        out.append(f"  {SIGNAL_NAMES[sig]}: {bins}{tail}")
    return out


def reverse_lines(rv: pl.DataFrame, threshold: float) -> list[str]:
    full = rv.filter((pl.col("threshold") == threshold) & (pl.col("scope") == "complete"))
    rows = []
    for sig in SIGNAL_NAMES:
        one = {r["state"]: r for r in full.filter(pl.col("signal") == sig).iter_rows(named=True)}
        cells = []
        for st in STATE_NAMES:
            r = one.get(st)
            hit = "" if r is None or r["hit_rate"] is None else f", hit {pct(r['hit_rate'])}"
            cells.append("-" if r is None else f"{r['n']}{hit}")
        rows.append([SIGNAL_NAMES[sig], *cells])
    head = ["first flags by what the crowd did", *STATE_NAMES.values()]
    return ["Reverse view (each signal's first flag of a player; hit = the Radar's own label):",
            *rows_text(head, rows)]  # fmt: skip


def conversion_lines(cv: pl.DataFrame, threshold: float) -> list[str]:
    full = cv.filter((pl.col("threshold") == threshold) & (pl.col("scope") == "complete"))
    rows = [[SIGNAL_NAMES[r["signal"]], str(r["n_flags"]), f"{r['flags_per_week']:.1f}",
             pct(r["share_added"]), pct(r["share_added_after"])]
            for r in full.iter_rows(named=True)]  # fmt: skip
    head = ["flags of players under the threshold", "flags", "per week", "added", "added later"]
    return ["Volume and conversion:", *rows_text(head, rows)]


def h2h_lines(h: pl.DataFrame, threshold: float) -> list[str]:
    one = h.filter(pl.col("threshold") == threshold)
    rows = []
    for lv in ("listed", "spec_plus", "must_add"):
        d = {r["h2h"]: r for r in one.filter(pl.col("level") == lv).iter_rows(named=True)}
        both = d.get("both")
        cells = [str(d[k]["n"]) if k in d else "0" for k in H2H_ORDER]
        if both is not None:
            cells[0] += f" (Radar earlier {both['n_radar_earlier']})"
        rows.append([SIGNAL_NAMES[lv], *cells])
    head = ["Radar level vs momentum", "both before", "Radar only", "momentum only", "neither"]
    return ["Head to head on the same crowd adds:", *rows_text(head, rows)]


def report_lines(frames: dict[str, pl.DataFrame], threshold: float) -> list[str]:
    """The whole printout from :func:`~twm.modules.lead_time.study.build_frames`'s frames."""
    s, others = frames["summary"], [t for t in THRESHOLDS if t != threshold]
    out = ["Lead time vs the crowd (docs/lead_time.md): the Radar's first flag vs the waiver "
           "period most ESPN leagues rostered the player (+ = the Radar first).", ""]  # fmt: skip
    out += coverage_lines(frames["coverage"]) + [""]
    out += summary_lines(s, threshold, frames["coverage"])
    for t in others:
        out += [""] + summary_lines(s, t, frames["coverage"])
    out += [""] + season_lines(s, threshold, frames["coverage"])
    out += [""] + hist_lines(frames["hist"], s, threshold)
    out += [""] + h2h_lines(frames["h2h"], threshold)
    out += [""] + reverse_lines(frames["reverse"], threshold)
    out += [""] + conversion_lines(frames["conversion"], threshold)
    out += [
        "",
        "'-' = fewer than 5 cases (no statistic). Source: ESPN roster % as FantasyPros "
        "scraped it (aggregates only: its license forbids republishing per-player values).",
    ]
    return out


def lead_time_cmd(
    threshold: int = typer.Option(
        int(PRIMARY), "--threshold", help="The crowd's add threshold, % rostered (50 or 25)."
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
) -> None:
    """Lead time vs the crowd (feature #8, docs/lead_time.md): how many weeks before most ESPN
    leagues rostered a player the Radar flagged him (2021-2023 pooled; 2020 partial), against a
    simple roster-trend baseline, and what became of the must-adds the crowd never added.
    Reads the warehouse and the approved Radar pin (about 2 s); writes nothing."""
    from twm.config import settings
    from twm.modules.lead_time import study as st

    if float(threshold) not in THRESHOLDS:
        typer.echo(f"--threshold must be one of {', '.join(f'{t:.0f}' for t in THRESHOLDS)}",
                   err=True)  # fmt: skip
        raise typer.Exit(code=2)
    path = Path(db) if db is not None else settings().path("warehouse")
    if not path.exists():
        typer.echo(f"warehouse not found: {path}; run `twm build` first", err=True)
        raise typer.Exit(code=1)
    for line in report_lines(st.build_frames(st.run(path)), float(threshold)):
        typer.echo(line)

"""Made-up Teammate-out snapshots for `twm publish` tests (feature #5), added to the Waiver Radar's
synthetic publish data (tests/publish_synthetic.py): snapshots stored with the module's own
writer (weekly.store_snapshot) in a small predictions store, the pinned table's tables (the
committed pin) and a live record graded with made-up player-games. Deterministic.

2026 week 5: BUF (vs KC) has its RB1 out (Out), three teammates listed; DAL (vs SF) has its WR1
(reserve list) and TE1 (Doubtful) out, two teammates listed. Later weeks repeat it a week later."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from tests.publish_synthetic import gid
from twm.modules.teammate_out import production as tp
from twm.modules.teammate_out import weekly as wk
from twm.publish import teammate_out as tn
from twm.publish.collect import PublishData

CREATED = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # as-of of the first snapshot (2026 week 5)
KICKOFF = datetime(2026, 10, 11, 17, 0)  # naive UTC, as the store keeps it
# (team, opponent, out ids, out names, out positions, out reasons, vacated carry / target share)
GAMES = (
    ("BUF", "KC", [gid(1, 0)], "Player 1-0", "RB", "Out", 0.55, 0.10),
    ("DAL", "SF", [gid(2, 0), gid(3, 0)], "Player 2-0, Player 3-0", "WR, TE",
     "roster RES, Doubtful", 0.0, 0.42),
)  # fmt: skip
# (game index, teammate id, position, role, base points, predicted gain)
MATES = (
    (0, gid(1, 1), "RB", "RB2", 6.0, 5.0),
    (0, gid(1, 2), "RB", "RB3", 2.0, 2.5),
    (0, gid(2, 1), "WR", "WR1", 12.0, -0.5),
    (1, gid(2, 2), "WR", "WR2", 8.0, 3.0),
    (1, gid(3, 1), "TE", "TE2", 3.0, 2.0),
)
OUT_IDS = [gid(1, 0), gid(2, 0), gid(3, 0)]
MATE_IDS = [m[1] for m in MATES]


def list_rows(shift: float = 0.0, week: int = 5) -> pl.DataFrame:
    """The listed teammates (store layout, weekly.list_rows' columns); ``shift`` moves every
    predicted number; ``week``: 2026 week 5 (kickoffs from KICKOFF) or a week later each."""
    rows = []
    for g, mate, pos, role, base, gain in MATES:
        team, opp, ids, names, poss, reasons, vac_c, vac_t = GAMES[g]
        pred = base + gain + shift
        rows.append({
            "season": 2026, "week": week, "team": team, "opponent": opp,
            "game_id": f"2026_{week:02d}_{opp}_{team}",
            "kickoff_utc": KICKOFF + timedelta(days=7 * (week - 5), hours=3 * g),
            "out_ids": ",".join(ids), "out_players": names, "out_positions": poss,
            "out_reasons": reasons, "n_out": len(ids), "vac_carry_share": vac_c,
            "vac_target_share": vac_t, "gsis_id": mate, "player": f"Mate {mate[-2:]}",
            "position": pos, "role": role, "base_games": 3, "base_carry_share": 0.2,
            "base_target_share": 0.1, "base_snap_share": 0.5, "base_points": base,
            "base_team_carries": 25.0, "base_team_targets": 33.0,
            "pred_carry_share": 0.2 + gain / 50, "pred_target_share": 0.1 + gain / 100,
            "carry_share_change": gain / 50, "target_share_change": gain / 100,
            "pred_points": round(pred, 6), "points_lo": round(max(0.0, pred - 4), 6),
            "points_hi": round(pred + 6, 6), "pred_gain": round(gain + shift, 6),
            "alloc_carry_share": 0.2 + gain / 50, "alloc_target_share": 0.1 + gain / 100,
            "source": "observed",
        })  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None).select(wk.LIST_COLUMNS)


def write_store(store: Path, as_ofs: tuple[datetime, ...] = (FRIDAY,), *, shift: float = 0.0,
                version: str = "alloc-test", week: int = 5) -> Path:  # fmt: skip
    """Snapshots of 2026 ``week`` at each as-of (append-only: a stored as-of is kept)."""
    for t in as_ofs:
        wk.store_snapshot(store, list_rows(shift, week), season=2026, week=week, as_of=t,
                          model_version=version, created_at=CREATED)  # fmt: skip
    return store


def actuals(points: dict[str, float] | None = None, *, week: int = 5,
            starters: tuple[str, ...] = ()) -> pl.DataFrame:  # fmt: skip
    """weekly.read_actuals rows of 2026 ``week``: BUF's game is in by default (its three
    teammates played: ``points``, default 12 / 3 / 10), DAL's is not; ``starters``: absent
    starters who played after all."""
    got = points or {gid(1, 1): 12.0, gid(1, 2): 3.0, gid(2, 1): 10.0}
    team = {m[1]: GAMES[m[0]][0] for m in MATES} | {i: "BUF" for i in OUT_IDS[:1]}
    team |= {i: "DAL" for i in OUT_IDS[1:]}
    rows = [(i, p, True) for i, p in got.items()] + [(i, 5.0, True) for i in starters]
    return pl.DataFrame({
        "season": [2026] * len(rows), "week": [week] * len(rows),
        "team": [team[i] for i, _, _ in rows], "gsis_id": [i for i, _, _ in rows],
        "played": [p for _, _, p in rows], "carry_share": [0.3] * len(rows),
        "target_share": [0.15] * len(rows), "points": [p for _, p, _ in rows],
    }, schema=tn.ACTUALS)  # fmt: skip


def add_teammate_out(data: PublishData, store: Path, *, allocation: int | None = None,
                     played: pl.DataFrame | None = None) -> PublishData:  # fmt: skip
    """``data`` with the snapshots of ``store`` and the module's tables (the real pin's;
    ``allocation``: keep only that many rows), the live record graded with ``played`` (default
    :func:`actuals`: BUF's game is in)."""
    spec, _ = tp.load_pinned(2026)
    snaps = wk.read_snapshots(store)
    lists, rows = tn.snapshot_frames(snaps)
    tables = tn.pin_tables(spec.content)
    if allocation is not None:
        tables["teammate_out_allocation"] = tables["teammate_out_allocation"].head(allocation)
    versions = sorted(set(lists.get_column("model_version").to_list()))
    vrows = [tn.version_row({**spec.content, "model_version": v}, CREATED) for v in versions]
    d = tn.TeammateOutData(2026, versions[0] if versions else "", lists, rows, tables,
                           names=tn.out_names(snaps),
                           actuals=actuals() if played is None else played)  # fmt: skip
    tables["teammate_out_live"] = tn.live_record(d, tn.no_published())
    data.tables.update(tables)
    data.tables["model_versions"] = pl.concat([data.tables["model_versions"], *vrows],
                                              how="vertical_relaxed")  # fmt: skip
    data.teammate_out = d
    return data

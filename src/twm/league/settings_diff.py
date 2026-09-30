"""`twm league settings-diff`: the league's real ESPN settings (the last `twm league sync`)
against config/scoring.yaml and config/league.yaml (step F2).

Prints a plain table of differences (every scoring item incl. K and D/ST, the points- and
yards-allowed tiers, the lineup slots, the number of teams), the settings no config key can
express (to decide by hand), and a suggested YAML patch. It never edits the config: the owner
decides. ESPN stat ids and labels are espn-api's SETTINGS_SCORING_FORMAT_MAP
(football/constant.py:271); a stat the league does not list scores 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import duckdb

from twm.config import League, Scoring

EPS = 1e-9


@dataclass(frozen=True)
class Diff:
    file: str  # scoring.yaml / league.yaml
    item: str  # the config key, e.g. passing.yards, defense.points_allowed_tiers, lineup.IR
    espn: object  # the league's value in the config's terms
    config: object
    note: str = ""


@dataclass(frozen=True)
class Espn:
    """The synced scoring items: stat id -> (points for a player, D/ST override or None)."""

    items: Mapping[int, tuple[float, float | None]]
    labels: Mapping[int, str]

    def has(self, *sids: int) -> bool:
        return any(s in self.items for s in sids)

    def pts(self, sid: int) -> float:
        return self.items[sid][0] if sid in self.items else 0.0

    def dst(self, sid: int) -> float:
        if sid not in self.items:
            return 0.0
        base, over = self.items[sid]
        return base if over is None else over


# config key -> (per-unit stat, {"every N units" stat: N}) : ESPN scores yards either per yard
# or per whole block of N (same rate, rounded per game).
RATES: dict[str, tuple[int, dict[int, int]]] = {
    "passing.yards": (3, {5: 5, 6: 10, 7: 20, 8: 25, 9: 50, 10: 100}),
    "rushing.yards": (24, {27: 5, 28: 10, 29: 20, 30: 25, 31: 50, 32: 100}),
    "receiving.receptions": (53, {54: 5, 55: 10}),
    "receiving.yards": (42, {47: 5, 48: 10, 49: 20, 50: 25, 51: 50, 52: 100}),
}
SIMPLE: dict[str, int] = {
    "passing.touchdowns": 4, "passing.interceptions": 20, "passing.two_point_conversions": 19,
    "rushing.touchdowns": 25, "rushing.two_point_conversions": 26,
    "receiving.touchdowns": 43, "receiving.two_point_conversions": 44,
    "misc.fumble_recovery_touchdowns": 63,
    "kicking.pat_made": 86, "kicking.pat_missed": 88,
}  # fmt: skip
DEFENSE: dict[str, int] = {  # D/ST items: the D/ST override when the league sets one
    "sacks": 99, "interceptions": 95, "fumble_recoveries": 96, "blocked_kicks": 97,
    "safeties": 98, "kickoff_return_tds": 101, "punt_return_tds": 102,
    "interception_return_tds": 103, "fumble_return_tds": 104, "blocked_kick_return_tds": 93,
}  # fmt: skip
PA = ((0, 89), (6, 90), (13, 91), (17, 92), (21, 121), (27, 122), (34, 123), (45, 124),
      (None, 125))  # fmt: skip
DPA = (
    (0, 188),
    (6, 189),
    (13, 190),
    (17, 191),
    (21, 192),
    (27, 193),
    (34, 194),
    (45, 195),
    (None, 196),
)  # "D/ST n points allowed": used instead of PA when the league lists them
YA = ((99, 128), (199, 129), (299, 130), (349, 131), (399, 132), (449, 133), (499, 134),
      (549, 135), (None, 136))  # fmt: skip
FUMBLES_LOST_PARTS = (69, 70, 71)  # passing / rushing / receiving fumbles lost
FG_MISSED_BY_DISTANCE = (82, 79, 76, 200, 203)
FG_TOTAL_MADE = 83  # "Total FG Made": adds to every distance bucket
DEF_RETURN_TD = 94  # "Fumble or INT Return for TD"
TOTAL_RETURN_TD = 105


def _same(a: object, b: object) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < EPS
    return a == b


def merge_tiers(tiers: list[tuple[int | None, float]]) -> list[tuple[int | None, float]]:
    """Adjacent tiers worth the same points merged (ESPN lists 18-21 and 22-27 apart, the
    config may hold them as one); all-zero tiers = no tiers (the category is off)."""
    out: list[tuple[int | None, float]] = []
    for bound, pts in tiers:
        if out and _same(out[-1][1], pts):
            out[-1] = (bound, out[-1][1])
        else:
            out.append((bound, float(pts)))
    return [] if all(_same(p, 0) for _, p in out) else out


def _tiers(e: Espn, table: tuple) -> list[tuple[int | None, float]]:
    return merge_tiers([(b, e.dst(sid)) for b, sid in table])


def _config(sc: Scoring, key: str) -> object:
    section, name = key.split(".")
    if section in ("passing", "rushing", "receiving", "misc"):
        v = getattr(sc, section).get(name)
        return None if v is None else float(v)
    if section == "options":
        return getattr(sc.options, name)
    obj = getattr(sc, section)
    if obj is None:
        return None
    v = getattr(obj, name)
    return merge_tiers([(b, p) for b, p in v]) if name.endswith("_tiers") else float(v)


def espn_values(e: Espn) -> tuple[dict[str, object], dict[str, str], set[int]]:
    """(config key -> the league's value, config key -> note, stat ids used)."""
    val: dict[str, object] = {}
    note: dict[str, str] = {}
    used = {FG_TOTAL_MADE, DEF_RETURN_TD, TOTAL_RETURN_TD, 72, 101, 102, 80, 77, 198, 201, 74,
            85, 100, 120, 127, 187, *FUMBLES_LOST_PARTS, *FG_MISSED_BY_DISTANCE}  # fmt: skip
    for key, (per, blocks) in RATES.items():
        val[key] = e.pts(per) + sum(e.pts(s) / n for s, n in blocks.items())
        used |= {per, *blocks}
        if e.has(*blocks):
            note[key] = "ESPN scores whole blocks of yards (per game); the app scores per yard"
    for key, sid in SIMPLE.items():
        val[key] = e.pts(sid)
        used.add(sid)
    parts = {e.pts(p) for p in FUMBLES_LOST_PARTS if e.has(p)}
    val["misc.fumbles_lost"] = e.pts(72) + (min(parts) if parts else 0.0)
    if len(parts) > 1:
        note["misc.fumbles_lost"] = "passing / rushing / receiving fumbles lost differ in ESPN"
    if e.has(*FUMBLES_LOST_PARTS) and not e.has(72):
        val["options.fumbles_lost_scope"] = "scrimmage"
        note["options.fumbles_lost_scope"] = "the league scores only passing/rushing/receiving "
        note["options.fumbles_lost_scope"] += "fumbles lost (none on returns)"
    kr, pr = e.pts(101), e.pts(102)
    val["misc.special_teams_touchdowns"] = kr
    if not _same(kr, pr):
        note["misc.special_teams_touchdowns"] = f"kick return TD {kr:g}, punt return TD {pr:g}"
    total = e.pts(FG_TOTAL_MADE)
    val["kicking.fg_made_0_39"] = e.pts(80) + total
    val["kicking.fg_made_40_49"] = e.pts(77) + total
    val["kicking.fg_made_50_59"] = e.pts(198 if e.has(198) else 74) + total
    val["kicking.fg_made_60_plus"] = e.pts(201 if e.has(201) else 74) + total
    dist = [e.pts(s) for s in FG_MISSED_BY_DISTANCE if e.has(s)]
    val["kicking.fg_missed"] = e.pts(85) + (dist[0] if dist else 0.0)
    if len({round(d, 9) for d in dist}) > 1:
        note["kicking.fg_missed"] = "ESPN scores misses by distance; the app has one value"
    return val, note, used


def defense_values(e: Espn) -> tuple[dict[str, object], dict[str, str], set[int]]:
    note: dict[str, str] = {}
    used = set(DEFENSE.values()) | {sid for t in (PA, DPA, YA) for _, sid in t}
    pts = {name: e.dst(sid) for name, sid in DEFENSE.items()}
    for name in ("interception_return_tds", "fumble_return_tds"):
        pts[name] += e.dst(DEF_RETURN_TD)
    for name in pts:
        if name.endswith("return_tds"):
            pts[name] += e.dst(TOTAL_RETURN_TD)
    if e.has(100):
        note["defense.sacks"] = f"the league also scores half sacks ({e.dst(100):g} each)"
    val: dict[str, object] = {f"defense.{k}": v for k, v in pts.items()}
    pa = DPA if e.has(*(sid for _, sid in DPA)) else PA
    val["defense.points_allowed_tiers"] = _tiers(e, pa)
    val["defense.yards_allowed_tiers"] = _tiers(e, YA)
    return val, note, used


def scoring_diffs(e: Espn, sc: Scoring) -> tuple[list[Diff], list[str]]:
    """(differences a config key can fix, notes to decide by hand)."""
    v1, n1, u1 = espn_values(e)
    v2, n2, u2 = defense_values(e)
    values, notes, used = {**v1, **v2}, {**n1, **n2}, u1 | u2
    diffs: list[Diff] = []
    for key, espn in values.items():
        cfg = _config(sc, key)
        if cfg is None and key.split(".")[0] in ("kicking", "defense"):
            cfg = "(section missing)"
        if not _same(espn, cfg):
            diffs.append(Diff("scoring.yaml", key, espn, cfg, notes.pop(key, "")))
    if e.has(72) and not e.has(*FUMBLES_LOST_PARTS):
        notes.setdefault("options.fumbles_lost_scope", (
            "ESPN scores 'Total Fumbles Lost' (stat 72); the settings do not say whether a "
            "fumble lost on a kick or punt return counts (the app's `all` assumes it does)"
        ))  # fmt: skip
    k = sc.kicking
    notes.setdefault("kicking.fg_blocked", (
        "ESPN has no blocked-FG category; the app scores a block like a miss (fg_blocked = "
        f"{k.fg_blocked:g}): check one blocked kick in a box score" if k else "no kicking: "
        "section in scoring.yaml"))  # fmt: skip
    for sid in (120, 127, 187):
        if e.has(sid):
            notes[f"stat {sid}"] = f"the league scores {e.labels.get(sid, sid)} per point/yard"
    for sid, (pts, over) in sorted(e.items.items()):
        if sid not in used and not (_same(pts, 0) and (over is None or _same(over, 0))):
            extra = "" if over is None else f" (D/ST {over:g})"
            notes[f"stat {sid}"] = (f"the league scores {e.labels.get(sid, 'stat')} "
                                    f"{pts:g}{extra}; the app does not score it")  # fmt: skip
    return diffs, [f"{k}: {v}" for k, v in notes.items()]


# ESPN lineup slot labels (football/constant.py:1 POSITION_MAP) -> config/league.yaml
DIRECT_SLOTS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D/ST": "DST",
                "BE": "bench", "IR": "IR"}  # fmt: skip
CONFIG_ALIASES = {"BE": "bench", "D/ST": "DST", "DEF": "DST"}
MULTI_SLOTS = {"RB/WR/TE": ("RB", "TE", "WR"), "RB/WR": ("RB", "WR"), "WR/TE": ("TE", "WR"),
               "OP": ("QB", "RB", "TE", "WR")}  # fmt: skip
MULTI_NAMES = {("RB", "TE", "WR"): "FLEX", ("QB", "RB", "TE", "WR"): "SUPERFLEX",
               ("RB", "WR"): "RB_WR", ("TE", "WR"): "WR_TE"}  # fmt: skip
SLOT_ORDER = ("QB", "RB", "WR", "TE", "K", "DST", "bench", "IR")


def lineup_diffs(slots: Mapping[str, int], teams: int | None, lg: League) -> tuple[
    list[Diff], list[str]
]:  # fmt: skip
    """ESPN's roster slots and team count against league.yaml's lineup and teams."""
    diffs: list[Diff] = []
    notes: list[str] = []
    if teams is not None and teams != lg.teams:
        diffs.append(Diff("league.yaml", "teams", teams, lg.teams))
    cfg_direct: dict[str, int] = {}
    cfg_multi: dict[tuple[str, ...], tuple[str, int]] = {}
    for name, n in lg.lineup.items():
        if name in lg.slot_eligibility:
            key = tuple(sorted(lg.slot_eligibility[name]))
            first, total = cfg_multi.get(key, (name, 0))
            cfg_multi[key] = (first, total + n)
        else:
            d = CONFIG_ALIASES.get(name, name)
            cfg_direct[d] = cfg_direct.get(d, 0) + n
    espn_direct: dict[str, int] = {}
    espn_multi: dict[tuple[str, ...], int] = {}
    for label, n in slots.items():
        if label in DIRECT_SLOTS:
            espn_direct[DIRECT_SLOTS[label]] = n
        elif label in MULTI_SLOTS:
            espn_multi[MULTI_SLOTS[label]] = n
        else:
            notes.append(f"lineup: the league has {n} ESPN slot(s) {label!r} the app lacks")
    rank = {s: i for i, s in enumerate(SLOT_ORDER)}
    for name in sorted(set(espn_direct) | set(cfg_direct), key=lambda s: (rank.get(s, 99), s)):
        e, c = espn_direct.get(name, 0), cfg_direct.get(name, 0)
        if e != c:
            diffs.append(Diff("league.yaml", f"lineup.{name}", e, c))
    for key in sorted(set(espn_multi) | set(cfg_multi)):
        name, c = cfg_multi.get(key, (MULTI_NAMES.get(key, "_".join(key)), 0))
        e = espn_multi.get(key, 0)
        if e != c:
            new = "" if name in lg.slot_eligibility else f"; add slot_eligibility.{name}"
            diffs.append(Diff("league.yaml", f"lineup.{name}", e, c, f"holds {'/'.join(key)}{new}"))
    return diffs, notes


@dataclass(frozen=True)
class Synced:
    """The league's settings as the last sync stored them (data/league.duckdb)."""

    season: int
    espn: Espn
    slots: dict[str, int]
    teams: int | None
    other: dict[str, str]  # faab, budget and ESPN's raw waiver keys (information only)


def load(con: duckdb.DuckDBPyConnection) -> Synced | None:
    """The latest synced season's settings, or None before the first sync."""
    row = con.execute(
        "SELECT league_id, season FROM league_settings ORDER BY synced_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    where = " WHERE league_id = ? AND season = ?"
    items = con.execute(
        "SELECT stat_id, points, dst_points, label FROM league_scoring" + where, list(row)
    ).fetchall()
    kv = dict(con.execute("SELECT key, value FROM league_settings" + where, list(row)).fetchall())
    return Synced(
        season=int(row[1]),
        espn=Espn({int(s): (float(p), d) for s, p, d, _ in items},
                  {int(s): lab for s, _, _, lab in items}),
        slots={k.removeprefix("slot:"): int(v) for k, v in kv.items() if k.startswith("slot:")},
        teams=int(kv["team_count"]) if "team_count" in kv else None,
        other={k: v for k, v in kv.items()
               if k.startswith("waiver:") or k in ("faab", "acquisition_budget")},
    )  # fmt: skip


def fmt(v: object) -> str:
    if v is None:
        return "(not set)"
    if isinstance(v, list):
        return "[" + ", ".join(f"[{'null' if b is None else b}, {p:g}]" for b, p in v) + "]"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def table(diffs: list[Diff]) -> list[str]:
    head = ("file", "setting", "ESPN league", "config", "note")
    rows = [head] + [(d.file, d.item, fmt(d.espn), fmt(d.config), d.note) for d in diffs]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(c.ljust(w) for c, w in zip(r[:4], widths, strict=True)) + "  " + r[4])
        if i == 0:
            out.append("  ".join("-" * w for w in widths) + "  ----")
    return [line.rstrip() for line in out]


def yaml_patch(diffs: list[Diff]) -> list[str]:
    """A suggested patch, file by file (the owner copies what they agree with)."""
    out: list[str] = []
    for file in ("scoring.yaml", "league.yaml"):
        mine = [d for d in diffs if d.file == file]
        if not mine:
            continue
        out.append(f"# config/{file} (suggested; `twm league settings-diff` never edits it)")
        sections: dict[str, list[Diff]] = {}
        for d in mine:
            head = d.item.split(".")[0] if "." in d.item else ""
            sections.setdefault(head, []).append(d)
        for head, ds in sections.items():
            if not head:
                out += [f"{d.item}: {fmt(d.espn)}  # config: {fmt(d.config)}" for d in ds]
                continue
            out.append(f"{head}:")
            for d in ds:
                name = d.item.split(".", 1)[1]
                if isinstance(d.espn, list):
                    out.append(f"  {name}:" + ("" if d.espn else " []"))
                    out += [f"    - [{'null' if b is None else b}, {p:g}]" for b, p in d.espn]
                else:
                    out.append(f"  {name}: {fmt(d.espn)}  # config: {fmt(d.config)}")
            for d in ds:
                if "add slot_eligibility." in d.note:
                    name = d.item.split(".", 1)[1]
                    held = d.note.split("holds ", 1)[1].split(";")[0].split("/")
                    out += ["slot_eligibility:", f"  {name}: [{', '.join(held)}]"]
    return out


def report(synced: Synced, sc: Scoring, lg: League) -> tuple[list[str], int]:
    """(output lines, number of differences)."""
    d1, n1 = scoring_diffs(synced.espn, sc)
    d2, n2 = lineup_diffs(synced.slots, synced.teams, lg)
    diffs, notes = d1 + d2, n1 + n2
    lines = [f"League settings from the last sync ({synced.season}) vs config/scoring.yaml and "
             "config/league.yaml:"]  # fmt: skip
    lines += table(diffs) if diffs else ["No differences: the config matches the league."]
    if notes:
        lines += ["", "To decide by hand (no config key can say it):"] + [f"- {n}" for n in notes]
    if synced.other:
        lines += ["", "Waiver settings (information: the Radar's deadline, docs/progress.md):"]
        lines += [f"- {k}: {v}" for k, v in sorted(synced.other.items())]
    if diffs:
        lines += ["", *yaml_patch(diffs)]
    return lines, len(diffs)

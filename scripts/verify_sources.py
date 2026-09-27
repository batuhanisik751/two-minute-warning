"""Step A3: verify every nflverse loader, coverage year and column the spec relies on.

Probes, for each per-season dataset, the current season, the previous season, a mid-range
season (2015), the dataset's claimed first season and the year before it (to confirm where
coverage starts); one-file datasets are probed once. Records rows/cols/errors per probe, the
expected-column check and the column differences across eras, plus data checks (currently the
``spread_line`` sign) under a "checks" key. Writes reports/verify_sources.json. The
human-readable conclusions go into docs/assumptions.md.

Cache-only by default: seasons that are not in data/raw are skipped (no network) and a guard
makes any attempted download fail loudly. Pass --allow-download to probe missing seasons.

Run: uv run python scripts/verify_sources.py [--allow-download] [dataset ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import polars as pl

from twm.config import ROOT, settings
from twm.sources import nflverse as nv

CURRENT = settings().current_season
MID_SEASON = 2015  # mid-history probe: catches columns that exist only in middle years
SKIP_NOTE = "not cached; pass --allow-download to probe it"

# Set by main(). When False, nv._loader is replaced by a guard that refuses to download.
ALLOW_DOWNLOAD = False
_downloads = 0

# Columns the spec (or our planned features) rely on. Best-known names; the report says which
# actually exist. Missing ones must be renamed in the spec/assumptions, never invented.
EXPECTED: dict[str, list[str]] = {
    "pbp": [
        "game_id",
        "season",
        "week",
        "game_date",
        "posteam",
        "defteam",
        "home_team",
        "away_team",
        "play_type",
        "down",
        "ydstogo",
        "yardline_100",
        "game_seconds_remaining",
        "half_seconds_remaining",
        "game_half",
        "score_differential",
        "posteam_timeouts_remaining",
        "defteam_timeouts_remaining",
        "posteam_score",
        "defteam_score",
        "epa",
        "wp",
        "vegas_wp",
        "home_wp",
        "wpa",
        "xpass",
        "cpoe",
        "xyac_epa",
        "xyac_mean_yardage",
        "air_yards",
        "yards_after_catch",
        "passer_player_id",
        "receiver_player_id",
        "rusher_player_id",
        "yards_gained",
        "touchdown",
        "pass_touchdown",
        "rush_touchdown",
        "complete_pass",
        "incomplete_pass",
        "interception",
        "fumble_lost",
        "two_point_attempt",
        "two_point_conv_result",
        "extra_point_result",
        "extra_point_attempt",
        "fourth_down_converted",
        "fourth_down_failed",
        "field_goal_result",
        "field_goal_attempt",
        "kick_distance",
        "punt_attempt",
        "timeout",
        "timeout_team",
        "penalty",
        "qb_kneel",
        "qb_spike",
        "spread_line",
        "total_line",
        "roof",
        "surface",
        "temp",
        "wind",
        "receive_2h_ko",
        "goal_to_go",
        "pass_location",
        "run_location",
        "shotgun",
        "no_huddle",
        "series_success",
        "drive",
        "qtr",
        "quarter_seconds_remaining",
        "sp",
        "fixed_drive_result",
        "aborted_play",
        "play_deleted",
        "success",
        "pass",
        "rush",
        "special",
        "desc",
        "home_coach",
        "away_coach",
        "div_game",
    ],
    "player_stats": [
        "player_id",
        "player_name",
        "player_display_name",
        "position",
        "position_group",
        "team",
        "recent_team",
        "opponent_team",
        "season",
        "week",
        "season_type",
        "completions",
        "attempts",
        "passing_yards",
        "passing_tds",
        "interceptions",
        "passing_interceptions",
        "sack_fumbles_lost",
        "passing_2pt_conversions",
        "carries",
        "rushing_yards",
        "rushing_tds",
        "rushing_fumbles_lost",
        "rushing_2pt_conversions",
        "receptions",
        "targets",
        "receiving_yards",
        "receiving_tds",
        "receiving_fumbles_lost",
        "receiving_air_yards",
        "receiving_yards_after_catch",
        "receiving_2pt_conversions",
        "target_share",
        "air_yards_share",
        "wopr",
        "racr",
        "fantasy_points",
        "fantasy_points_ppr",
    ],
    "schedules": [
        "game_id",
        "season",
        "game_type",
        "week",
        "gameday",
        "weekday",
        "gametime",
        "away_team",
        "home_team",
        "away_score",
        "home_score",
        "result",
        "total",
        "overtime",
        "location",
        "away_rest",
        "home_rest",
        "away_moneyline",
        "home_moneyline",
        "spread_line",
        "away_spread_odds",
        "home_spread_odds",
        "total_line",
        "under_odds",
        "over_odds",
        "div_game",
        "roof",
        "surface",
        "temp",
        "wind",
        "away_coach",
        "home_coach",
        "referee",
        "stadium_id",
        "stadium",
        "old_game_id",
        "espn",
        "pfr",
    ],
    "snap_counts": [
        "game_id",
        "pfr_game_id",
        "season",
        "game_type",
        "week",
        "player",
        "pfr_player_id",
        "position",
        "team",
        "opponent",
        "offense_snaps",
        "offense_pct",
        "defense_snaps",
        "defense_pct",
        "st_snaps",
        "st_pct",
    ],
    "injuries": [
        "season",
        "game_type",
        "team",
        "week",
        "gsis_id",
        "position",
        "full_name",
        "first_name",
        "last_name",
        "report_primary_injury",
        "report_secondary_injury",
        "report_status",
        "practice_primary_injury",
        "practice_status",
        "date_modified",
    ],
    "depth_charts": [
        "season",
        "week",
        "club_code",
        "team",
        "game_type",
        "depth_team",
        "last_name",
        "first_name",
        "football_name",
        "formation",
        "gsis_id",
        "jersey_number",
        "position",
        "elias_id",
        "depth_position",
        "full_name",
        "dt",
        "pos_grp",
        "pos_name",
        "pos_abb",
        "pos_slot",
        "pos_rank",
    ],
    "rosters": [
        "season",
        "team",
        "position",
        "depth_chart_position",
        "jersey_number",
        "status",
        "full_name",
        "first_name",
        "last_name",
        "birth_date",
        "height",
        "weight",
        "college",
        "gsis_id",
        "espn_id",
        "sportradar_id",
        "yahoo_id",
        "rotowire_id",
        "pff_id",
        "pfr_id",
        "fantasy_data_id",
        "sleeper_id",
        "years_exp",
        "headshot_url",
        "week",
        "game_type",
        "entry_year",
        "rookie_year",
        "draft_club",
        "draft_number",
    ],
    "rosters_weekly": [
        "season",
        "team",
        "position",
        "depth_chart_position",
        "status",
        "full_name",
        "birth_date",
        "gsis_id",
        "espn_id",
        "pfr_id",
        "sleeper_id",
        "years_exp",
        "week",
        "game_type",
        "entry_year",
        "rookie_year",
        "draft_club",
        "draft_number",
    ],
    "ngs_passing": [
        "season",
        "season_type",
        "week",
        "player_display_name",
        "player_position",
        "team_abbr",
        "player_gsis_id",
        "avg_time_to_throw",
        "avg_completed_air_yards",
        "avg_intended_air_yards",
        "aggressiveness",
        "completion_percentage_above_expectation",
        "attempts",
    ],
    "ngs_receiving": [
        "season",
        "season_type",
        "week",
        "player_gsis_id",
        "avg_cushion",
        "avg_separation",
        "avg_intended_air_yards",
        "percent_share_of_intended_air_yards",
        "receptions",
        "targets",
        "catch_percentage",
        "avg_yac",
        "avg_expected_yac",
        "avg_yac_above_expectation",
    ],
    "ngs_rushing": [
        "season",
        "season_type",
        "week",
        "player_gsis_id",
        "efficiency",
        "percent_attempts_gte_eight_defenders",
        "avg_time_to_los",
        "rush_attempts",
        "expected_rush_yards",
        "rush_yards_over_expected",
        "rush_yards_over_expected_per_att",
        "rush_pct_over_expected",
    ],
    "pfr_pass": [
        "season",
        "week",
        "game_id",
        "pfr_player_id",
        "team",
        "passing_drops",
        "times_pressured",
        "times_blitzed",
        "times_hurried",
    ],
    "pfr_rush": [
        "season",
        "week",
        "game_id",
        "pfr_player_id",
        "team",
        "carries",
        "rushing_broken_tackles",
        "rushing_yards_before_contact",
        "rushing_yards_after_contact",
    ],
    "pfr_rec": [
        "season",
        "week",
        "game_id",
        "pfr_player_id",
        "team",
        "receiving_drop",
        "receiving_broken_tackles",
        "receiving_int",
    ],
    "ftn_charting": [
        "nflverse_game_id",
        "nflverse_play_id",
        "season",
        "week",
        "is_play_action",
        "is_motion",
        "is_rpo",
        "n_blitzers",
        "n_pass_rushers",
        "is_screen_pass",
    ],
    "participation": [
        "nflverse_game_id",
        "play_id",
        "possession_team",
        "offense_formation",
        "offense_personnel",
        "defenders_in_box",
        "defense_personnel",
        "number_of_pass_rushers",
        "players_on_play",
        "offense_players",
        "defense_players",
        "n_offense",
        "n_defense",
        "ngs_air_yards",
        "time_to_throw",
        "was_pressure",
        "route",
        "defense_man_zone_type",
        "defense_coverage_type",
    ],
    "ff_opportunity": [
        "season",
        "week",
        "player_id",
        "full_name",
        "position",
        "team",
        "pass_attempt",
        "pass_completions_exp",
        "pass_yards_gained_exp",
        "pass_touchdown_exp",
        "receptions_exp",
        "rec_yards_gained_exp",
        "rec_touchdown_exp",
        "rush_yards_gained_exp",
        "rush_touchdown_exp",
        "pass_fantasy_points_exp",
        "rec_fantasy_points_exp",
        "rush_fantasy_points_exp",
        "total_fantasy_points_exp",
        "total_fantasy_points",
        "total_fantasy_points_diff",
        "rec_attempt",
        "rush_attempt",
        "receptions",
        "rec_yards_gained",
        "rec_touchdown",
        "rush_yards_gained",
        "rush_touchdown",
        "pass_completions",
        "pass_yards_gained",
        "pass_touchdown",
        "pass_interception",
        "pass_interception_exp",
        "pass_two_point_conv",
        "rec_two_point_conv",
        "rush_two_point_conv",
        "rec_fumble_lost",
        "rush_fumble_lost",
        "pass_first_down_exp",
        "game_id",
    ],
    "ff_opportunity_pass": [
        "season",
        "week",
        "game_id",
        "play_id",
        "receiver_player_id",
        "passer_player_id",
        "air_yards",
        "yardline_100",
        "down",
        "ydstogo",
        "pass_completion_exp",
        "yards_gained_exp",
        "pass_touchdown_exp",
        "rec_fantasy_points_exp",
        "pass_fantasy_points_exp",
    ],
    "ff_opportunity_rush": [
        "season",
        "week",
        "game_id",
        "play_id",
        "rusher_player_id",
        "yardline_100",
        "down",
        "ydstogo",
        "rush_yards_gained_exp",
        "rush_touchdown_exp",
        "rush_fantasy_points_exp",
    ],
    "draft_picks": [
        "season",
        "round",
        "pick",
        "team",
        "gsis_id",
        "pfr_player_id",
        "cfb_player_id",
        "pfr_player_name",
        "hof",
        "position",
        "category",
        "side",
        "college",
        "age",
        "to",
        "allpro",
        "probowls",
        "seasons_started",
    ],
    "combine": [
        "season",
        "draft_year",
        "draft_team",
        "draft_round",
        "draft_ovr",
        "pfr_id",
        "cfb_id",
        "player_name",
        "pos",
        "school",
        "ht",
        "wt",
        "forty",
        "bench",
        "vertical",
        "broad_jump",
        "cone",
        "shuttle",
    ],
    "ff_playerids": [
        "mfl_id",
        "sportradar_id",
        "fantasypros_id",
        "gsis_id",
        "pff_id",
        "sleeper_id",
        "nfl_id",
        "espn_id",
        "yahoo_id",
        "fleaflicker_id",
        "cbs_id",
        "pfr_id",
        "cfbref_id",
        "rotowire_id",
        "rotoworld_id",
        "ktc_id",
        "stats_id",
        "stats_global_id",
        "fantasy_data_id",
        "swish_id",
        "name",
        "merge_name",
        "position",
        "team",
        "birthdate",
        "age",
        "draft_year",
        "draft_round",
        "draft_pick",
        "draft_ovr",
        "twitter_username",
        "height",
        "weight",
        "college",
        "db_season",
    ],
    "ff_rankings_draft": [
        "fantasypros_id",
        "player_name",
        "pos",
        "team",
        "rank",
        "ecr",
        "sd",
        "best",
        "worst",
        "sportsdata_id",
        "yahoo_id",
        "cbs_id",
        "page_type",
        "scrape_date",
        "id",
    ],
    "ff_rankings_week": [
        "fantasypros_id",
        "player_name",
        "pos",
        "team",
        "rank",
        "ecr",
        "sd",
        "best",
        "worst",
        "page_type",
        "scrape_date",
        "id",
    ],
    "ff_rankings_all": [
        "fantasypros_id",
        "player_name",
        "pos",
        "team",
        "rank",
        "ecr",
        "sd",
        "best",
        "worst",
        "page_type",
        "scrape_date",
        "id",
        "ecr_type",
    ],
    "contracts": [
        "player",
        "position",
        "team",
        "is_active",
        "year_signed",
        "years",
        "value",
        "apy",
        "guaranteed",
        "apy_cap_pct",
        "inflated_value",
        "inflated_apy",
        "inflated_guaranteed",
        "player_page",
        "otc_id",
        "gsis_id",
        "date_of_birth",
        "height",
        "weight",
        "college",
        "draft_year",
        "draft_round",
        "draft_overall",
        "draft_team",
        "cols",
    ],
    "players": [
        "gsis_id",
        "display_name",
        "common_first_name",
        "first_name",
        "last_name",
        "short_name",
        "football_name",
        "esb_id",
        "nfl_id",
        "pfr_id",
        "pff_id",
        "otc_id",
        "espn_id",
        "smart_id",
        "birth_date",
        "position_group",
        "position",
        "height",
        "weight",
        "headshot",
        "college_name",
        "rookie_season",
        "last_season",
        "latest_team",
        "status",
        "draft_year",
        "draft_round",
        "draft_pick",
        "draft_team",
        "years_of_experience",
        "jersey_number",
    ],
    "teams": [
        "team_abbr",
        "team_name",
        "team_id",
        "team_nick",
        "team_conf",
        "team_division",
        "team_color",
        "team_color2",
        "team_color3",
        "team_color4",
        "team_logo_wikipedia",
        "team_logo_espn",
        "team_wordmark",
        "team_conference_logo",
        "team_league_logo",
        "team_logo_squared",
        "season",
    ],
    "team_stats": ["season", "week", "team", "opponent_team", "passing_epa", "rushing_epa"],
}

# Coverage years claimed in the spec Section 4.1 (verify: first year loads, year before fails).
CLAIMED_FIRST: dict[str, int] = {
    "pbp": 1999,
    "player_stats": 1999,
    "schedules": 1999,
    "snap_counts": 2012,
    "injuries": 2009,
    "depth_charts": 2001,
    "rosters": 1999,
    "rosters_weekly": 2002,
    "ngs_passing": 2016,
    "ngs_receiving": 2016,
    "ngs_rushing": 2016,
    "pfr_pass": 2018,
    "pfr_rush": 2018,
    "pfr_rec": 2018,
    "ftn_charting": 2022,
    "participation": 2016,
    "ff_opportunity": 2006,
    "ff_opportunity_pass": 2006,
    "ff_opportunity_rush": 2006,
    "draft_picks": 1980,
    "combine": 2000,
    "team_stats": 1999,
}


def _install_download_guard() -> bool:
    """Route every loader lookup through a counter; refuse downloads in cache-only mode."""
    real = getattr(nv, "_loader", None)
    if real is None:
        return False

    def guarded(ds: nv.Dataset) -> Callable[..., pl.DataFrame]:
        global _downloads
        if not ALLOW_DOWNLOAD:
            raise RuntimeError(f"{ds.name}: download blocked (cache-only mode; {SKIP_NOTE})")
        _downloads += 1
        return real(ds)

    nv._loader = guarded
    return True


def _cached(name: str, season: int | None) -> bool:
    return nv.cache_path(name, season).exists()


def _fetch_kwargs() -> dict[str, Any]:
    # Cache-only mode: a cached file is never stale, so fetch() never reaches for the network.
    # With --allow-download, fetch()'s defaults apply (the current season and one-file
    # datasets are refreshed after max_age_hours).
    return {} if ALLOW_DOWNLOAD else {"max_age_hours": float("inf")}


def try_fetch(name: str, season: int | None, **kw: Any) -> tuple[pl.DataFrame | None, str | None]:
    t0 = time.time()
    try:
        df = nv.fetch(name, season, snapshot=False, **kw)
        return df, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        print(f"  {name} {season}: {time.time() - t0:.1f}s", file=sys.stderr)


def _describe(df: pl.DataFrame) -> dict[str, Any]:
    rec: dict[str, Any] = {"rows": df.height, "cols": df.width}
    if "week" in df.columns and df.height:
        rec["week_min"] = df["week"].min()
        rec["week_max"] = df["week"].max()
    if "season" in df.columns and df.height:
        rec["season_values"] = sorted(df["season"].unique().to_list())[:5]
    return rec


def probe(name: str) -> dict[str, Any]:
    ds = nv.DATASETS[name]
    out: dict[str, Any] = {
        "dataset": name,
        "loader": ds.loader,
        "kwargs": ds.kwargs,
        "per_season": ds.per_season,
        "probes": {},
        "errors": {},
        "skipped": [],
    }
    if ds.per_season:
        first = CLAIMED_FIRST.get(name, ds.first_season or 1999)
        probes: dict[str, int | None] = {
            "current": CURRENT,
            "prev": CURRENT - 1,
            "mid": MID_SEASON,
            "first": first,
            "before_first": first - 1,
        }
        if not first < MID_SEASON < CURRENT - 1:
            del probes["mid"]
    else:
        probes = {"all": None}

    frames: dict[str, pl.DataFrame] = {}
    for label, season in probes.items():
        rec: dict[str, Any] = {"season": season}
        if not (ALLOW_DOWNLOAD or _cached(name, season)):
            rec["skipped"] = SKIP_NOTE
            out["skipped"].append(label)
        else:
            df, err = try_fetch(name, season, **_fetch_kwargs())
            rec["error"] = err
            if err is not None:
                out["errors"][label] = err
            if df is not None:
                rec.update(_describe(df))
                frames[label] = df
        out["probes"][label] = rec

    # Column check against the freshest frame that loaded, and note differences across eras.
    order = ("current", "prev", "all", "mid", "first")
    ref = next((frames[k] for k in order if k in frames), None)
    if ref is not None:
        cols = set(ref.columns)
        exp = EXPECTED.get(name, [])
        out["expected_present"] = [c for c in exp if c in cols]
        out["expected_missing"] = [c for c in exp if c not in cols]
        out["actual_columns"] = {c: str(t) for c, t in ref.schema.items()}
        for label in ("prev", "mid", "first"):
            if label in frames and frames[label] is not ref:
                other = set(frames[label].columns)
                out[f"cols_only_in_{label}"] = sorted(other - cols)
                out[f"cols_absent_in_{label}"] = sorted(cols - other)
    return out


def spread_sign_check() -> dict[str, Any]:
    """Is ``spread_line`` the home team's expected margin? (docs/assumptions.md section 7)

    For the two cached seasons before the current one, over played games: the correlation of
    ``spread_line`` with ``result`` (home minus away score), the mean ``result`` when
    ``spread_line`` > 0, and how often the moneyline agrees (home is the moneyline favourite
    when ``spread_line`` > 0). All three should point the same way.
    """
    out: dict[str, Any] = {}
    for season in (CURRENT - 2, CURRENT - 1):
        key = str(season)
        if not (ALLOW_DOWNLOAD or _cached("schedules", season)):
            out[key] = {"skipped": SKIP_NOTE}
            continue
        df, err = try_fetch("schedules", season, **_fetch_kwargs())
        if df is None:
            out[key] = {"error": err}
            continue
        played = df.filter(pl.col("result").is_not_null() & pl.col("spread_line").is_not_null())
        fav = played.filter(pl.col("spread_line") > 0)
        ml = fav.filter(
            pl.col("home_moneyline").is_not_null() & pl.col("away_moneyline").is_not_null()
        )
        agree = ml.filter(pl.col("home_moneyline") < pl.col("away_moneyline")).height
        out[key] = {
            "games": played.height,
            "corr_spread_result": round(played.select(pl.corr("spread_line", "result")).item(), 3),
            "n_spread_positive": fav.height,
            "mean_result_when_spread_positive": round(fav["result"].mean(), 2),
            "moneyline_agreement_share": round(agree / ml.height, 3) if ml.height else None,
        }
    out["interpretation"] = "spread_line > 0 means the home team is favoured by that margin"
    return out


def main(argv: list[str]) -> None:
    global ALLOW_DOWNLOAD
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("datasets", nargs="*", help="dataset names to probe (default: all)")
    ap.add_argument(
        "--allow-download",
        action="store_true",
        help="probe seasons that are not in data/raw (uses the network; default: skip them)",
    )
    args = ap.parse_args(argv)
    ALLOW_DOWNLOAD = args.allow_download
    unknown = [n for n in args.datasets if n not in nv.DATASETS]
    if unknown:
        ap.error(f"unknown dataset(s) {unknown}; known: {sorted(nv.DATASETS)}")
    guard = _install_download_guard()
    names = args.datasets or list(nv.DATASETS)

    results = []
    for name in names:
        print(f"== {name}", file=sys.stderr)
        results.append(probe(name))
    print("== checks", file=sys.stderr)
    checks = {"spread_sign": spread_sign_check()}

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "current_season": CURRENT,
        "allow_download": ALLOW_DOWNLOAD,
        "downloads": _downloads,
        "datasets": results,
        "checks": checks,
    }
    out = ROOT / "reports" / "verify_sources.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"wrote {out}")
    for r in results:
        miss = r.get("expected_missing", "?")
        failed = sorted(r["errors"])
        print(f"{r['dataset']:22s} missing={miss} failed={failed} skipped={r['skipped']}")
    mode = "downloads allowed" if ALLOW_DOWNLOAD else "cache-only mode"
    guard_note = "" if guard else " (download guard unavailable: nv._loader not found)"
    print(f"downloads: {_downloads} ({mode}){guard_note}")


if __name__ == "__main__":
    main(sys.argv[1:])

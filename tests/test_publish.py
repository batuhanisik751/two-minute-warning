"""`twm publish` without a database (step E2): target safety, the collected rows, the checks
before anything is written, and the rule that the publisher never reads league data."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from tests import publish_synthetic as ps
from twm.cli import app
from twm.config import ROOT
from twm.modules.waiver_radar import confidence as cf
from twm.publish import collect as col
from twm.publish import target as tg
from twm.publish import write as wr
from twm.publish.tables import TABLES

SECRET = "Sup3r-Secret_pw-7f3a9c"  # never allowed to appear in any output
NO_ENV_FILE = Path("/nonexistent/.env")


# --------------------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host, local",
    [("localhost", True), ("127.0.0.1", True), ("127.3.2.1", True), ("::1", True),
     ("[::1]", True), ("/tmp", True), ("app.localhost", True), ("twm-postgres", True),
     ("0.0.0.0", True), ("ep-cool-bird-123.us-east-1.aws.neon.tech", False),
     ("10.0.0.5", False), ("db.example.com", False)],
)  # fmt: skip
def test_is_local_host(host: str, local: bool) -> None:
    assert tg.is_local_host(host) is local


def test_local_default_is_the_compose_container() -> None:
    t = tg.resolve("local", env={}, env_file=NO_ENV_FILE)
    assert t.url == tg.DEFAULT_LOCAL_URL and t.source == "default"
    assert t.hosts == ("127.0.0.1",) and t.port == "5434" and t.dbname == "twm"


def test_remote_refuses_localhost_and_never_shows_the_url() -> None:
    for host in ("localhost", "127.0.0.1", "[::1]"):
        url = f"postgresql://twm_job:{SECRET}@{host}:5434/neondb?sslmode=verify-full"
        with pytest.raises(tg.TargetError, match="refusing --target remote") as e:
            tg.resolve("remote", env={"DATABASE_URL": url}, env_file=NO_ENV_FILE)
        assert SECRET not in str(e.value) and url not in str(e.value)


def test_remote_refuses_a_missing_host_and_an_unset_url() -> None:
    with pytest.raises(tg.TargetError, match="names no host"):
        tg.resolve("remote", env={"DATABASE_URL": f"postgresql://u:{SECRET}@/db"},
                   env_file=NO_ENV_FILE)  # fmt: skip
    with pytest.raises(tg.TargetError, match="not set"):
        tg.resolve("remote", env={}, env_file=NO_ENV_FILE)


def test_local_refuses_a_remote_host() -> None:
    url = f"postgresql://twm:{SECRET}@ep-x.us-east-1.aws.neon.tech/neondb?sslmode=verify-full"
    with pytest.raises(tg.TargetError, match="refusing --target local") as e:
        tg.resolve("local", env={"TWM_LOCAL_DATABASE_URL": url}, env_file=NO_ENV_FILE)
    assert SECRET not in str(e.value)
    # a local URL listing a remote host too is refused as well
    both = f"host=127.0.0.1,db.example.com dbname=x password={SECRET}"
    with pytest.raises(tg.TargetError, match="db.example.com"):
        tg.resolve("local", env={"TWM_LOCAL_DATABASE_URL": both}, env_file=NO_ENV_FILE)


def test_remote_needs_verify_full_and_gets_a_ca_bundle() -> None:
    base = f"postgresql://twm_job:{SECRET}@ep-x.us-east-1.aws.neon.tech/neondb"
    for mode in ("", "?sslmode=require", "?sslmode=prefer&channel_binding=require"):
        with pytest.raises(tg.TargetError, match="verify-full"):
            tg.resolve("remote", env={"DATABASE_URL": base + mode}, env_file=NO_ENV_FILE)
    t = tg.resolve("remote", env={"DATABASE_URL": base + "?sslmode=verify-full"},
                   env_file=NO_ENV_FILE)  # fmt: skip
    assert t.hosts == ("ep-x.us-east-1.aws.neon.tech",) and t.dbname == "neondb"
    if any(Path(b).exists() for b in tg.CA_BUNDLES):
        assert dict(t.connect_args)["sslrootcert"] in tg.CA_BUNDLES
    own = tg.resolve("remote", env={"DATABASE_URL": base + "?sslmode=verify-full&sslrootcert="
                                    "/some/ca.pem"}, env_file=NO_ENV_FILE)  # fmt: skip
    assert own.connect_args == ()
    assert SECRET not in t.describe() and SECRET not in repr(t)


def test_env_file_is_read_after_the_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    url = f"postgresql://twm_job:{SECRET}@ep-x.aws.neon.tech/neondb?sslmode=verify-full&a=1"
    env_file.write_text(f"DATABASE_URL={url}\n")
    with pytest.raises(tg.TargetError):  # 'a' is not a libpq parameter: refused, not shown
        tg.resolve("remote", env={}, env_file=env_file)
    env_file.write_text(f"DATABASE_URL='{url.replace('&a=1', '')}'\n")
    t = tg.resolve("remote", env={}, env_file=env_file)
    assert t.source == ".env"
    other = "postgresql://u:pw123456@ep-y.aws.neon.tech/other?sslmode=verify-full"
    assert tg.resolve("remote", env={"DATABASE_URL": other}, env_file=env_file).dbname == "other"


def test_invalid_url_is_not_repeated() -> None:
    bad = f"postgresql://u:{SECRET}@host/db?nonsense=1"
    with pytest.raises(tg.TargetError) as e:
        tg.resolve("local", env={"TWM_LOCAL_DATABASE_URL": bad}, env_file=NO_ENV_FILE)
    assert SECRET not in str(e.value) and "nonsense" not in str(e.value)


def test_redact() -> None:
    url = f"postgresql://u:{SECRET}@h/db"
    text = f"failed for {url} with password {SECRET}"
    assert SECRET not in tg.redact(text, url)
    assert tg.redact("twm publish failed", "postgresql://twm:twm@127.0.0.1/twm") == (
        "twm publish failed"
    )  # a 3-character password is not scrubbed out of ordinary words


def test_parse_week() -> None:
    assert wr.parse_week("2026-W04") == (2026, 4)
    assert wr.parse_week("2026-w4") == (2026, 4)
    with pytest.raises(ValueError):
        wr.parse_week("2026-4")


# --------------------------------------------------------------------------------------
# The CLI never prints a connection string
# --------------------------------------------------------------------------------------


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tg, "default_env_file", lambda: NO_ENV_FILE)
    for key in ("DATABASE_URL", "TWM_LOCAL_DATABASE_URL", "PGHOST"):
        monkeypatch.delenv(key, raising=False)


def _cli_args(syn: ps.Synthetic, target: str) -> list[str]:
    i = syn.inputs
    return ["publish", "--target", target, "--db", str(i.warehouse), "--store", str(i.store),
            "--dataset", str(i.dataset), "--evaluation", str(i.evaluation_csv),
            "--module", "waiver_radar"]  # fmt: skip


def test_cli_refusals_never_print_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_env_file: None
) -> None:
    syn = ps.build(tmp_path)
    runner = CliRunner()
    cases = [
        ("remote", "DATABASE_URL", f"postgresql://twm_job:{SECRET}@127.0.0.1:5434/x"),
        ("local", "TWM_LOCAL_DATABASE_URL", f"postgresql://twm:{SECRET}@db.example.com/x"),
        # accepted, but nothing listens on port 9: the connection error is shown, the URL not
        ("local", "TWM_LOCAL_DATABASE_URL", f"postgresql://twm:{SECRET}@127.0.0.1:9/x"),
    ]
    for target, key, url in cases:
        monkeypatch.setenv(key, url)
        res = runner.invoke(app, _cli_args(syn, target))
        out = res.output
        assert res.exit_code == 1, out
        assert SECRET not in out and url not in out
        assert "not published" in out
        monkeypatch.delenv(key)


def test_cli_remote_without_database_url(tmp_path: Path, no_env_file: None) -> None:
    res = CliRunner().invoke(app, ["publish", "--target", "remote"])
    assert res.exit_code == 1 and "DATABASE_URL is not set" in res.output


# --------------------------------------------------------------------------------------
# What is collected
# --------------------------------------------------------------------------------------


@pytest.fixture
def synthetic(tmp_path: Path) -> ps.Synthetic:
    return ps.build(tmp_path, live_weeks=(1,))


def test_collect_lists_and_picks(synthetic: ps.Synthetic) -> None:
    data = col.collect(synthetic.inputs)
    assert col.validate(data) == []
    lists = data.lists
    # 2 backtest seasons x 3 weeks x 4 positions, plus the live week 1
    assert lists.filter(pl.col("kind") == "backtest").height == 24
    assert lists.filter(pl.col("kind") == "live").height == 4
    assert set(lists.get_column("n_pool").to_list()) == {ps.POOL}
    assert data.picks.height == 28 * col.TOP_N
    live = data.picks.filter((pl.col("kind") == "live") & (pl.col("position") == "QB"))
    first = live.row(0, named=True)
    assert first["rank"] == 1 and first["gsis_id"] == ps.gid(0, 0) and first["team"] == "BUF"
    assert first["chance"] == pytest.approx(0.875) and first["chance_low"] < first["chance"]
    assert first["tier"] == "must-add" and first["reasons"][0].startswith("Reason 1")
    back = data.picks.filter(pl.col("kind") == "backtest")
    # walk-forward chances (E2 review): 2025's lists from the 2024 backtest; 2024 is the first
    # backtest season of this store, so its lists have no earlier season and stay NULL
    b24, b25 = (back.filter(pl.col("season") == s) for s in (2024, 2025))
    assert b24.get_column("chance").null_count() == b24.height == 300
    assert b24.get_column("tier").null_count() == 300
    assert b25.get_column("chance").null_count() == 0
    assert set(b25.get_column("tier").to_list()) <= {"must-add", "speculative", "watch"}
    assert set(back.get_column("reasons").list.len().to_list()) == {0}
    # the QB note of the weekly report, only on the current season's lists
    notes = lists.filter(pl.col("note").is_not_null())
    assert notes.select("season", "position").unique().rows() == [(2026, "QB")]
    assert notes.get_column("note")[0].startswith("Note for QB")
    # every column the tables need, and nothing else
    assert list(data.lists.columns) == list(TABLES["radar_list"].names)
    assert list(data.picks.columns) == list(TABLES["radar_pick"].names)
    for name, df in data.tables.items():
        assert list(df.columns) == list(TABLES[name].names), name


def test_collect_other_tables(synthetic: ps.Synthetic) -> None:
    data = col.collect(synthetic.inputs)
    t = data.tables
    assert t["dim_team"].get_column("team_abbr").to_list() == sorted(ps.TEAMS)  # current only
    players = t["dim_player"]
    missing = players.filter(pl.col("gsis_id") == ps.gid(0, ps.POOL - 1)).row(0, named=True)
    assert missing["display_name"] == f"Player 0-{ps.POOL - 1}"  # from the dataset
    assert players.get_column("display_name").null_count() == 0
    # outcomes needed: every pick + every current-season dataset row (pool and beyond)
    keys = data.outcome_keys
    assert keys.filter(pl.col("season") == ps.SEASON).height == 3 * 4 * (ps.POOL + 1)
    pws = t["player_week_summary"]
    assert pws.height == 2 * 2 * 4 * 4 and "00-0099999" not in pws.get_column("gsis_id")
    row = pws.filter((pl.col("gsis_id") == ps.gid(2, 1)) & (pl.col("season") == 2026)
                     & (pl.col("week") == 1)).row(0, named=True)  # fmt: skip
    # 3 catches, 41 yards, 10 rushing yards, full PPR: 3 + 4.1 + 1.0
    assert row["fantasy_points"] == pytest.approx(8.1)
    # step PXFP: xFP/FPOE are Regression Watch's own walk-forward xFP
    # (tests/test_player_pages_xfp.py); a publish without that module carries none
    assert all(row[c] is None for c in col.XFP_COLUMNS)
    assert row["carry_share"] == pytest.approx(6 / 25) and row["snap_share"] == 0.6
    snaps_only = pws.filter(pl.col("gsis_id") == ps.gid(1, 3)).row(0, named=True)
    assert snaps_only["fantasy_points"] == 0 and snaps_only["target_share"] == 0
    assert t["glossary"].height > 50
    assert data.meta["current_week"] == "2" and data.meta["data_through_week"] == "2"
    assert data.data_as_of is not None and data.data_as_of.isoformat().startswith("2026-09-22")


def test_current_as_of_is_the_official_as_of_of_the_current_week(synthetic: ps.Synthetic) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from twm.asof import weekly_as_of

    meta = col.collect(synthetic.inputs).meta
    assert meta["current_as_of"] == "2026-09-22T14:00:00+00:00"
    assert meta["current_as_of"] == weekly_as_of(synthetic.inputs.warehouse, 2026, 2).isoformat()
    # at the as-of itself the week is current; before the season's first as-of, empty
    at = col.collect(replace(synthetic.inputs, now=datetime(2026, 9, 29, 14, 0, tzinfo=UTC))).meta
    assert (at["current_week"], at["current_as_of"]) == ("3", "2026-09-29T14:00:00+00:00")
    early = col.collect(replace(synthetic.inputs, now=datetime(2026, 9, 10, tzinfo=UTC))).meta
    assert (early["current_week"], early["current_as_of"]) == ("", "")


def test_track_record_equals_the_csv(synthetic: ps.Synthetic) -> None:
    tr = col.collect(synthetic.inputs).tables["track_record"]
    assert tr.height == len(synthetic.csv_rows)
    for r, row in zip(synthetic.csv_rows, tr.iter_rows(named=True), strict=True):
        assert row["model"] == r["model"] and row["metric"] == r["metric"]
        assert row["scope_value"] == r["key"]
        assert row["excl_rostered"] is (r["subset"] == "without_rostered")
        parts = r["seasons"].split("-")
        assert (row["season_from"], row["season_to"]) == (int(parts[0]), int(parts[-1]))
        for col_, csv_col in (("value", "value"), ("low", "lo"), ("high", "hi")):
            assert row[col_] == (float(r[csv_col]) if r[csv_col] else None)
        for col_, csv_col in (("n_lists", "n_groups"), ("n_positives", "n_pos"),
                              ("n_rows", "n_rows"), ("n_top_hits", "n_top_hits"),
                              ("n_top", "n_top")):  # fmt: skip
            assert row[col_] == (int(r[csv_col]) if r[csv_col] else None)


def test_tier_stats_equal_the_weekly_report_table(synthetic: ps.Synthetic) -> None:
    tiers = col.collect(synthetic.inputs).tables["tier_stats"]
    conf = cf.from_store(synthetic.inputs.store, model="logit", label="y_hit",
                         seasons=cf.seasons_before(ps.SEASON))  # fmt: skip
    every = tiers.filter(pl.col("week") == 0).sort("tier")
    ref = conf.tiers.sort("tier")
    assert every.get_column("players").to_list() == ref.get_column("rows").to_list()
    assert every.get_column("hits").to_list() == ref.get_column("hits").to_list()
    assert every.get_column("hit_rate").to_list() == ref.get_column("rate").to_list()
    week2 = tiers.filter(pl.col("week") == 2).sort("tier")
    assert week2.get_column("players").to_list() == conf.week_tiers(2).sort("tier").get_column(
        "rows").to_list()  # fmt: skip
    assert set(tiers.get_column("week").to_list()) == {0, 1, 2, 3}


# --------------------------------------------------------------------------------------
# Checks before writing
# --------------------------------------------------------------------------------------


def test_validation_catches_broken_rows(synthetic: ps.Synthetic) -> None:
    data = col.collect(synthetic.inputs)
    good = data.picks

    def problems(picks: pl.DataFrame) -> list[str]:
        data.picks = picks
        return col.validate(data)

    assert problems(good) == []
    gap = good.with_columns(
        pl.when(pl.col("rank") == 5).then(99).otherwise(pl.col("rank")).alias("rank")
    )
    assert any("1..n" in p for p in problems(gap))
    assert any("outside [0, 1]" in p for p in problems(good.with_columns(
        pl.lit(1.5).alias("model_prob"))))  # fmt: skip
    no_team = good.with_columns(
        pl.when(pl.col("rank") == 1).then(None).otherwise(pl.col("team")).alias("team")
    )
    assert any("no team" in p for p in problems(no_team))
    stranger = good.with_columns(
        pl.when(pl.col("rank") == 2).then(pl.lit("00-0999998")).otherwise(pl.col("gsis_id"))
        .alias("gsis_id")
    )  # fmt: skip
    assert any("missing from dim_player" in p for p in problems(stranger))
    old_team = good.with_columns(pl.lit("OAK").alias("team"))
    assert any("not a current franchise" in p for p in problems(old_team))
    data.picks = good
    data.tables["dim_player"] = data.tables["dim_player"].with_columns(pl.lit("x").alias("espn_id"))
    assert any("espn_id" in p for p in col.validate(data))


def test_dataset_without_the_pick_is_refused(tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1, 2))
    ps.write_dataset(syn.inputs.dataset, weeks_2026=(1,))  # week 2 is missing
    data = col.collect(syn.inputs)
    assert any("no team in the dataset" in p for p in col.validate(data))


def test_stale_store_outcomes_are_refused(tmp_path: Path) -> None:
    syn = ps.build(tmp_path)
    ds = pl.read_parquet(syn.inputs.dataset)
    ds.with_columns(
        pl.when(pl.col("season") == 2024).then(~pl.col("y_hit")).otherwise(pl.col("y_hit"))
        .alias("y_hit")
    ).write_parquet(syn.inputs.dataset)  # fmt: skip
    with pytest.raises(col.PublishInputError, match="stale"):
        col.collect(syn.inputs)


def test_missing_inputs_are_explained(tmp_path: Path) -> None:
    syn = ps.build(tmp_path)
    from dataclasses import replace

    with pytest.raises(col.PublishInputError, match="twm radar dataset"):
        col.collect(replace(syn.inputs, dataset=tmp_path / "nope.parquet"))
    with pytest.raises(col.PublishInputError, match="predictions store not found"):
        col.collect(replace(syn.inputs, store=tmp_path / "nope.duckdb"))


def test_plan_live_without_a_target(synthetic: ps.Synthetic) -> None:
    data = col.collect(synthetic.inputs)
    decisions, keys = wr.plan_live(data, {}, allow_incomplete=False, replace_live=[])
    assert [d.action for d in decisions] == ["insert"] * 4 and keys.height == 4
    with pytest.raises(wr.PublishError, match="no live list of 2026 week 2"):
        wr.plan_live(data, {}, allow_incomplete=False, replace_live=[(2026, 2)])


# --------------------------------------------------------------------------------------
# No league data, ever (spec rule 9)
# --------------------------------------------------------------------------------------


def test_publish_package_never_imports_the_league_module() -> None:
    for path in sorted((ROOT / "src" / "twm" / "publish").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(n == "twm.league" or n.startswith("twm.league.") for n in names), path
    code = (
        "import sys; import twm.publish.collect, twm.publish.write, twm.publish.target, "
        "twm.publish.migrate, twm.publish.tables; import twm.cli; "
        "bad = [m for m in sys.modules if m == 'twm.league' or m.startswith('twm.league.')]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 0, res.stdout + res.stderr


def test_every_table_is_written_in_order() -> None:
    from twm.publish.tables import WRITE_ORDER

    assert WRITE_ORDER.index("dim_player") < WRITE_ORDER.index("radar_pick")
    assert WRITE_ORDER.index("radar_list") < WRITE_ORDER.index("radar_pick")
    assert WRITE_ORDER.index("model_versions") < WRITE_ORDER.index("radar_list")


# --------------------------------------------------------------------------------------
# Walk-forward chances of the backtest lists (reviewer's rule after E2)
# --------------------------------------------------------------------------------------


def _flip_outcomes(syn: ps.Synthetic, season: int) -> None:
    """Invert every final y_hit of ``season`` in the store and the dataset (they must agree)."""
    import duckdb

    con = duckdb.connect(str(syn.inputs.store))
    try:
        con.execute("UPDATE outcomes SET y_hit = NOT y_hit WHERE season = ?", [season])
    finally:
        con.close()
    ds = pl.read_parquet(syn.inputs.dataset)
    ds.with_columns(
        pl.when(pl.col("season") == season).then(~pl.col("y_hit")).otherwise(pl.col("y_hit"))
        .alias("y_hit")
    ).write_parquet(syn.inputs.dataset)  # fmt: skip


def _chances(syn: ps.Synthetic, season: int) -> list:
    picks = col.collect(syn.inputs).picks
    return (
        picks.filter((pl.col("kind") == "backtest") & (pl.col("season") == season))
        .sort("week", "position", "rank")
        .select("chance", "chance_low", "chance_high", "tier")
        .rows()
    )


def test_backtest_chances_never_use_their_own_or_later_seasons(tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=())
    before = _chances(syn, 2025)
    assert before and all(c is not None for c, *_ in before)
    # the same number the weekly list's code gives for a 2025 list: bins of 2014-2024 only
    from twm.modules.waiver_radar import confidence as cf

    conf = cf.from_store(syn.inputs.store, model="logit", label="y_hit",
                         seasons=cf.seasons_before(2025))  # fmt: skip
    assert conf.seasons == (2024, 2024)
    picks = col.collect(syn.inputs).picks.filter(
        (pl.col("kind") == "backtest") & (pl.col("season") == 2025)
    )
    band = conf.band(picks.get_column("model_prob").to_numpy())
    assert picks.get_column("chance").to_list() == [
        round(x, 4) for x in band.get_column("chance").to_list()
    ]
    # 1. season 2025's own outcomes change: its chances do not
    _flip_outcomes(syn, 2025)
    assert _chances(syn, 2025) == before
    # 2. an earlier season's outcomes change: they do (the property is not vacuous)
    _flip_outcomes(syn, 2024)
    assert _chances(syn, 2025) != before
    # 2024 (the first backtest season here) never gets a chance
    assert {r[0] for r in _chances(syn, 2024)} == {None}

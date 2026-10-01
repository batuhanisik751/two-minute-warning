"""Feature/metric registry: entries are valid and point at real columns, the feature guard
refuses identifiers/labels/planned/unknown names, templates render, and docs/glossary.md is in
sync with the code."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from twm import registry as rg
from twm.cli import app
from twm.situations import SituationRules
from twm.warehouse import schema as sc

ROOT = Path(__file__).resolve().parents[1]


def warehouse_columns() -> dict[str, set[str]]:
    out = {name: set(t.column_names) for name, t in sc.tables().items()}
    for name in out:  # every event table gets available_at at build time (B2)
        out[name].add("available_at")
    return out


def test_registry_has_the_core_entries():
    names = set(rg.REGISTRY)
    assert {"fantasy_points", "offense_snap_share", "target_share", "wopr", "epa", "wp",
            "is_garbage_time", "is_neutral", "gsis_id", "y_hit"} <= names  # fmt: skip
    assert len(names) == len(rg._entries())  # no duplicates were silently dropped


def test_sources_point_at_real_warehouse_columns():
    """Every `table.column` an entry cites exists in the warehouse schema."""
    cols = warehouse_columns()
    cited = 0
    for e in rg.REGISTRY.values():
        for table, column in re.findall(r"\b((?:fact|dim|bridge|coach)_\w+)\.(\w+)", e.source):
            assert table in cols, (e.name, table)
            assert column in cols[table], (e.name, f"{table}.{column}")
            cited += 1
    assert cited >= 12


def test_identifiers_are_real_columns():
    all_cols = set().union(*warehouse_columns().values())
    for e in rg.entries(kind="identifier"):
        assert e.name in all_cols, e.name


def test_available_features_are_real_columns():
    """A feature is a warehouse column, or a column the Waiver Radar feature code computes
    (C3: twm.modules.waiver_radar.features) or the streamer's (S1c:
    twm.modules.streamer.features), or one of the per-game values it computes from warehouse
    columns (snap share = offense_pct, carry share, xFP, FPOE)."""
    from twm.modules.decisions.conversion import FEATURES as GO_FEATURES
    from twm.modules.decisions.fieldgoal import FEATURES as FG_FEATURES
    from twm.modules.decisions.wp_data import FEATURES as WP_FEATURES
    from twm.modules.decisions.wp_data import SMOOTH_FEATURES as WP_SMOOTH
    from twm.modules.streamer.features import FEATURE_COLUMNS as STREAMER_FEATURES
    from twm.modules.waiver_radar.features import FEATURE_COLUMNS

    cols = set().union(*warehouse_columns().values())
    computed = {"offense_snap_share", "carry_share", "xfp", "fpoe", *STREAMER_FEATURES,
                *WP_FEATURES, *WP_SMOOTH, *GO_FEATURES, *FG_FEATURES}  # fmt: skip
    for e in rg.entries(kind="feature", status="available"):
        assert e.name in cols or e.name in computed or e.name in FEATURE_COLUMNS, e.name
    # every streamer feature is registered for the streamer, and nothing else is
    streamer = {e.name for e in rg.REGISTRY.values() if e.kind == "feature"
                and "streamer" in e.modules}  # fmt: skip
    assert streamer == set(STREAMER_FEATURES)
    # G1/G2/G1b: every WP (+ the G1b drive features), go-for-it and field-goal feature is
    # registered for the decisions module
    decisions = {e.name for e in rg.REGISTRY.values() if e.kind == "feature"
                 and "decisions" in e.modules}  # fmt: skip
    assert decisions == {*WP_FEATURES, *WP_SMOOTH, *GO_FEATURES, *FG_FEATURES}


def test_every_waiver_radar_feature_is_registered():
    from twm.modules.waiver_radar.features import FEATURE_COLUMNS

    used = rg.check_features(FEATURE_COLUMNS, "waiver_radar")
    assert [e.name for e in used] == list(FEATURE_COLUMNS)
    assert all(e.step == "C3" or e.name in ("ppg_to_date", "preseason_pos_rank") for e in used)


def test_check_features_accepts_available_features():
    used = rg.check_features(["offense_snap_share", "target_share", "wopr"], "waiver_radar")
    assert [e.name for e in used] == ["offense_snap_share", "target_share", "wopr"]


@pytest.mark.parametrize(
    ("column", "message"),
    [
        ("gsis_id", "identifier can never be a feature"),
        ("team", "identifier can never be a feature"),
        ("y_hit", "registered as a label"),
        ("fantasy_points", "registered as a metric"),
        ("snap_shar", "not registered (did you mean snap_share_last?)"),
    ],
)
def test_check_features_refuses(column, message):
    with pytest.raises(rg.FeatureCheckError, match=re.escape(message)):
        rg.check_features(["target_share", column], "waiver_radar")


def test_check_features_refuses_planned_entries(monkeypatch):
    planned = rg.Entry(name="future_signal", title="F", kind="feature",
                       modules=("waiver_radar",), unit="u", formula="f", explanation="e",
                       status="planned", step="C9")  # fmt: skip
    monkeypatch.setitem(rg.REGISTRY, "future_signal", planned)
    with pytest.raises(rg.FeatureCheckError, match="planned for C9"):
        rg.check_features(["target_share", "future_signal"], "waiver_radar")


def test_check_features_reports_every_problem_at_once():
    with pytest.raises(rg.FeatureCheckError) as err:
        rg.check_features(["gsis_id", "y_hit", "nope"], "waiver_radar")
    assert str(err.value).count("\n  ") == 3


def test_check_features_respects_modules():
    with pytest.raises(rg.FeatureCheckError, match="not registered for module decisions"):
        rg.check_features(["target_share"], "decisions")
    with pytest.raises(ValueError, match="unknown module"):
        rg.check_features(["target_share"], "radar")


def test_entry_validation():
    base = dict(title="T", kind="feature", modules=("shared",), unit="u", formula="f",
                explanation="e")  # fmt: skip
    with pytest.raises(ValueError, match="snake_case"):
        rg.Entry(name="Bad Name", **base)
    with pytest.raises(ValueError, match="must name the step"):
        rg.Entry(name="x", status="planned", **base)
    with pytest.raises(ValueError, match="subset of"):
        rg.Entry(name="x", **{**base, "modules": ("radar",)})
    with pytest.raises(ValueError, match="unknown fields"):
        rg.Entry(name="x", reason_template="{who} did it", **base)
    with pytest.raises(ValueError, match="duplicate"):
        rg._build([rg.Entry(name="x", **base), rg.Entry(name="x", **base)])


def test_reason_templates_render():
    snap = rg.get("offense_snap_share")
    text = snap.reason(player="Jordan Example", prev=0.41, value=0.78)
    assert text == "Jordan Example's snap share went from 41% to 78%"
    points = rg.get("fantasy_points").reason(player="A", value=18.26)
    assert points == "A scored 18.3 fantasy points"
    for e in rg.REGISTRY.values():
        if e.reason_template:
            e.reason(player="P", value=0.5, prev=0.25, delta=0.25, weeks=3, teammate="T", team="X",
                     why="is out", pos="WR")  # fmt: skip
    # C6: a yes/no feature can phrase "no"; its fields are checked like the template's
    assert rg.get("bye_in_next3").reason_if_false == "No bye week in the next 3 weeks"
    with pytest.raises(ValueError, match="unknown fields"):
        rg.Entry(name="x", title="T", kind="feature", modules=("shared",), unit="u", formula="f",
                 explanation="e", reason_template="a", reason_if_false="{who}")  # fmt: skip
    with pytest.raises(ValueError, match="needs a reason_template"):
        rg.Entry(name="x", title="T", kind="feature", modules=("shared",), unit="u", formula="f",
                 explanation="e", reason_if_false="no")  # fmt: skip
    with pytest.raises(ValueError, match="no reason template"):
        rg.get("wp").reason(value=0.5)


def test_garbage_time_formula_follows_the_config():
    assert rg.get("is_garbage_time").formula == SituationRules.from_config().describe_garbage_time()


def test_unknown_name_suggests_close_matches():
    with pytest.raises(KeyError, match="did you mean"):
        rg.get("woprr")


def test_glossary_doc_is_in_sync():
    committed = (ROOT / "docs" / "glossary.md").read_text()
    assert committed == rg.glossary_markdown(), "run `uv run twm glossary --write`"


def test_glossary_cli(tmp_path, monkeypatch):
    runner = CliRunner()
    listing = runner.invoke(app, ["glossary"])
    assert listing.exit_code == 0 and "offense_snap_share" in listing.output
    one = runner.invoke(app, ["glossary", "wopr"])
    assert one.exit_code == 0 and "1.5 x target_share" in one.output
    bad = runner.invoke(app, ["glossary", "woprr"])
    assert bad.exit_code == 1 and "did you mean" in bad.output
    import twm.config

    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(twm.config, "ROOT", tmp_path)
    wrote = runner.invoke(app, ["glossary", "--write"])
    assert wrote.exit_code == 0
    assert (tmp_path / "docs" / "glossary.md").read_text() == rg.glossary_markdown()

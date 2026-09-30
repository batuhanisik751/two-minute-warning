"""Static checks of the scheduled workflows (step E4): they parse, never commit or push, can
only read the repository, run on the schedule the config describes, and keep the secret and
the inputs out of their shell scripts."""

from __future__ import annotations

import re

import pytest
import yaml

from twm.config import ROOT, settings
from twm.pipeline import schedule as sc

WORKFLOWS = ROOT / ".github" / "workflows"
MINE = ("pipeline.yml", "retrain.yml")


def load(name: str) -> dict:
    wf = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(wf, dict), name
    # YAML 1.1 reads the key `on` as True
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def steps(wf: dict) -> list[dict]:
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


@pytest.mark.parametrize("name", MINE)
def test_parses_and_can_only_read_the_repository(name: str) -> None:
    wf = load(name)
    assert wf["permissions"] == {"contents": "read"}
    for job_name, job in wf["jobs"].items():
        assert "permissions" not in job, f"{name}: job {job_name} must not widen permissions"
        assert int(job["timeout-minutes"]) <= 60
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_no_workflow_commits_or_pushes() -> None:
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert {f.name for f in files} >= set(MINE)
    pattern = re.compile(r"git\s+(commit|push)|git-auto-commit|create-pull-request", re.I)
    for f in files:
        assert not pattern.search(f.read_text()), f"{f.name} must never commit or push"


@pytest.mark.parametrize("name", MINE)
def test_scripts_never_interpolate_expressions(name: str) -> None:
    """Secrets and inputs reach the scripts only through `env:` (no script injection, and
    the connection string never becomes part of a command line)."""
    wf = load(name)
    for step in steps(wf):
        if "run" in step:
            assert "${{" not in step["run"], f"{name}: {step.get('name') or step['run'][:40]}"
    text = (WORKFLOWS / name).read_text()
    for m in re.finditer(r"\$\{\{\s*secrets\.(\w+)", text):
        assert m.group(1) == "DATABASE_URL"
    for step in steps(wf):
        for value in (step.get("with") or {}).values():
            assert "secrets." not in str(value)


def test_the_pipeline_schedule_matches_the_config() -> None:
    wf = load("pipeline.yml")
    cfg = settings().pipeline
    crons = [c["cron"] for c in wf["on"]["schedule"]]
    assert crons == [sc.nightly_cron(cfg), *sc.retry_crons(cfg)]
    # every retry comes after the weekly as-of (Tuesday 14:00 UTC)
    asof = settings().as_of.weekly
    assert asof.weekday == "tuesday" and asof.time == "14:00"
    assert all(day != "tuesday" or hhmm > "14:00" for day, hhmm in cfg.attempts())
    # never on the hour: GitHub delays scheduled runs most at the start of every hour
    assert all(not c.startswith("0 ") for c in crons)


def test_the_pipeline_inputs_and_steps() -> None:
    wf = load("pipeline.yml")
    inputs = wf["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"week", "dry_run", "skip_publish"}
    assert inputs["dry_run"]["type"] == inputs["skip_publish"]["type"] == "boolean"
    run = next(s for s in steps(wf) if s.get("id") == "run")
    assert run["env"]["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
    assert "uv run twm pipeline run" in run["run"]
    uses = [s["uses"] for s in steps(wf) if "uses" in s]
    assert any(u.startswith("actions/cache/restore@") for u in uses)
    assert any(u.startswith("actions/cache/save@") for u in uses)
    upload = next(s for s in steps(wf) if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()" and int(upload["with"]["retention-days"]) == 90
    save = next(s for s in steps(wf) if s.get("uses", "").startswith("actions/cache/save@"))
    assert "steps.run.outputs.ingest == 'ok'" in save["if"]


def test_the_retrain_workflow_is_manual_and_only_uploads() -> None:
    wf = load("retrain.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    text = (WORKFLOWS / "retrain.yml").read_text()
    assert "secrets." not in text  # it never needs the database
    assert "twm model candidate" in text and "twm publish" not in text
    # the candidate's backtest is made first, so it is frozen with the candidate model
    assert text.index("twm radar backtest --model logit") < text.index("twm model candidate")
    assert not any(s.get("uses", "").startswith("actions/cache/save@") for s in steps(wf))


def test_the_scheduled_path_never_retrains() -> None:
    """The pipeline restores the approved backtest and scores with the approved model: no
    `twm radar backtest`, `twm train` or `--retrain` anywhere in its code or workflow."""
    text = (WORKFLOWS / "pipeline.yml").read_text()
    code = (ROOT / "src" / "twm" / "pipeline" / "runner.py").read_text()
    for needle in ('"radar", "backtest"', '"train"', "--retrain", "radar backtest",
                   '"streamer", "backtest"', "streamer backtest", '"regression", "backtest"',
                   "regression backtest", '"pin"', "streamer pin", "regression pin"):  # fmt: skip
        assert needle not in text and needle not in code, needle
    assert '"restore-backtest"' in code and '"--pinned"' in code
    # P2: the streamer's frozen backtest is only checked; both modules score with their pins
    assert '["model", "check", "streamer"' in code
    assert '["streamer", "score"]' in code and '["regression", "score"]' in code


def test_one_publish_carries_every_module() -> None:
    """P2: the streamer and Regression Watch are scored after the Radar, then ONE publish."""
    code = (ROOT / "src" / "twm" / "pipeline" / "runner.py").read_text()
    assert code.count('"publish", "--target"') == 1
    stages = code[code.index("def _stages(") :]
    order = ['r.command("dataset"', 'r.score(p)', 'r.command("streamer_dataset"',
             '"streamer_backtest"', '"regression_backtest"', "for module in MODULE_SCORES",
             "r.publish()"]  # fmt: skip
    at = [stages.index(s) for s in order]
    assert at == sorted(at), order
    # Regression Watch's backtest lists are frozen (the reviewer's rule after P2): the publish
    # reads the pinned snapshot and never recomputes D3 (nor needs the warehouse before 2012)
    assert settings().pipeline.build_start == 2012
    publish = (ROOT / "src" / "twm" / "publish" / "regression_lists.py").read_text()
    for needle in ("build_rows", "bt.choose", "make_params", "player_games_history(db, list(range("
                   "pj.FIRST_DATA_SEASON"):  # fmt: skip
        assert needle not in publish, needle
    assert "load_snapshot" in publish

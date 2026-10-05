"""The scheduled pipeline's stage runner (step E4): stage order, arguments and exit codes
(not ready vs failure vs skipped publish), with a fake executor; and the real executor's
scrubbing and timeout."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from twm.pipeline import runner as rn
from twm.pipeline import schedule as sc

SECRET = "postgresql://twm_job:S3cret-Passw0rd-xyz@ep-x.us-east-1.aws.neon.tech/neondb?sslmode=verify-full"  # noqa: E501
DATES = sc.SeasonDates(2026, date(2026, 9, 10), date(2027, 1, 10))


def t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def windows() -> list[sc.WeekWindow]:
    out = []
    for w in range(1, 19):
        a = t("2026-09-15T14:00") + timedelta(days=7 * (w - 1))
        out.append(sc.WeekWindow(w, a, None if w == 18 else a + timedelta(days=2, hours=10)))
    return out


class FakeTarget:
    name = "remote"

    def describe(self) -> str:
        return "remote database 'neondb' on ep-x (connection string not shown)"


@dataclass
class Fake:
    """A fake executor (exit codes per stage; a list = one code per call) and fake hooks."""

    codes: dict[str, object] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    dates: sc.SeasonDates | None = DATES
    model_error: str | None = None
    eos_due: datetime | None = None  # the Hot-Seat end-of-season snapshot due time
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)

    def execute(self, stage: str, args, log: Path, timeout: float) -> int:
        self.calls.append((stage, list(args)))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"{stage} output\n")
        code = self.codes.get(stage, 0)
        if isinstance(code, list):
            code = code.pop(0)
        if stage == "publish" and code == 0:
            out = Path(args[args.index("--result-json") + 1])
            out.write_text(json.dumps({"tables": {"glossary": {"rows": 101, "written": 0,
                                       "note": "unchanged (not rewritten)"}},
                                       "live": {"insert": ["2026-W03 QB"]}}))  # fmt: skip
        return int(code)  # type: ignore[arg-type]

    def hooks(self) -> rn.Hooks:
        def check_model(season: int) -> str:
            if self.model_error:
                raise ValueError(self.model_error)
            return "approved model logit-test"

        def record(target, **kw) -> bool:
            self.failures.append(kw)
            return True

        return rn.Hooks(
            check_model=check_model, season_dates=lambda s: self.dates,
            windows=lambda s: windows(), missing_cache=lambda s: list(self.missing),
            export_list=lambda s, w, out: {"files": [out / f"{s}-W{w:02d}.csv"], "kind": "live",
                                           "incomplete": False},
            export_module=lambda m, s, w, out: {"files": [out / f"{m}-{s}-W{w:02d}.csv"],
                                                "kind": "live", "incomplete": False},
            resolve_target=lambda mode: FakeTarget(), record_failure=record,
            sleep=self.slept.append,
            decisions_summary=lambda s: f"{s}: 9 fourth downs graded (4 clear)",
            end_of_season_due=lambda s: self.eos_due,
        )  # fmt: skip

    def stages(self) -> list[str]:
        return [s for s, _ in self.calls]

    def args(self, stage: str) -> list[list[str]]:
        return [a for s, a in self.calls if s == stage]


def run(fake: Fake, tmp_path: Path, now: str = "2026-09-29T15:20", *, env=None, **kw):
    opts = rn.Options(out=tmp_path / "run", now=t(now), trigger=kw.pop("trigger", "schedule"),
                      cron=kw.pop("cron", "17 15 * * 2"), run_id="run-1", **kw)  # fmt: skip
    env = {"DATABASE_URL": SECRET} if env is None else env
    return rn.run(opts, execute=fake.execute, hooks=fake.hooks(), env=env)


def test_a_full_run_in_season(tmp_path: Path) -> None:
    fake = Fake()
    res = run(fake, tmp_path)
    assert res.exit_code == rn.EXIT_OK and res.status == "ok", res.errors
    assert fake.stages() == ["ingest", "build", "dataset", "backtest", "score", "streamer_dataset",
                             "streamer_backtest", "regression_backtest", "streamer_score",
                             "regression_score", "decisions_backtest", "decisions",
                             "hotseat_score", "questionable", "publish"]  # fmt: skip
    assert fake.args("ingest") == [["ingest", "--start", "2026", "--force"]]
    # Regression Watch's backtest lists are frozen: the job's warehouse still starts in 2012
    assert fake.args("build") == [["build", "--start", "2012", "--end", "2026"]]
    assert fake.args("streamer_dataset") == [["streamer", "dataset", "--end", "2026"]]
    # the streamer's frozen backtest is checked, never retrained or restored
    assert fake.args("streamer_backtest") == [["model", "check", "streamer", "--season", "2026"]]
    # Regression Watch's frozen backtest lists: checked, never recomputed on the runner
    assert fake.args("regression_backtest") == [["model", "check", "regression_watch",
                                                 "--season", "2026"]]  # fmt: skip
    # step P3: the decisions' approved grading and frozen history are checked, never regraded;
    # the season in progress is graded with the pins on every run
    assert fake.args("decisions_backtest") == [["model", "check", "decisions", "--season",
                                                "2026"]]  # fmt: skip
    assert fake.args("decisions") == [["decisions", "grade-pinned", "--season", "2026"]]
    assert res.decisions == "2026: 9 fourth downs graded (4 clear)"
    for stage, cmd in (("streamer_score", ["streamer", "score"]),
                       ("regression_score", ["regression", "score"]),
                       ("hotseat_score", ["hotseat", "score"])):  # fmt: skip
        args = fake.args(stage)[0]
        assert args[:6] == [*cmd, "--season", "2026", "--week", "3"] and "--pinned" not in args
        assert args[args.index("--out") + 1].endswith("-2026-W03.md") and "--now" in args
    assert res.modules == {
        "streamer": {"score": "scored", "list_kind": "live"},
        "regression_watch": {"score": "scored", "list_kind": "live"},
        "hot_seat": {"score": "scored", "list_kind": "live", "week": "3"},
    }
    # the approved backtest is restored, never retrained
    assert fake.args("backtest") == [["model", "restore-backtest", "waiver_radar", "--season",
                                      "2026"]]  # fmt: skip
    score = fake.args("score")[0]
    assert score[:5] == ["radar", "score", "--season", "2026", "--week"] and score[5] == "3"
    assert "--pinned" in score and score[score.index("--now") + 1].startswith("2026-09-29T15:20")
    pub = fake.args("publish")[0]
    assert pub[:3] == ["publish", "--target", "remote"] and "--dry-run" not in pub
    assert pub[pub.index("--run-id") + 1] == "run-1"
    notes = json.loads(Path(pub[pub.index("--notes-file") + 1]).read_text())["pipeline"]
    assert notes["week"] == 3 and notes["score"] == "scored" and notes["trigger"] == "schedule"
    order = ["preflight", "gate", "ingest", "build", "plan", "dataset", "backtest", "score",
             "export", "streamer_dataset", "streamer_backtest", "regression_backtest",
             "streamer_score", "streamer_export", "regression_score", "regression_export",
             "decisions_backtest", "decisions", "hotseat_score", "hotseat_export",
             "board_score", "questionable", "publish"]  # fmt: skip
    assert [s.name for s in res.stages] == order
    assert res.stage("board_score").status == "skipped"  # step I6b: outside its window
    assert res.week == 3 and res.list_kind == "live" and res.publish == "published"
    out = tmp_path / "run"
    summary = (out / "summary.md").read_text()
    assert "pipeline: done" in summary and "| glossary | 101 | 0 | unchanged" in summary
    assert "**K and D/ST streamer:** week 3: scored, stored as 'live'" in summary
    assert "**Regression Watch:** week 3: scored" in summary
    assert "**Decisions:** 2026: 9 fourth downs graded (4 clear)" in summary
    assert "**Hot-Seat Meter:** week 3: scored, stored as 'live'" in summary
    assert notes["modules"]["streamer"]["score"] == "scored"
    assert json.loads((out / "result.json").read_text())["exit_code"] == 0
    assert SECRET not in summary and "S3cret" not in (out / "result.json").read_text()


def test_no_database_url_runs_everything_and_skips_the_publish(tmp_path: Path) -> None:
    fake = Fake()
    res = run(fake, tmp_path, env={}, github=True)
    assert res.exit_code == rn.EXIT_OK and res.status == "warning"
    assert "publish" not in fake.stages() and "score" in fake.stages()
    assert res.publish == "skipped (no DATABASE_URL secret)"
    assert any("DATABASE_URL is not set" in w for w in res.warnings)
    assert res.stage("publish").status == "skipped"


def test_skip_publish_and_dry_run(tmp_path: Path) -> None:
    fake = Fake()
    res = run(fake, tmp_path / "a", skip_publish=True)
    assert res.exit_code == 0 and "publish" not in fake.stages()
    assert res.publish == "skipped (asked)" and res.status == "ok"
    fake = Fake()
    res = run(fake, tmp_path / "b", dry_run=True)
    assert "--dry-run" in fake.args("publish")[0] and res.publish == "dry_run"


def test_not_ready_on_an_early_attempt_is_a_warning(tmp_path: Path) -> None:
    fake = Fake(codes={"score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path, now="2026-09-29T15:20")
    assert res.exit_code == rn.EXIT_OK and res.status == "warning"
    assert res.score == "not_ready" and "publish" in fake.stages()  # outcomes stay fresh
    assert "early attempt" in res.attempt and fake.failures == []


def test_not_ready_on_the_last_attempt_fails_with_code_3(tmp_path: Path) -> None:
    fake = Fake(codes={"score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path, now="2026-09-30T03:20", cron="17 3 * * 3")
    assert res.exit_code == rn.EXIT_NOT_READY and res.status == "late"
    assert res.score == "late" and "publish" in fake.stages()
    assert any("has not arrived by the last attempt" in e for e in res.errors)
    notes = json.loads(Path(fake.args("publish")[0][fake.args("publish")[0].index(
        "--notes-file") + 1]).read_text())["pipeline"]  # fmt: skip
    assert notes["score"] == "late"


def test_the_streamer_and_regression_watch_follow_the_same_not_ready_rules(
    tmp_path: Path,
) -> None:
    # an early attempt: a warning, the other lists and the one publish still run
    fake = Fake(codes={"streamer_score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path / "a", now="2026-09-29T15:20")
    assert res.exit_code == rn.EXIT_OK and res.status == "warning" and res.score == "scored"
    assert res.modules["streamer"]["score"] == "not_ready"
    assert fake.stages()[-6:] == ["regression_score", "decisions_backtest", "decisions",
                                  "hotseat_score", "questionable", "publish"]  # fmt: skip
    assert res.stage("streamer_export") is None
    assert any(w.startswith("streamer: week 3's data has not fully arrived") for w in res.warnings)
    # the last attempt: late (exit code 3), still published once
    fake = Fake(codes={"regression_score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path / "b", now="2026-09-30T03:20", cron="17 3 * * 3")
    assert res.exit_code == rn.EXIT_NOT_READY and res.status == "late"
    assert res.score == "scored" and res.modules["regression_watch"]["score"] == "late"
    assert fake.stages().count("publish") == 1
    assert any(e.startswith("regression_score: regression watch: week 3") for e in res.errors)


def test_a_failed_streamer_or_regression_stage_stops_before_the_publish(tmp_path: Path) -> None:
    for i, stage in enumerate(("streamer_dataset", "streamer_backtest", "regression_backtest",
                               "streamer_score", "regression_score", "decisions_backtest",
                               "decisions", "hotseat_score")):  # fmt: skip
        fake = Fake(codes={stage: 1})
        res = run(fake, tmp_path / str(i))
        assert res.exit_code == rn.EXIT_FAILED and res.status == "failed", stage
        assert "publish" not in fake.stages() and fake.failures[0]["stage"] == stage
        assert fake.stages()[-1] == stage


def test_a_failed_stage_stops_the_run_and_is_recorded(tmp_path: Path) -> None:
    fake = Fake(codes={"ingest": [1, 1]})
    res = run(fake, tmp_path)
    assert res.exit_code == rn.EXIT_FAILED and res.status == "failed"
    assert fake.stages() == ["ingest", "ingest"] and fake.slept == [rn.INGEST_RETRY_WAIT_S]
    assert len(fake.failures) == 1 and fake.failures[0]["stage"] == "ingest"
    assert fake.failures[0]["notes"]["step"] == "ingest"
    assert "ingest output" in fake.failures[0]["error"]  # the log's last lines
    assert res.stage("ingest").status == "failed" and res.stage("build") is None


def test_a_flaky_download_is_retried_once(tmp_path: Path) -> None:
    fake = Fake(codes={"ingest": [1, 0]})
    res = run(fake, tmp_path)
    assert res.exit_code == 0 and res.status == "warning" and fake.failures == []
    assert any("retrying once" in w for w in res.warnings)


def test_a_cold_cache_ingests_the_history_first(tmp_path: Path) -> None:
    fake = Fake(missing=["pbp 1999", "players all"])
    res = run(fake, tmp_path)
    assert res.exit_code == 0
    assert fake.args("ingest") == [["ingest", "--end", "2025"],
                                   ["ingest", "--start", "2026", "--force"]]  # fmt: skip
    assert "cold cache (2 files missing" in res.stage("ingest").detail


def test_other_failures(tmp_path: Path) -> None:
    fake = Fake(codes={"score": 1})
    res = run(fake, tmp_path / "a")
    assert res.exit_code == 1 and "publish" not in fake.stages()
    assert fake.failures[0]["stage"] == "score"
    # a refused publish (the shrink guard): the publish recorded its own failed row
    fake = Fake(codes={"publish": rn.PUBLISH_SHRINK_REFUSED})
    res = run(fake, tmp_path / "b")
    assert res.exit_code == 1 and res.publish == "refused (a table would shrink)"
    assert fake.failures == []
    # the approved model does not load: nothing runs
    fake = Fake(model_error="the approved model file is missing")
    res = run(fake, tmp_path / "c")
    assert res.exit_code == 1 and fake.calls == [] and "file is missing" in res.errors[0]
    # in a dry run nothing is recorded
    fake = Fake(codes={"build": 1})
    res = run(fake, tmp_path / "d", dry_run=True)
    assert res.exit_code == 1 and fake.failures == []


def test_offseason_days_stop_at_the_gate(tmp_path: Path) -> None:
    fake = Fake()
    res = run(fake, tmp_path, now="2027-03-03T10:50", cron="47 10 * * *")
    assert res.exit_code == 0 and res.status == "skipped" and fake.calls == []
    fake = Fake()
    res = run(fake, tmp_path / "tue", now="2027-03-02T10:50", cron="47 10 * * *")
    assert res.exit_code == 0 and "ingest" in fake.stages() and "score" not in fake.stages()
    assert res.stage("score").status == "skipped" and "publish" in fake.stages()


def test_no_list_due_still_refreshes_and_publishes(tmp_path: Path) -> None:
    fake = Fake()
    res = run(fake, tmp_path, now="2026-10-02T10:50", cron="47 10 * * *")  # Friday
    assert res.exit_code == 0 and res.week is None and "score" not in fake.stages()
    assert "publish" in fake.stages() and "window closed" in res.plan
    # the decisions are graded whether or not a list is due (fresh games every night)
    assert fake.stages()[-4:] == ["decisions_backtest", "decisions", "questionable", "publish"]


def test_github_outputs_summary_and_annotations(tmp_path: Path, capsys) -> None:
    fake = Fake(codes={"score": rn.SCORE_NOT_READY})
    out_file, summary = tmp_path / "gh_output", tmp_path / "gh_summary"
    env = {"GITHUB_OUTPUT": str(out_file), "GITHUB_STEP_SUMMARY": str(summary)}
    res = run(fake, tmp_path, env=env, github=True)
    assert res.exit_code == 0
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    assert outputs == {"status": "warning", "exit_code": "0", "ingest": "ok", "week": "3"}
    assert summary.read_text().startswith("## Two-Minute Warning pipeline: done, with warnings")
    printed = capsys.readouterr().out
    assert "::warning title=Two-Minute Warning pipeline::" in printed
    assert "::group::ingest" in printed and "::endgroup::" in printed


def test_the_executor_scrubs_the_connection_string(tmp_path: Path) -> None:
    execute = rn.subprocess_executor(
        [SECRET], echo=False, prefix=[sys.executable, "-c", "import sys; print(sys.argv[1:])"]
    )
    log = tmp_path / "x.log"
    assert execute("publish", [SECRET, "S3cret-Passw0rd-xyz"], log, 30) == 0
    text = log.read_text()
    assert "S3cret" not in text and "***" in text


def test_the_executor_stops_a_stage_at_its_limit(tmp_path: Path) -> None:
    execute = rn.subprocess_executor(
        [], echo=False, prefix=[sys.executable, "-c", "import time; time.sleep(30)"]
    )
    log = tmp_path / "slow.log"
    assert execute("build", [], log, 0.5) == 124
    assert "killed after" in log.read_text()


def test_exit_codes_per_status() -> None:
    assert {s: rn.exit_code_for(s) for s in ("ok", "warning", "skipped", "failed", "late")} == {
        "ok": 0, "warning": 0, "skipped": 0, "failed": 1, "late": 3}  # fmt: skip


@pytest.mark.parametrize("trigger", ["workflow_dispatch", "schedule"])
def test_the_cli_maps_github_event_names(trigger: str, tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    seen = {}

    def fake_run(opts):
        seen["opts"] = opts
        return rn.RunResult(run_id="x", started=datetime.now(UTC), exit_code=3)

    monkeypatch.setattr(rn, "run", fake_run)
    res = CliRunner().invoke(app, ["pipeline", "run", "--trigger", trigger, "--out",
                                   str(tmp_path), "--week", "", "--no-github"])  # fmt: skip
    assert res.exit_code == 3, res.output
    o = seen["opts"]
    assert o.trigger == ("manual" if trigger == "workflow_dispatch" else "schedule")
    assert o.week is None and o.github is False and o.out == tmp_path


def test_the_exported_list_is_the_top_25_per_position() -> None:
    import polars as pl

    from twm.pipeline.report import list_frame

    band = json.dumps({"chance": 0.52, "lo": 0.45, "hi": 0.55})
    reasons = json.dumps([{"text": "Reason A"}, {"text": "Reason B"}])
    rows = pl.DataFrame({
        "season": [2026] * 30, "week": [3] * 30, "as_of": [t("2026-09-29T14:00")] * 30,
        "kind": ["live"] * 30, "incomplete": [None] * 30, "position": ["WR"] * 30,
        "rank": list(range(1, 31)), "gsis_id": [f"00-00{i:05d}" for i in range(30)],
        "name": ["x"] * 30, "team": ["BUF"] * 30, "band": [band] * 30, "score": [0.6] * 30,
        "tier": ["must-add"] * 30, "reasons_json": [reasons] * 30,
        "model_version": ["logit-x"] * 30,
    })  # fmt: skip
    flat = list_frame(rows)
    assert flat.height == 25 and flat.get_column("rank").max() == 25
    first = flat.row(0, named=True)
    assert (first["chance"], first["chance_low"], first["chance_high"]) == (0.52, 0.45, 0.55)
    assert first["reasons"] == "Reason A; Reason B" and first["incomplete"] is False
    assert first["model_prob"] == 0.6


def test_a_bug_in_the_runner_still_ends_with_a_result(tmp_path: Path) -> None:
    fake = Fake()
    hooks = fake.hooks()
    hooks.windows = lambda s: 1 / 0  # the plan's own error handling reports it
    opts = rn.Options(out=tmp_path / "a", now=t("2026-09-29T15:20"), run_id="r")
    res = rn.run(opts, execute=fake.execute, hooks=hooks, env={})
    assert res.exit_code == 1 and res.stage("plan").status == "failed"
    hooks = fake.hooks()
    hooks.export_list = lambda *a: {"files": None}  # a bug: the runner itself crashes
    res = rn.run(rn.Options(out=tmp_path / "b", now=t("2026-09-29T15:20"), run_id="r"),
                 execute=fake.execute, hooks=hooks, env={})  # fmt: skip
    assert res.exit_code == 1 and res.errors[-1].startswith("pipeline: internal error")
    assert (tmp_path / "b" / "summary.md").exists()


# the synthetic season's end-of-season snapshot is due the night before its last week's
# Tuesday as-of: a Sunday-night finale (kickoff 01:20 UTC Monday) + 4 h + the 6 h pbp lag
EOS_DUE = windows()[-1].as_of - timedelta(hours=26, minutes=40)
MONDAY_CRON = "37 11 * * 1"


def test_the_hot_seat_end_of_season_snapshot_is_scored_once_due_before_black_monday(
    tmp_path: Path,
) -> None:
    """Step H4a: no list follows the last regular-season week, but once every team's last
    game is public (the due time) the Hot-Seat end-of-season snapshot is scored, the morning
    after the last game day, a day before the last week's Tuesday as-of."""
    assert EOS_DUE.weekday() == 0 and windows()[-1].as_of > EOS_DUE  # Monday, before Tuesday
    now = (EOS_DUE + timedelta(minutes=20)).isoformat()[:16]
    fake = Fake(eos_due=EOS_DUE)
    res = run(fake, tmp_path / "a", now=now, cron=MONDAY_CRON)
    assert res.exit_code == rn.EXIT_OK, res.errors
    assert res.gate.startswith("the Hot-Seat end-of-season attempt")
    assert res.week is None and res.stage("score").status == "skipped"
    args = fake.args("hotseat_score")[0]
    assert args[:6] == ["hotseat", "score", "--season", "2026", "--week", "18"]
    assert res.modules["hot_seat"]["score"] == "scored" and res.modules["hot_seat"]["week"] == "18"
    summary = (tmp_path / "a" / "run" / "summary.md").read_text()
    assert "**Hot-Seat Meter:** week 18: scored" in summary
    assert fake.stages()[-3:] == ["hotseat_score", "questionable", "publish"]
    # data not arrived yet: a warning (the deadline is the last week's last Tuesday retry)
    fake = Fake(eos_due=EOS_DUE, codes={"hotseat_score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path / "b", now=now, cron=MONDAY_CRON)
    assert res.exit_code == rn.EXIT_OK and res.modules["hot_seat"]["score"] == "not_ready"
    assert "early attempt: the last one is Wed 2027-01-13 03:17 UTC" in " ".join(res.warnings)
    assert fake.stages()[-1] == "publish"
    # ... and still missing at that deadline: late, GitHub emails the owner
    fake = Fake(eos_due=EOS_DUE, codes={"hotseat_score": rn.SCORE_NOT_READY})
    res = run(fake, tmp_path / "c", now="2027-01-13T03:20", cron="17 3 * * 3")
    assert res.modules["hot_seat"]["score"] == "late" and res.exit_code == rn.EXIT_NOT_READY


def test_the_end_of_season_snapshot_waits_for_its_due_time(tmp_path: Path) -> None:
    """An early Monday attempt before every team's last game is public scores nothing and
    says when the snapshot is due; the rest of the run (and the publish) goes ahead."""
    fake = Fake(eos_due=EOS_DUE)
    res = run(fake, tmp_path / "a", now="2027-01-11T05:50", cron="47 5 * * 1")
    assert res.exit_code == rn.EXIT_OK, res.errors
    assert "hotseat_score" not in fake.stages() and fake.stages()[-1] == "publish"
    assert res.stage("hotseat_score").status == "skipped"
    assert "end-of-season snapshot is due at Mon 2027-01-11 11:20 UTC" in (
        res.stage("hotseat_score").detail
    )
    # before the last weeks: nothing about it
    fake = Fake(eos_due=EOS_DUE)
    res = run(fake, tmp_path / "b", now="2026-10-03T12:00", cron="47 10 * * *")
    assert "end-of-season" not in res.stage("hotseat_score").detail


def test_the_monday_attempts_stop_at_the_gate_on_every_other_monday(tmp_path: Path) -> None:
    fake = Fake(eos_due=EOS_DUE)
    res = run(fake, tmp_path, now="2026-10-05T05:50", cron="47 5 * * 1")
    assert res.exit_code == rn.EXIT_OK and res.status == "skipped"
    assert fake.stages() == [] and "go ahead only in the 2 days after" in res.gate


def test_an_unknown_due_time_falls_back_to_the_last_weeks_tuesday_as_of(tmp_path: Path) -> None:
    """Without the warehouse's due time the snapshot waits for the last week's Tuesday as-of
    (later than due, never earlier)."""
    fake = Fake()
    run(fake, tmp_path / "a", now="2027-01-11T11:40", trigger="manual")
    assert "hotseat_score" not in fake.stages()
    fake = Fake()
    res = run(fake, tmp_path / "b", now="2027-01-12T15:20", trigger="manual")
    assert fake.args("hotseat_score")[0][4:6] == ["--week", "18"]
    assert res.modules["hot_seat"]["score"] == "scored"


def test_the_due_time_comes_from_the_warehouse_and_matches_the_snapshot_rows(
    tmp_path: Path,
) -> None:
    """On the synthetic Hot-Seat world (a built warehouse, nothing typed): the plan's due time
    equals the latest team's end-of-season row as-of (anchor last_game_end, the rule `twm
    hotseat score` checks), comes before the last week's Tuesday as-of, and the run scores the
    snapshot from that moment on, not a minute before."""
    from tests.test_hot_seat_features import build_hot_seat_world
    from twm.modules.hot_seat import features as hf
    from twm.warehouse.build import connect

    db = build_hot_seat_world(tmp_path / "world")
    due = sc.end_of_season_due(db, 2025)
    wins = sc.week_windows(db, 2025)
    con = connect(db, read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        rules = hf.HotSeatRules(end_of_season_anchor="last_game_end")
        points = hf.as_of_points(hf._Warehouse(con), 2025, rules, t("2030-01-01T00:00"))
    finally:
        con.close()
    assert due == max(p.as_of for p in points if p.snapshot == "end_of_season")
    assert due is not None and due < max(wins, key=lambda w: w.week).as_of
    for minutes, scored in ((-5, False), (5, True)):
        fake = Fake()
        hooks = fake.hooks()
        hooks.windows = lambda s: wins
        hooks.end_of_season_due = lambda s: sc.end_of_season_due(db, 2025)
        opts = rn.Options(out=tmp_path / f"run{minutes}", now=due + timedelta(minutes=minutes),
                          run_id="r")  # fmt: skip
        res = rn.run(opts, execute=fake.execute, hooks=hooks, env={})
        assert res.exit_code == rn.EXIT_OK, res.errors
        assert ("hotseat_score" in fake.stages()) is scored
        if scored:
            assert fake.args("hotseat_score")[0][4:6] == ["--week", str(wins[-1].week)]


def test_the_refresh_records_each_datasets_newest_week_and_fetch_time(tmp_path: Path) -> None:
    """docs/assumptions.md section 12: per dataset, the newest season/week in the cache after
    the ingest and when it was fetched, in result.json, the summary and pipeline_runs' notes."""
    import polars as pl

    from twm.pipeline import arrivals as ar

    raw = tmp_path / "raw"
    for name, df in {
        "pbp": pl.DataFrame({"season": [2026, 2026], "week": [3, 4]}),
        "schedules": pl.DataFrame(
            {"season": [2026] * 3, "week": [4, 5, 18], "result": [3, None, None]}
        ),
        "depth_charts": pl.DataFrame({"team": ["KC"]}),  # no season or week column
    }.items():
        (raw / name).mkdir(parents=True)
        df.write_parquet(raw / name / "2026.parquet")
    (raw / "teams").mkdir()
    pl.DataFrame({"team_abbr": ["KC"]}).write_parquet(raw / "teams" / "all.parquet")
    (raw / "snap_counts").mkdir()
    (raw / "snap_counts" / "2026.parquet").write_text("not parquet")
    got = ar.dataset_arrivals(2026, raw)
    assert set(got) == {"pbp", "schedules", "depth_charts", "teams", "snap_counts"}
    assert (got["pbp"]["season"], got["pbp"]["week"]) == (2026, 4)
    assert got["schedules"]["week"] == 4  # the newest week with a final result
    assert set(got["depth_charts"]) == {"season", "fetched_utc"}  # no week column
    assert got["teams"]["season"] is None and "error" in got["snap_counts"]
    assert datetime.fromisoformat(got["pbp"]["fetched_utc"]).tzinfo is not None
    # the run: ingest's detail, result.json, the summary and the publish notes carry it
    fake = Fake()
    hooks = fake.hooks()
    hooks.arrivals = lambda s: ar.dataset_arrivals(s, raw)
    opts = rn.Options(out=tmp_path / "run", now=t("2026-09-29T15:20"), run_id="r")
    res = rn.run(opts, execute=fake.execute, hooks=hooks, env={"DATABASE_URL": SECRET})
    assert res.exit_code == rn.EXIT_OK and res.arrivals["pbp"]["week"] == 4
    assert "newest: pbp 2026 week 4 (fetched " in res.stage("ingest").detail
    out = tmp_path / "run"
    assert json.loads((out / "result.json").read_text())["arrivals"]["pbp"]["week"] == 4
    assert "- **Data:** pbp 2026 week 4" in (out / "summary.md").read_text()
    notes = json.loads((out / "publish_notes.json").read_text())["pipeline"]
    assert notes["arrivals"]["schedules"]["week"] == 4
    # unreadable: a warning, the run goes on
    hooks.arrivals = lambda s: 1 / 0
    res = rn.run(rn.Options(out=tmp_path / "run2", now=t("2026-09-29T15:20"), run_id="r"),
                 execute=fake.execute, hooks=hooks, env={})  # fmt: skip
    assert res.exit_code == rn.EXIT_OK and res.arrivals == {}
    assert any("newest weeks could not be read" in w for w in res.warnings)

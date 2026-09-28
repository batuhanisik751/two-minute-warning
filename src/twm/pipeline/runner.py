"""One scheduled run, stage by stage (step E4): ``twm pipeline run``.

The stages, in order (each ``twm`` command runs as its own process, with the same code, the
same exit codes and the same files as when the owner types it):

1. **preflight** (here): the approved model loads (``config/production_models.yaml``: file,
   sha256 and version checked); where the publish goes and, for Neon, which CA bundle checks
   its certificate.
2. **gate** (here, :func:`twm.pipeline.schedule.gate`): offseason days without a run stop here.
3. **ingest**: ``twm ingest --start <season> --force`` (the nightly refresh: the current season
   and the one-file datasets); on a cold cache (historical seasons missing) first
   ``twm ingest --end <season - 1>``. One retry after a pause (downloads can be flaky).
4. **build**: ``twm build --start <build_start> --end <season>``.
5. **plan** (here, :func:`twm.pipeline.schedule.plan_week`): which week's list is due.
6. **dataset**: ``twm radar dataset``.
7. **backtest**: ``twm radar backtest --model logit --label y_hit``, rebuilt on every run (about a
   minute; it reproduces the owner's store exactly). The weekly list's chance, band and priority
   and the published time-machine lists come from it.
8. **score** (only when a list is due): ``twm radar score --pinned`` (exit code 3 = not ready).
9. **export** (here): the scored list as CSV and Parquet for the run's files.
10. **publish**: ``twm publish`` (remote when ``DATABASE_URL`` is set, else skipped with a
    warning), also after a not-ready score, so outcomes and player pages stay fresh.

Exit codes of the run: :data:`EXIT_OK` (0: done, including a skipped publish, an offseason day
and a week that is not ready on an early attempt, each with a warning), :data:`EXIT_FAILED` (1: a
stage failed; the failed stage is recorded in ``pipeline_runs`` when the database is reachable)
and :data:`EXIT_NOT_READY` (3: the week's data had not arrived by the last attempt, or a week
named by the operator is not ready). Every line a stage prints is scrubbed of the connection
string before it reaches the log or the run's files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from twm.pipeline import schedule as sc

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_READY = 3
SCORE_NOT_READY = 3  # `twm radar score`: the week's data has not arrived
PUBLISH_SKIPPED_INCOMPLETE = 4  # `twm publish`: done, but incomplete live lists were skipped
PUBLISH_SHRINK_REFUSED = 5  # `twm publish`: a replaced table would lose too many rows
PUBLISH_MODES = ("auto", "local", "remote", "skip")
# Per-stage limits (seconds); the workflow's job timeout is the outer limit.
TIMEOUTS = {"ingest": 25 * 60, "build": 15 * 60, "dataset": 15 * 60, "backtest": 20 * 60,
            "score": 15 * 60, "publish": 15 * 60}  # fmt: skip
INGEST_RETRY_WAIT_S = 60.0


# --------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------


@dataclass
class StageResult:
    name: str
    status: str  # ok | failed | not_ready | late | skipped | warning
    seconds: float = 0.0
    code: int | None = None
    detail: str = ""
    logs: list[str] = field(default_factory=list)  # paths relative to the run's folder


@dataclass
class Options:
    out: Path
    trigger: str = "manual"  # schedule | manual
    cron: str | None = None  # github.event.schedule
    week: int | None = None
    now: datetime | None = None  # a pretend clock: nothing is stored as live
    dry_run: bool = False
    skip_publish: bool = False
    publish: str = "auto"  # auto | local | remote | skip
    github: bool = False
    run_id: str | None = None
    ingest_retry_wait: float = INGEST_RETRY_WAIT_S
    run_url: str = ""


@dataclass
class RunResult:
    run_id: str
    started: datetime
    finished: datetime | None = None
    status: str = "ok"  # ok | warning | skipped | failed | late
    exit_code: int = EXIT_OK
    season: int | None = None
    trigger: str = "manual"
    cron: str | None = None
    pretend_now: datetime | None = None
    gate: str = ""
    plan: str = ""
    week: int | None = None
    attempt: str = ""
    score: str = "none"  # none | scored | not_ready | late | failed
    list_kind: str = ""  # live | backtest (what the score stored)
    publish: str = "not run"  # published | dry_run | skipped (why) | failed | refused
    target: str = ""
    stages: list[StageResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    publish_result: dict[str, Any] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        end = self.finished or datetime.now(UTC)
        return (end - self.started).total_seconds()

    def stage(self, name: str) -> StageResult | None:
        return next((s for s in self.stages if s.name == name), None)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("started", "finished", "pretend_now"):
            d[k] = None if d[k] is None else d[k].isoformat()
        d["seconds"] = round(self.seconds, 1)
        return d


def exit_code_for(status: str) -> int:
    """The run's exit code for its final status (module docstring)."""
    return {"failed": EXIT_FAILED, "late": EXIT_NOT_READY}.get(status, EXIT_OK)


# --------------------------------------------------------------------------------------
# Running a `twm` command: streamed, scrubbed, logged
# --------------------------------------------------------------------------------------

# (stage, twm arguments, log file, timeout in seconds) -> exit code
Executor = Callable[[str, Sequence[str], Path, float], int]


def secrets_from(env: Mapping[str, str]) -> list[str]:
    """The connection strings a log must never show (scrubbed with their passwords)."""
    return [env[k] for k in ("DATABASE_URL", "TWM_LOCAL_DATABASE_URL", "MIGRATE_DATABASE_URL")
            if env.get(k)]  # fmt: skip


def scrub(text: str, secrets: Sequence[str]) -> str:
    from twm.publish.target import redact

    return redact(text, *secrets) if secrets else text


def subprocess_executor(
    secrets: Sequence[str], *, echo: bool = True, prefix: Sequence[str] | None = None
) -> Executor:
    """Run ``python -m twm.cli <args>`` (``prefix`` replaces ``python -m twm.cli``, for tests)
    in this environment; every output line is scrubbed, printed (``echo``) and written to the
    log file; the process is killed after the timeout (exit code 124 then, as coreutils'
    ``timeout``)."""
    base = list(prefix) if prefix is not None else [sys.executable, "-m", "twm.cli"]

    def execute(stage: str, args: Sequence[str], log: Path, timeout: float) -> int:
        log.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        cmd = [*base, *args]
        killed = threading.Event()
        with log.open("a") as f:
            f.write(scrub(f"$ twm {' '.join(args)}\n", secrets))
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                bufsize=1,
            )  # fmt: skip

            def kill() -> None:
                killed.set()
                proc.kill()

            timer = threading.Timer(timeout, kill)
            timer.start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    clean = scrub(line, secrets)
                    f.write(clean)
                    if echo:
                        sys.stdout.write(clean)
                        sys.stdout.flush()
                code = proc.wait()
            finally:
                timer.cancel()
            if killed.is_set():
                f.write(f"killed after {timeout:.0f} s (stage limit)\n")
                return 124
        return code

    return execute


# --------------------------------------------------------------------------------------
# The in-process pieces (replaceable in tests)
# --------------------------------------------------------------------------------------


@dataclass
class Hooks:
    check_model: Callable[[int], str]  # -> description; raises on a refusal
    season_dates: Callable[[int], sc.SeasonDates | None]
    windows: Callable[[int], list[sc.WeekWindow]]
    missing_cache: Callable[[int], list[str]]  # "<dataset> <season>" missing from the cache
    export_list: Callable[[int, int, Path], dict[str, Any]]  # files, kind, incomplete
    resolve_target: Callable[[str], Any]  # "local" | "remote" -> twm.publish.target.Target
    record_failure: Callable[..., bool]
    sleep: Callable[[float], None] = time.sleep
    ca_bundle: Callable[[], str] = lambda: ca_bundle()  # noqa: E731


def ca_bundle() -> str:
    """Which CA bundle ``twm publish --target remote`` would check Neon's certificate against
    (:data:`twm.publish.target.CA_BUNDLES`, the first that exists), and whether it loads: the
    runner check docs/deploy.md 'TLS' asked for. No network."""
    import ssl

    from twm.publish.target import CA_BUNDLES

    for bundle in CA_BUNDLES:
        if Path(bundle).exists():
            ctx = ssl.create_default_context(cafile=bundle)
            n = ctx.cert_store_stats().get("x509_ca", 0)
            return f"CA bundle {bundle} ({n} certificate authorities)"
    return "no CA bundle found (" + ", ".join(CA_BUNDLES) + ")"


def missing_cache(season: int) -> list[str]:
    """Historical seasons (before ``season``) and one-file datasets missing from the raw cache:
    a cold cache needs a full ingest first."""
    from twm.config import settings
    from twm.sources import nflverse as nv

    out = []
    first_default = settings().seasons["pbp_start"]
    for name, ds in nv.DATASETS.items():
        if not ds.per_season:
            if not nv.cache_path(name, None).exists():
                out.append(f"{name} all")
            continue
        have = set(nv.cached_seasons(name))
        first = ds.first_season or first_default
        out += [f"{name} {s}" for s in range(first, season) if s not in have]
    return out


def default_hooks() -> Hooks:
    from twm import pins
    from twm.config import settings
    from twm.modules.waiver_radar.production import MODULE
    from twm.pipeline import report as rp
    from twm.publish import target as tg
    from twm.publish import write as wr

    def check_model(season: int) -> str:
        pm, pin = pins.load_pinned(MODULE, season)
        return f"approved model {pm.model_version} ({pin.file}, sha256 {pin.sha256[:12]}...)"

    return Hooks(
        check_model=check_model,
        season_dates=lambda s: sc.season_dates_from_cache(settings().path("raw_cache"), s),
        windows=lambda s: sc.week_windows(settings().path("warehouse"), s),
        missing_cache=missing_cache,
        export_list=lambda s, w, out: rp.export_list(
            settings().path("predictions"), settings().path("warehouse"), s, w, out
        ),
        resolve_target=tg.resolve,
        record_failure=wr.record_failure_at,
    )


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def _tail(path: Path, n: int = 6) -> str:
    try:
        lines = [ln for ln in path.read_text().splitlines()
                 if ln.strip() and not ln.startswith("$ twm ")]  # fmt: skip
    except OSError:
        return ""
    return "\n".join(lines[-n:])


class _Run:
    """The state of one run (see :func:`run`)."""

    def __init__(self, opts: Options, execute: Executor, hooks: Hooks, env: Mapping[str, str]):
        from twm.config import settings

        self.opts, self.execute, self.hooks, self.env = opts, execute, hooks, env
        self.s = settings()
        self.season = int(self.s.current_season)
        started = datetime.now(UTC)
        self.now = opts.now or started
        self.out = opts.out
        self.out.mkdir(parents=True, exist_ok=True)
        self.res = RunResult(
            run_id=opts.run_id or uuid.uuid4().hex, started=started, season=self.season,
            trigger=opts.trigger, cron=opts.cron, pretend_now=opts.now,
        )  # fmt: skip
        self.target: Any = None
        self.mode: str | None = None
        self.n_logs = 0

    # -- helpers ------------------------------------------------------------------------

    def add(self, name: str, status: str, t0: float, detail: str = "", **kw: Any) -> StageResult:
        st = StageResult(name, status, round(time.perf_counter() - t0, 1), detail=detail, **kw)
        self.res.stages.append(st)
        return st

    def warn(self, text: str) -> None:
        self.res.warnings.append(text)

    def cli(self, stage: str, args: Sequence[str]) -> tuple[int, Path]:
        self.n_logs += 1
        log = self.out / "logs" / f"{self.n_logs:02d}-{stage}.log"
        if self.opts.github:
            print(f"::group::{stage}: twm {' '.join(args)}", flush=True)
        try:
            code = self.execute(stage, list(args), log, TIMEOUTS.get(stage, 900))
        finally:
            if self.opts.github:
                print("::endgroup::", flush=True)
        return code, log

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.out).as_posix()
        except ValueError:
            return str(path)

    def fail(self, stage: str, message: str, t0: float, *, code: int | None = None,
             logs: Sequence[Path] = (), recorded: bool = False) -> None:  # fmt: skip
        self.add(stage, "failed", t0, message, code=code, logs=[self.rel(p) for p in logs])
        self.res.status = "failed"
        self.res.errors.append(f"{stage}: {message}")
        if recorded or self.target is None or self.opts.dry_run:
            return
        tail = "\n".join(_tail(p) for p in logs)
        error = scrub(f"{stage} failed: {message}" + (f"\n{tail}" if tail else ""),
                      secrets_from(self.env))  # fmt: skip
        try:
            ok = self.hooks.record_failure(
                self.target, run_id=self.res.run_id, started=self.res.started, error=error,
                notes={"step": stage, "pipeline": self.notes()}, stage=stage,
            )  # fmt: skip
        except Exception:  # recording is best effort; the run already failed
            ok = False
        self.warn(
            f"the failed stage was recorded in pipeline_runs ({stage})" if ok
            else "the failure could not be recorded in pipeline_runs (database not reachable?)"
        )  # fmt: skip

    def notes(self) -> dict[str, Any]:
        r = self.res
        return {
            "trigger": r.trigger, "cron": r.cron, "season": r.season, "week": r.week,
            "score": r.score, "attempt": r.attempt, "run_url": self.opts.run_url or None,
            "pretend_now": None if r.pretend_now is None else r.pretend_now.isoformat(),
            "stages": {st.name: {"status": st.status, "seconds": st.seconds} for st in r.stages},
        }  # fmt: skip

    # -- the stages ---------------------------------------------------------------------

    def preflight(self) -> bool:
        t0 = time.perf_counter()
        try:
            desc = self.hooks.check_model(self.season)
        except Exception as e:  # twm.pins.PinError, or an unreadable file
            self.fail("preflight", f"the approved model cannot be used: {e}", t0)
            return False
        o = self.opts
        if o.publish not in PUBLISH_MODES:
            self.fail("preflight", f"--publish must be one of {PUBLISH_MODES}", t0)
            return False
        if o.skip_publish or o.publish == "skip":
            self.res.publish = "skipped (asked)"
        elif o.publish == "auto" and not self.env.get("DATABASE_URL", "").strip():
            self.res.publish = "skipped (no DATABASE_URL secret)"
            self.warn(
                "DATABASE_URL is not set: everything ran, but nothing was published (set the "
                "GitHub secret DATABASE_URL to publish; docs/deploy.md)"
            )
        else:
            self.mode = "remote" if o.publish == "auto" else o.publish
            try:
                self.target = self.hooks.resolve_target(self.mode)
            except Exception as e:  # twm.publish.target.TargetError (never shows the URL)
                self.fail("preflight", f"publish target refused: {e}", t0)
                return False
            self.res.target = self.target.describe()
        if o.dry_run and self.target is None:
            self.warn("--dry-run has nothing to roll back: the publish is skipped anyway")
        detail = desc + (f"; publish to {self.res.target}" if self.target is not None else "")
        try:
            detail += f"; {self.hooks.ca_bundle()}"
        except Exception as e:  # informative only; the publish checks the certificate itself
            detail += f"; CA bundle check failed: {e}"
        self.add("preflight", "ok", t0, detail)
        return True

    def gate(self) -> bool:
        t0 = time.perf_counter()
        g = sc.gate(self.now, trigger=self.opts.trigger, cron=self.opts.cron,
                    dates=self.hooks.season_dates(self.season), cfg=self.s.pipeline)  # fmt: skip
        self.res.gate = g.reason
        self.add("gate", "ok" if g.run else "skipped", t0, g.reason)
        if not g.run:
            self.res.status = "skipped"
            self.res.publish = "skipped (offseason day)"
        return g.run

    def ingest(self) -> bool:
        t0 = time.perf_counter()
        missing = self.hooks.missing_cache(self.season)
        commands = [["ingest", "--start", str(self.season), "--force"]]
        what = "warm cache: the current season and the one-file datasets refreshed"
        if missing:
            commands.insert(0, ["ingest", "--end", str(self.season - 1)])
            what = f"cold cache ({len(missing)} files missing, e.g. {missing[0]}): full ingest"
        logs: list[Path] = []
        codes: list[int] = []
        for attempt in (1, 2):
            codes = []
            for args in commands:
                code, log = self.cli("ingest", args)
                logs.append(log)
                codes.append(code)
            if all(c == 0 for c in codes):
                break
            if attempt == 1:
                self.warn(f"ingest failed (exit {codes}); retrying once in "
                          f"{self.opts.ingest_retry_wait:.0f} s")  # fmt: skip
                self.hooks.sleep(self.opts.ingest_retry_wait)
        if any(c != 0 for c in codes):
            self.fail("ingest", f"downloads failed twice (exit codes {codes})", t0,
                      code=max(codes), logs=logs)  # fmt: skip
            return False
        self.add("ingest", "ok", t0, what, code=0, logs=[self.rel(p) for p in logs])
        return True

    def command(self, stage: str, args: Sequence[str], detail: str = "") -> bool:
        t0 = time.perf_counter()
        code, log = self.cli(stage, args)
        if code != 0:
            self.fail(stage, f"`twm {' '.join(args)}` exited with {code}", t0, code=code,
                      logs=[log])  # fmt: skip
            return False
        self.add(stage, "ok", t0, detail, code=0, logs=[self.rel(log)])
        return True

    def plan(self) -> sc.Plan | None:
        t0 = time.perf_counter()
        try:
            windows = self.hooks.windows(self.season)
        except Exception as e:
            self.fail("plan", f"cannot read the season's weeks from the warehouse: {e}", t0)
            return None
        p = sc.plan_week(self.now, self.season, windows, self.s.pipeline, week=self.opts.week)
        self.res.plan, self.res.week = p.reason, p.week
        self.res.attempt = p.attempt_text(self.now)
        self.add("plan", "ok", t0, p.reason + (f"; {self.res.attempt}" if self.res.attempt else ""))
        return p

    def score(self, p: sc.Plan) -> bool:
        t0 = time.perf_counter()
        if p.week is None:
            self.add("score", "skipped", t0, p.reason)
            return True
        report = self.out / "reports" / f"{self.season}-W{p.week:02d}.md"
        args = ["radar", "score", "--season", str(self.season), "--week", str(p.week),
                "--pinned", "--out", str(report)]  # fmt: skip
        if self.opts.now is not None:
            args += ["--now", self.opts.now.isoformat()]
        code, log = self.cli("score", args)
        logs = [self.rel(log)]
        if code == 0:
            self.res.score = "scored"
            self.res.files.append(self.rel(report))
            self.add("score", "ok", t0, f"week {p.week} scored", code=code, logs=logs)
            return True
        if code == SCORE_NOT_READY:
            missing = _tail(log, 8)
            if p.not_ready_fails(self.now):
                self.res.score = "late"
                text = (f"week {p.week}'s data has not arrived by the last attempt "
                        f"({self.res.attempt}); the score log says what is missing")  # fmt: skip
                self.res.errors.append(f"score: {text}")
                self.add("score", "late", t0, text + (f"\n{missing}" if missing else ""),
                         code=code, logs=logs)  # fmt: skip
            else:
                self.res.score = "not_ready"
                text = f"week {p.week}'s data has not fully arrived yet ({self.res.attempt})"
                self.warn(text)
                self.add("score", "not_ready", t0, text + (f"\n{missing}" if missing else ""),
                         code=code, logs=logs)  # fmt: skip
            return True  # the publish still runs: outcomes and player pages stay fresh
        self.res.score = "failed"
        self.fail("score", f"`twm radar score` exited with {code}", t0, code=code, logs=[log])
        return False

    def export(self, p: sc.Plan) -> None:
        if self.res.score != "scored" or p.week is None:
            return
        t0 = time.perf_counter()
        try:
            info = self.hooks.export_list(self.season, p.week, self.out / "lists")
        except Exception as e:  # the list is stored and published anyway
            self.warn(f"the list could not be exported to the run's files: {e}")
            self.add("export", "warning", t0, str(e))
            return
        self.res.list_kind = str(info.get("kind", ""))
        files = [self.rel(Path(f)) for f in info.get("files", [])]
        self.res.files += files
        if info.get("incomplete"):
            self.warn(f"week {p.week}'s list was scored with incomplete data")
        self.add("export", "ok", t0, f"{self.res.list_kind} list: " + ", ".join(files))

    def publish(self) -> bool:
        t0 = time.perf_counter()
        if self.target is None:
            self.add("publish", "skipped", t0, self.res.publish)
            return True
        notes = self.out / "publish_notes.json"
        result = self.out / "publish.json"
        notes.write_text(json.dumps({"pipeline": self.notes()}, indent=2, default=str))
        args = ["publish", "--target", str(self.mode), "--run-id", self.res.run_id,
                "--notes-file", str(notes), "--result-json", str(result)]  # fmt: skip
        if self.opts.dry_run:
            args.append("--dry-run")
        if self.opts.now is not None:
            args += ["--now", self.opts.now.isoformat()]
        code, log = self.cli("publish", args)
        if result.exists():
            try:
                self.res.publish_result = json.loads(result.read_text())
            except ValueError:
                self.res.publish_result = {}
        logs = [self.rel(log)]
        if code in (0, PUBLISH_SKIPPED_INCOMPLETE):
            self.res.publish = "dry_run" if self.opts.dry_run else "published"
            if code == PUBLISH_SKIPPED_INCOMPLETE:
                self.warn("some live lists were scored with incomplete data and not published")
            self.add("publish", "ok", t0, self.res.publish, code=code, logs=logs)
            return True
        # the publish records its own failed pipeline_runs row (it knows the error best)
        self.res.publish = "refused (a table would shrink)" if code == PUBLISH_SHRINK_REFUSED \
            else "failed"  # fmt: skip
        self.fail("publish", f"`twm publish` exited with {code} ({self.res.publish})", t0,
                  code=code, logs=[log], recorded=True)  # fmt: skip
        return False

    def finish(self) -> RunResult:
        r = self.res
        r.finished = datetime.now(UTC)
        if r.status != "failed" and r.status != "skipped":
            r.status = "late" if r.score == "late" else ("warning" if r.warnings else "ok")
        r.exit_code = exit_code_for(r.status)
        return r


def run(
    opts: Options,
    *,
    execute: Executor | None = None,
    hooks: Hooks | None = None,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    """One run of the pipeline (module docstring). Writes ``result.json`` and ``summary.md``
    into ``opts.out``; on GitHub Actions also the job summary, the step outputs and the
    annotations."""
    from twm.pipeline import report as rp

    env = dict(os.environ if env is None else env)
    execute = execute or subprocess_executor(secrets_from(env))
    hooks = hooks or default_hooks()
    r = _Run(opts, execute, hooks, env)
    t0 = time.perf_counter()
    try:
        _stages(r)
    except Exception as e:  # a bug in the runner itself: still a clear result and summary
        r.fail("pipeline", f"internal error: {type(e).__name__}: {e}", t0)
    result = r.finish()
    rp.write_outputs(result, opts, env)
    return result


def _stages(r: _Run) -> None:
    s, season = r.s, r.season
    ok = r.preflight() and r.gate()
    ok = ok and r.ingest()
    ok = ok and r.command("build", ["build", "--start", str(s.pipeline.build_start),
                                    "--end", str(season)],
                          f"warehouse {s.pipeline.build_start}-{season}")  # fmt: skip
    p = r.plan() if ok else None
    ok = ok and p is not None
    ok = ok and r.command("dataset", ["radar", "dataset", "--end", str(season)])
    ok = ok and r.command(
        "backtest",
        ["radar", "backtest", "--model", "logit", "--label", "y_hit",
         "--out", str(r.out / "backtest" / "backtest.md")],
        "logit walk-forward backtest (the bands, priorities and time-machine lists)",
    )  # fmt: skip
    if ok and p is not None:
        ok = r.score(p)
        if ok:
            r.export(p)
            r.publish()

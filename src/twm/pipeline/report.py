"""What a pipeline run leaves behind (step E4): ``result.json`` and ``summary.md`` in the run's
folder (uploaded as the workflow artifact with the logs, the weekly report and the list), the
GitHub job summary, step outputs and annotations; plus the retrain workflow's report.

Nothing here prints or stores a connection string: the texts come from the run's result, whose
messages were scrubbed when they were made, and are scrubbed once more before they are written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

STATUS_TEXT = {
    "ok": "done",
    "warning": "done, with warnings",
    "skipped": "skipped (offseason day)",
    "failed": "FAILED",
    "late": "FAILED: the week's data is late",
}


def _secs(x: float) -> str:
    x = float(x)
    return f"{x:.0f} s" if x < 90 else f"{int(x // 60)} min {int(x % 60):02d} s"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def summary_markdown(result: Any) -> str:
    """The job summary (and ``summary.md``): status, the week, each stage with its time, the
    publish's rows per table, warnings and errors."""
    r = result
    lines = [f"## Two-Minute Warning pipeline: {STATUS_TEXT.get(r.status, r.status)}", ""]
    when = f"started {r.started:%Y-%m-%d %H:%M} UTC, {_secs(r.seconds)}"
    trig = r.trigger + (f" (`{r.cron}`)" if r.cron else "")
    lines.append(f"Run `{r.run_id}` · {trig} · {when} · exit code {r.exit_code}")
    if r.pretend_now is not None:
        lines.append(
            f"Pretend clock: {r.pretend_now:%Y-%m-%d %H:%M} UTC (nothing is stored as live)"
        )
    lines.append("")
    facts = [("Season", str(r.season)), ("Gate", r.gate), ("Plan", r.plan)]
    if r.week is not None:
        kind = f", stored as '{r.list_kind}'" if r.list_kind else ""
        facts.append(("List", f"week {r.week}: {r.score}{kind}" + (
            f" ({r.attempt})" if r.attempt else "")))  # fmt: skip
    facts.append(("Publish", r.publish + (f" to {r.target}" if r.target else "")))
    lines += [f"- **{k}:** {_cell(v)}" for k, v in facts if v]
    lines += ["", "| stage | result | time | notes |", "|---|---|---:|---|"]
    for st in r.stages:
        lines.append(f"| {st.name} | {st.status} | {_secs(st.seconds)} | {_cell(st.detail)} |")
    pub = r.publish_result or {}
    tables = pub.get("tables") or {}
    if tables:
        lines += ["", "| table | rows now | written | note |", "|---|---:|---:|---|"]
        for name, t in tables.items():
            lines.append(
                f"| {name} | {t.get('rows', 0):,} | {t.get('written', 0):,} | "
                f"{_cell(t.get('note', ''))} |"
            )
    for action, labels in (pub.get("live") or {}).items():
        lines.append(f"- live lists {action}: {', '.join(labels)}")
    if r.warnings:
        lines += ["", "**Warnings**", *[f"- {_cell(w)}" for w in r.warnings]]
    if r.errors:
        lines += ["", "**Errors**", *[f"- {_cell(e)}" for e in r.errors]]
    if r.files:
        lines += ["", "Files in the run's artifact: " + ", ".join(f"`{f}`" for f in r.files)]
    return "\n".join(lines) + "\n"


def annotations(result: Any) -> list[str]:
    """GitHub workflow commands: an error per error, a warning per warning, one notice."""
    out = [f"::error title=Two-Minute Warning pipeline::{_one_line(e)}" for e in result.errors]
    out += [f"::warning title=Two-Minute Warning pipeline::{_one_line(w)}"
            for w in result.warnings]  # fmt: skip
    out.append(
        f"::notice title=Two-Minute Warning pipeline::{STATUS_TEXT.get(result.status)}; "
        f"list: {result.score}; publish: {result.publish}"
    )
    return out


def _one_line(text: str) -> str:
    # workflow commands end at a newline; %, \r and \n are escaped as GitHub documents
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def write_outputs(result: Any, opts: Any, env: Mapping[str, str]) -> None:
    """``result.json`` and ``summary.md`` in the run's folder; on GitHub Actions
    (``opts.github``) also the job summary, the step outputs and the annotations."""
    from twm.pipeline.runner import scrub, secrets_from

    secrets = secrets_from(env)
    out = Path(opts.out)
    out.mkdir(parents=True, exist_ok=True)
    text = scrub(summary_markdown(result), secrets)
    (out / "summary.md").write_text(text)
    body = scrub(json.dumps(result.to_json(), indent=2, default=str), secrets)
    (out / "result.json").write_text(body + "\n")
    print(text, flush=True)
    if not opts.github:
        return
    for line in annotations(result):
        print(scrub(line, secrets), flush=True)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text)
    outputs = env.get("GITHUB_OUTPUT")
    if outputs:
        ingest = result.stage("ingest")
        with open(outputs, "a") as f:
            f.write(f"status={result.status}\n")
            f.write(f"exit_code={result.exit_code}\n")
            f.write(f"ingest={'ok' if ingest and ingest.status == 'ok' else 'no'}\n")
            f.write(f"week={'' if result.week is None else result.week}\n")


# --------------------------------------------------------------------------------------
# The scored list, as files
# --------------------------------------------------------------------------------------

LIST_TOP = 25


def list_frame(rows: pl.DataFrame) -> pl.DataFrame:
    """The top 25 of each position of a stored week (with names and teams), flat."""
    top = rows.filter(pl.col("rank") <= LIST_TOP)
    bands = [json.loads(b) if b else {} for b in top.get_column("band").to_list()]
    reasons = [
        "; ".join(str(x.get("text", "")) for x in json.loads(r or "[]"))
        for r in top.get_column("reasons_json").to_list()
    ]
    return top.select(
        "season", "week", "as_of", "kind", pl.col("incomplete").fill_null(False), "position",
        "rank", "gsis_id", "name", "team",
        pl.Series("chance", [b.get("chance") for b in bands], dtype=pl.Float64),
        pl.Series("chance_low", [b.get("lo") for b in bands], dtype=pl.Float64),
        pl.Series("chance_high", [b.get("hi") for b in bands], dtype=pl.Float64),
        pl.col("score").alias("model_prob"), "tier",
        pl.Series("reasons", reasons, dtype=pl.String), "model_version",
    )  # fmt: skip


def export_list(
    store: Path, warehouse: Path, season: int, week: int, out_dir: Path
) -> dict[str, Any]:
    """Write the stored list of ``season`` week ``week`` as CSV and Parquet into ``out_dir``:
    {files, kind, incomplete}."""
    from twm.modules.waiver_radar import weekly as wk

    rows, info = wk.stored_week(store, season, week)
    if rows.height == 0:
        raise ValueError(f"nothing stored for {season} week {week}")
    flat = list_frame(wk.attach_names_and_outcomes(rows, warehouse))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{season}-W{week:02d}"
    csv, parquet = stem.with_suffix(".csv"), stem.with_suffix(".parquet")
    flat.write_csv(csv)
    flat.write_parquet(parquet)
    return {"files": [csv, parquet], "kind": "/".join(info["kind"]),
            "incomplete": bool(info["incomplete"])}  # fmt: skip


# --------------------------------------------------------------------------------------
# The retrain workflow's report (`twm model candidate`)
# --------------------------------------------------------------------------------------


def _compare(new: Any, old: Any, dataset: pl.DataFrame) -> list[str]:
    """How the two models' lists differ on the season's pool rows so far."""
    rows = dataset.filter((pl.col("season") == new.season) & pl.col("in_pool"))
    if rows.height == 0:
        return ["- no pool rows of this season in the dataset yet: no list to compare"]
    _, p_new = new.predict(rows)
    _, p_old = old.predict(rows)
    df = rows.select("week", "position", "gsis_id").with_columns(
        pl.Series("p_new", p_new), pl.Series("p_old", p_old)
    )
    diff = (df.get_column("p_new") - df.get_column("p_old")).abs()
    overlaps = []
    for _, g in df.group_by("week", "position", maintain_order=True):
        a = set(g.sort("p_new", descending=True).head(10).get_column("gsis_id").to_list())
        b = set(g.sort("p_old", descending=True).head(10).get_column("gsis_id").to_list())
        overlaps.append(len(a & b) / max(1, min(10, g.height)))
    weeks = sorted(set(df.get_column("week").to_list()))
    return [
        f"- on the {rows.height:,} pool rows of {new.season} (weeks {weeks[0]}-{weeks[-1]}): "
        f"probabilities differ by {diff.mean():.4f} on average (at most {diff.max():.4f})",
        f"- the top 10 of each weekly list agree on {100 * sum(overlaps) / len(overlaps):.1f}% "
        f"of the players on average ({len(overlaps)} lists)",
    ]


def candidate_report(
    pm: Any, pin: Any, dataset: pl.DataFrame, *, old: Any = None, old_note: str = ""
) -> str:
    """Markdown for the owner's review of a retrained candidate."""
    row = pm.version_row
    lines = [
        f"# Retrain candidate: waiver_radar {pm.season}", "",
        f"- **candidate:** {pm.describe()}",
        f"- **file:** `{pin.file}` (sha256 `{pin.sha256}`)",
        f"- **training data hash:** `{row.get('dataset_hash')}`",
        f"- **settings:** `{json.dumps(pm.params, sort_keys=True, default=str)}`",
        f"- **calibration:** {pm.calibration or '-'}", "",
        "## Compared with the approved model", "",
    ]  # fmt: skip
    if old is None:
        lines.append(f"- no approved model to compare with: {old_note}")
    elif old.model_version == pm.model_version:
        lines.append(
            f"- identical to the approved model {old.model_version} (same training data, "
            "features and settings): nothing to approve"
        )
    else:
        orow = old.version_row
        lines += [
            f"- approved now: {old.describe()}",
            f"- training data hash then `{orow.get('dataset_hash')}`, now "
            f"`{row.get('dataset_hash')}`",
            *_compare(pm, old, dataset),
        ]
    lines += [
        "", "## To approve it", "",
        "1. Download this artifact and copy `artifacts/production_models/` and "
        "`config/production_models.yaml` from it into the checkout (same paths).",
        "2. `uv run twm model check` must print the candidate's version.",
        "3. Review `git diff config/production_models.yaml`, then commit both files yourself. "
        "The scheduled job uses the new model from the next run on.",
        "", "Nothing was committed by the workflow; the approved model stays in use until then.",
    ]  # fmt: skip
    return "\n".join(lines) + "\n"

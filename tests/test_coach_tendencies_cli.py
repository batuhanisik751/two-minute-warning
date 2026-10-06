"""`twm coach tendencies` (feature #10): the value formats, the percentile cells (pace as
"faster than"), the wide season rows and the coach lookup. Synthetic frames:
tests/publish_coach_tendencies_synthetic.py."""

from __future__ import annotations

import polars as pl

from tests import publish_coach_tendencies_synthetic as pcs
from twm.modules.coach_tendencies import cli
from twm.modules.coach_tendencies.season import METRICS


def test_values_and_percentiles_read_as_on_the_site() -> None:
    assert cli.fmt("neutral_pass_rate", 0.6234) == "62%"
    assert cli.fmt("proe", 4.56) == "+4.6" and cli.fmt("proe", -2.0) == "-2.0"
    assert cli.fmt("neutral_sec_per_play", 27.04) == "27.0s" and cli.fmt("proe", None) == "-"
    assert cli.cell("shotgun_rate", 0.5, 71.4) == "50% (71)"
    assert cli.cell("neutral_sec_per_play", 26.0, 10.0) == "26.0s (90)"  # faster than 90%
    assert cli.cell("fourth_go_rate", 0.2, None) == "20% (-)"


def test_wide_rows_and_the_coach_lookup() -> None:
    w = cli.wide(pcs.season_frame())
    assert w.height == len(pcs.ROWS) and set(METRICS) <= set(w.columns)
    lines = cli.table_lines(w.filter(pl.col("season") == 2026), "coach", pl.col("coach_id"))
    assert len(lines) == 3 and lines[0].startswith("coach") and "PROE" in lines[0]
    names = pcs.NAMES
    assert cli.find_coach(names, "pat-example")["coach_id"].to_list() == ["pat_example"]
    assert cli.find_coach(names, "D'ARCY")["coach_id"].to_list() == ["renee_darcy"]
    assert cli.find_coach(names, "e").height == 4 and cli.find_coach(names, "zz").height == 0

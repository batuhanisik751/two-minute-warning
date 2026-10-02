"""Step I3a on the owner's real data (opt-in: `uv run pytest -m realdata`): for every module
the first and last backtest season are recomputed from the warehouse with the model used then
and must equal the stored rows the site serves (tolerance 1e-9; docs/timemachine.md). Reads
the warehouse, the pins and the predictions store read-only; writes only under tmp_path.
About 3 minutes."""

from __future__ import annotations

import pytest

from twm.timemachine import recompute as rc

pytestmark = pytest.mark.realdata


@pytest.fixture(scope="module")
def ctx(tmp_path_factory: pytest.TempPathFactory) -> rc.Context:
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip(f"no warehouse at {db}")
    return rc.Context(db=db, work=tmp_path_factory.mktemp("timemachine"),
                      season=settings().current_season, n=2, seed=1,
                      store=settings().path("predictions"))  # fmt: skip


@pytest.mark.parametrize("module", list(rc.MODULES))
def test_real_stored_lists_equal_a_fresh_recomputation(ctx: rc.Context, module: str):
    (res,) = rc.run(ctx, [module])
    c = res.comparison
    detail = f"{res.error or ''} {c.examples} {res.notes}"
    assert res.status == "REPRODUCED", detail
    assert c.rows > 0 and c.mismatches == 0 and c.max_diff <= 1e-9, detail
    assert len(res.seasons) == 2 and res.seasons[0] < res.seasons[1]  # first + last season

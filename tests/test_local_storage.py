"""scripts/local_storage.sh moves the heavy local folders (raw cache, dataset, models ...) out
of the checkout and leaves symlinks; idempotent. Run on a scratch copy with a stub `uv` (no
virtualenv is created, nothing is installed)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_storage.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_models_and_data_are_linked_out_and_rerunning_is_safe(tmp_path):
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "local_storage.sh")
    (root / "models" / "waiver_radar").mkdir(parents=True)
    (root / "models" / "waiver_radar" / "logit-x.joblib").write_bytes(b"model")
    (root / "data" / "raw").mkdir(parents=True)
    store = tmp_path / "store"
    (store / "venv").mkdir(parents=True)
    (root / ".venv").symlink_to(store / "venv")  # an existing link: the venv is left alone
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "uv").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "TWM_LOCAL_STORE": str(store)}
    for _ in range(2):  # the second run finds every link in place
        out = subprocess.run(["bash", str(root / "scripts" / "local_storage.sh")], env=env,
                             capture_output=True, text=True, check=True)  # fmt: skip
    assert (root / "models").is_symlink() and (root / "models").resolve() == store / "models"
    assert (store / "models" / "waiver_radar" / "logit-x.joblib").read_bytes() == b"model"
    assert (root / "data" / "raw").is_symlink() and (root / "data" / "waiver_radar").is_symlink()
    league = root / "data" / "league.duckdb"  # F2: linked before the first sync
    assert league.is_symlink() and Path(os.readlink(league)) == store / "league.duckdb"
    assert "ok     models ->" in out.stdout

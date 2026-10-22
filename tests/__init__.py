"""Tests run against the made-up account in tests/fixtures (rensim.toml there), never real game files.
Set before any test imports renaissance, since settings are read once at import."""
import contextlib
import io
import os
import shutil
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ["RENSIM_CONFIG"] = str(FIXTURES / "rensim.toml")
os.environ.setdefault("RUNTIME__DLTHUB_TELEMETRY", "false")

_TMP = tempfile.TemporaryDirectory()   # removed when the test run ends
_FIXTURE_DB: Path | None = None


def load(tables: dict[str, list[dict]], folder: Path) -> Path:
    """Load tables into folder/renaissance.duckdb (no views) with dlt working files kept in folder."""
    from renaissance.load import load_duckdb

    db = folder / "renaissance.duckdb"
    with contextlib.redirect_stdout(io.StringIO()):   # dlt prints a load summary
        load_duckdb(tables, db, pipelines_dir=folder / "dlt")
    return db


def fixture_db() -> Path:
    """The fixtures loaded and the dbt models built, once per test run (about 6 seconds).
    Open it with renaissance.load.connect (read-only); to change rows, copy it first (copy_db)."""
    global _FIXTURE_DB
    if _FIXTURE_DB is None:
        from renaissance.dbt_runner import dbt
        from renaissance.run import parse_all

        folder = Path(_TMP.name) / "fixture"
        folder.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            tables = parse_all()
        db = load(tables, folder)
        if not dbt(["--quiet", "run"], db, work=folder / "dbt"):
            raise RuntimeError("dbt run failed on the fixtures; run `rensim run` in tests/fixtures to see why")
        _FIXTURE_DB = db
    return _FIXTURE_DB


def copy_db(folder: Path) -> Path:
    """A private copy of the fixture database in folder, safe to write to. The file keeps its name,
    because the dbt views refer to their tables through it ("renaissance"."game"...)."""
    return Path(shutil.copy(fixture_db(), folder / "renaissance.duckdb"))

"""Run dbt against your database. The dbt project in renaissance/dbt/ turns the loaded tables into
the star schema (dimensions, facts, analysis views); `rensim run` builds it after every load. Use
this for any other dbt command:

    rensim dbt build                 # every model, then every test
    rensim dbt test                  # just the tests
    rensim dbt ls                    # list the models, sources and tests
    rensim dbt docs generate         # the docs site, written to .dbt/target/
    rensim dbt docs serve            # ...and browse it at http://localhost:8080

dbt keeps its working files (compiled SQL, logs, the docs site) in .dbt/ next to rensim.toml.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .settings import BASE

PROJECT = Path(__file__).resolve().parent / "dbt"
WORK = BASE / ".dbt"


def dbt(args: list[str], db_path: Path, work: Path = WORK) -> bool:
    """Run one dbt command (e.g. ["run"]) against the DuckDB file at db_path; True if it succeeded.
    In its own process, the same as typing `dbt ...`: dbt-duckdb keeps the database open for writing
    until the process ends, and the caller may want to read it straight after."""
    env = {
        **os.environ,
        "RENSIM_DUCKDB": str(db_path),          # read by profiles.yml
        "DBT_PROJECT_DIR": str(PROJECT),
        "DBT_PROFILES_DIR": str(PROJECT),
        "DBT_TARGET_PATH": str(work / "target"),
        "DBT_LOG_PATH": str(work / "logs"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    cli = "from dbt.cli.main import cli; cli(prog_name='dbt')"   # what the `dbt` command runs
    return subprocess.run([sys.executable, "-c", cli, *args], env=env).returncode == 0


def main(argv=None):
    from .run import DB
    return 0 if dbt(sys.argv[1:] if argv is None else argv, DB) else 1


if __name__ == "__main__":
    sys.exit(main())

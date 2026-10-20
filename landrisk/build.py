"""Run the dbt project against the local DuckDB file."""
from __future__ import annotations

import json
import os
from datetime import date

from .config import DB_PATH, DBT_DIR, PRODUCTION_LINKED, REVIEW_SKIP_FIELDS, REVIEW_THRESHOLD


def run_dbt(command: str = "build", select: str | None = None, as_of: date | None = None) -> bool:
    from dbt.cli.main import dbtRunner  # imported lazily: dbt is slow to import

    os.environ["LANDRISK_DB_ABS"] = str(DB_PATH)
    vars_ = {"review_threshold": REVIEW_THRESHOLD,
             "as_of_date": (as_of or date.today()).isoformat(),
             "production_linked": PRODUCTION_LINKED,
             "review_skip_fields": REVIEW_SKIP_FIELDS}
    args = [command, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
            "--vars", json.dumps(vars_), "--quiet"]
    if select:
        args += ["--select", select]
    result = dbtRunner().invoke(args)
    if not result.success:
        print(f"dbt {command} failed")
    return result.success

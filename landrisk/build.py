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
    global last_problems
    last_problems = _problems(result)
    if not result.success:
        print(f"dbt {command} failed: " + ("; ".join(last_problems) or "see dbt/logs/dbt.log"))
    return result.success


# Plain-language failures from the most recent run, for the API to report.
last_problems: list[str] = []


def _problems(result) -> list[str]:
    out = []
    for r in getattr(result.result, "results", None) or []:
        status = str(getattr(r, "status", "")).lower()
        if status in ("error", "fail", "runtime error"):
            name = getattr(getattr(r, "node", None), "name", "?")
            failures = getattr(r, "failures", None)
            detail = f"{failures} failing row{'s' if failures != 1 else ''}" if failures else (getattr(r, "message", "") or status)
            out.append(f"{name} ({detail})")
    if not out and getattr(result, "exception", None):
        out.append(str(result.exception))
    return out

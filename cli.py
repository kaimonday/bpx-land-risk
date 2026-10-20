"""Command line entry point. Run `python cli.py --help`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="bpx land risk pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="Offline end-to-end run: sample RRC data, synthetic leases, mock extraction, dbt build")

    s = sub.add_parser("sample-rrc", help="Write and load a small synthetic RRC dump")

    s = sub.add_parser("ingest-rrc", help="Load the real RRC PDQ dump, filtered to one operator")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", type=Path, help="Path to PDQ_DSV.zip")
    src.add_argument("--dir", type=Path, help="Folder of extracted .dsv files")
    s.add_argument("--pattern", help="Regex for operator names (default: RRC_OPERATOR_PATTERN from .env)")
    s.add_argument("--since", default="201801", help="First production month to keep, YYYYMM")

    s = sub.add_parser("generate", help="Generate synthetic lease PDFs tied to the loaded RRC leases")
    s.add_argument("--count", type=int, default=18)
    s.add_argument("--seed", type=int, default=42)

    s = sub.add_parser("extract", help="Extract lease fields from the PDFs")
    s.add_argument("--provider", choices=["groq", "mock"], default="groq")
    s.add_argument("--only", nargs="*", help="Document ids, e.g. LSE-0003")

    sub.add_parser("evaluate", help="Score extractions against the synthetic answer key")
    sub.add_parser("build", help="Run dbt build (models + tests)")

    s = sub.add_parser("serve", help="Start the API on http://localhost:8000")
    s.add_argument("--port", type=int, default=8000)

    args = p.parse_args(argv)

    if args.cmd == "demo":
        from landrisk import sample_rrc
        from landrisk.build import run_dbt
        from landrisk.evaluate import evaluate
        from landrisk.extract import extract_all
        from landrisk.generate_leases import generate
        from landrisk.ingest_rrc import Source, ingest
        ingest(Source(dir_path=sample_rrc.write_sample()), pattern="^SAMPLE OPERATING")
        generate()
        extract_all(provider="mock")
        ok = run_dbt("build")
        evaluate()
        return 0 if ok else 1
    if args.cmd == "sample-rrc":
        from landrisk import sample_rrc
        from landrisk.ingest_rrc import Source, ingest
        ingest(Source(dir_path=sample_rrc.write_sample()), pattern="^SAMPLE OPERATING")
    elif args.cmd == "ingest-rrc":
        from landrisk.config import RRC_OPERATOR_PATTERN
        from landrisk.ingest_rrc import Source, ingest
        ingest(Source(zip_path=args.zip, dir_path=args.dir),
               pattern=args.pattern or RRC_OPERATOR_PATTERN, since=args.since)
    elif args.cmd == "generate":
        from landrisk.generate_leases import generate
        generate(count=args.count, seed=args.seed)
    elif args.cmd == "extract":
        from landrisk.extract import extract_all
        extract_all(provider=args.provider, only=args.only)
    elif args.cmd == "evaluate":
        from landrisk.evaluate import evaluate
        evaluate()
    elif args.cmd == "build":
        from landrisk.build import run_dbt
        return 0 if run_dbt("build") else 1
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("api.main:app", host="127.0.0.1", port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

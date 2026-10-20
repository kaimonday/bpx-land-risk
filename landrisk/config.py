"""Settings loaded from the environment (.env at the repo root, never committed)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
LEASE_PDF_DIR = DATA_DIR / "leases"
SAMPLE_PDQ_DIR = DATA_DIR / "sample_pdq"
DBT_DIR = ROOT / "dbt"


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


DB_PATH = _path(os.getenv("LANDRISK_DB", "data/landrisk.duckdb"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Longer documents are split into parts of about this many tokens. The default
# fits Groq's free tier (8,000 tokens a minute, counting the prompt and reply).
GROQ_MAX_INPUT_TOKENS = int(os.getenv("GROQ_MAX_INPUT_TOKENS", "4000"))
REVIEW_THRESHOLD = float(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "0.85"))
RRC_OPERATOR_PATTERN = os.getenv("RRC_OPERATOR_PATTERN", "^BPX")

# Lessee printed on the synthetic lease documents. Deliberately fictional:
# the documents are demo data and must never read as real bpx records.
SYNTHETIC_LESSEE = os.getenv("SYNTHETIC_LESSEE", "Permian Demo Operating LLC")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LEASE_PDF_DIR.mkdir(parents=True, exist_ok=True)

"""DuckDB connection helpers and the raw-layer schema.

Raw tables are written by the Python pipeline and the API; dbt reads them as
sources and builds everything downstream.
"""
from __future__ import annotations

import duckdb

from .config import DB_PATH, ensure_dirs

RAW_DDL = """
create schema if not exists raw;

create table if not exists raw.rrc_operators (
    operator_no varchar, operator_name varchar, loaded_at timestamp
);

create table if not exists raw.rrc_lease_cycle (
    oil_gas_code varchar, district_no varchar, lease_no varchar,
    cycle_year_month varchar, operator_no varchar, operator_name varchar,
    lease_name varchar, field_name varchar,
    lease_oil_prod_vol varchar, lease_gas_prod_vol varchar,
    lease_cond_prod_vol varchar, lease_csgd_prod_vol varchar,
    loaded_at timestamp
);

create table if not exists raw.rrc_well_completion (
    oil_gas_code varchar, district_no varchar, lease_no varchar, well_no varchar,
    api_county_code varchar, api_unique_no varchar, county_name varchar,
    well_shutin_dt varchar, wellbore_shutin_dt varchar, loaded_at timestamp
);

-- Land-system mapping: which RRC (regulatory) leases each tract feeds, and the
-- tract's allocation share of that RRC lease's production.
create table if not exists raw.tract_rrc_xref (
    tract_id varchar, oil_gas_code varchar, district_no varchar, lease_no varchar,
    allocation_pct double, loaded_at timestamp
);

create table if not exists raw.lease_documents (
    document_id varchar, file_name varchar, page_count integer,
    text_chars integer, loaded_at timestamp
);

-- One row per field per extraction run (long form, easy to pivot in dbt).
create table if not exists raw.lease_extraction_fields (
    extraction_id varchar, document_id varchar, provider varchar, model varchar,
    field_name varchar, value varchar, llm_confidence double, confidence double,
    evidence varchar, validation_notes varchar, extracted_at timestamp,
    alternatives varchar  -- JSON list of conflicting readings: value, evidence, confidence, part
);
-- Databases created before the column existed.
alter table raw.lease_extraction_fields add column if not exists alternatives varchar;

-- Human decisions from the review queue.
create table if not exists raw.lease_reviews (
    review_id varchar, document_id varchar, field_name varchar, action varchar,
    corrected_value varchar, reviewer varchar, note varchar, reviewed_at timestamp
);

-- Answer key for synthetic documents, used only by the evaluator.
create table if not exists raw.lease_ground_truth (
    document_id varchar, field_name varchar, value varchar, evidence varchar,
    scenario varchar
);
"""


def connect(must_exist: bool = False) -> duckdb.DuckDBPyConnection:
    # Always open read-write: DuckDB refuses a second connection in the same
    # process with a different configuration, and dbt (run in-process by the
    # API and the demo) holds a read-write one.
    ensure_dirs()
    if must_exist and not DB_PATH.exists():
        raise FileNotFoundError(f"No database at {DB_PATH}. Run `python cli.py demo` first.")
    return duckdb.connect(str(DB_PATH))


def init_raw() -> None:
    with connect() as con:
        con.execute(RAW_DDL)

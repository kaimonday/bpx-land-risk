"""The lease fields we extract. One definition shared by the generator, the
extractor, the evaluator and (via dbt vars) the warehouse models."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # text | date | int | decimal | bool
    required: bool
    description: str


FIELDS: list[Field] = [
    Field("lessor", "text", True, "Mineral owner(s) granting the lease, as named in the document"),
    Field("lessee", "text", True, "Party receiving the lease"),
    Field("effective_date", "date", True, "Effective date of the lease, YYYY-MM-DD (not the signature or recording date if different)"),
    Field("primary_term_months", "int", True, "Primary term length in months (5 years = 60)"),
    Field("royalty_rate", "decimal", True, "Lessor royalty as a decimal (3/16 = 0.1875). If a rider or exhibit amends the royalty, use the amended rate"),
    Field("county", "text", True, "County where the leased land lies, without the word County"),
    Field("state", "text", True, "Two-letter state code"),
    Field("tract_id", "text", True, "Tract identifier from the land description, e.g. T-0042"),
    Field("gross_acres", "decimal", True, "Gross acres covered by the lease"),
    Field("depth_limit_ft", "int", False, "Deepest depth in feet covered by the lease if limited; null if all depths"),
    Field("pugh_clause", "bool", True,
          "True only if the lease itself ends, after the primary term, as to acreage or depths not producing or "
          "not in a pooled unit (a Pugh clause: e.g. 'this lease shall terminate as to all lands not included "
          "within a producing unit'). False if it has no such clause. NOT a Pugh clause: a voluntary surrender "
          "clause letting the lessee release acreage whenever it chooses ('Lessee may at any time ... deliver a "
          "release'), and a shut-in clause that keeps the whole lease alive when wells are shut in. If the lease "
          "has neither, the answer is false, not null"),
    Field("shut_in_payment_per_acre", "decimal", False, "Shut-in royalty payment in dollars per acre; null if none"),
    Field("shut_in_grace_days", "int", False, "Days after production stops within which the shut-in payment must be made; null if none"),
    Field("continuous_drilling_days", "int", False, "Maximum days allowed between wells under a continuous drilling clause; null if none"),
]

FIELD_NAMES = [f.name for f in FIELDS]
FIELD_BY_NAME = {f.name: f for f in FIELDS}

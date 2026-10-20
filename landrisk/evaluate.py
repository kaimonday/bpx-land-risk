"""Score the latest extraction for each synthetic document against its answer key.

The two numbers that matter for an abstraction tool:
  - auto-accept precision: of the fields confident enough to skip review, how
    many were right (this is the risk you're taking by not reviewing them)
  - review load: the share of fields a person still has to look at
"""
from __future__ import annotations

from .config import REVIEW_THRESHOLD
from .db import connect
from .extract import normalize
from .fields import FIELD_BY_NAME, FIELD_NAMES


def _equal(kind: str, a: str | None, b: str | None) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    if a in (None, "") or b in (None, ""):
        return False
    if kind in ("decimal", "int"):
        return abs(float(a) - float(b)) < 1e-6
    if kind == "text":
        return a.strip().lower() == b.strip().lower()
    return a == b


def evaluate() -> dict:
    with connect(must_exist=True) as con:
        rows = con.execute("""
            with latest as (
                select document_id, arg_max(extraction_id, extracted_at) as extraction_id
                from raw.lease_extraction_fields group by 1
            )
            select e.document_id, e.field_name, e.value, e.confidence, g.value as truth
            from raw.lease_extraction_fields e
            join latest l using (document_id, extraction_id)
            join raw.lease_ground_truth g on g.document_id = e.document_id and g.field_name = e.field_name
        """).fetchall()
    if not rows:
        print("Nothing to evaluate: run generate and extract first.")
        return {}

    per_field = {n: [0, 0] for n in FIELD_NAMES}
    auto_total = auto_right = reviewed = 0
    for _doc, name, value, conf, truth in rows:
        kind = FIELD_BY_NAME[name].kind
        truth_norm, _ = normalize(kind, truth)
        right = _equal(kind, value, truth_norm)
        per_field[name][0] += right
        per_field[name][1] += 1
        if conf >= REVIEW_THRESHOLD:
            auto_total += 1
            auto_right += right
        else:
            reviewed += 1

    total = len(rows)
    print(f"\n{'field':28} accuracy")
    for name, (right, n) in per_field.items():
        print(f"{name:28} {right / n:6.0%}  ({right}/{n})")
    stats = {
        "fields": total,
        "raw_accuracy": sum(r for r, _ in per_field.values()) / total,
        "auto_accept_precision": auto_right / auto_total if auto_total else None,
        "review_load": reviewed / total,
    }
    print(f"\nRaw accuracy: {stats['raw_accuracy']:.1%}")
    if stats["auto_accept_precision"] is not None:
        print(f"Auto-accept precision (conf >= {REVIEW_THRESHOLD}): {stats['auto_accept_precision']:.1%}")
    print(f"Review load: {stats['review_load']:.1%} of fields")
    return stats

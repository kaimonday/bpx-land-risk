-- Fields a person still needs to check, worst confidence first.
select
    document_id,
    field_name,
    extracted_value,
    confidence,
    llm_confidence,
    evidence,
    validation_notes
from {{ ref('int_lease_fields_resolved') }}
where field_status = 'needs_review'
order by confidence, document_id, field_name

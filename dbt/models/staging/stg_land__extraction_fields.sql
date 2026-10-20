-- Fields from the most recent extraction run of each document.
with runs as (
    select * from {{ source('raw', 'lease_extraction_fields') }}
),

latest as (
    select document_id, arg_max(extraction_id, extracted_at) as extraction_id
    from runs
    group by 1
)

select
    runs.document_id,
    runs.extraction_id,
    runs.provider,
    runs.model,
    runs.field_name,
    runs.value as extracted_value,
    runs.llm_confidence,
    runs.confidence,
    runs.evidence,
    runs.validation_notes,
    runs.alternatives,
    runs.extracted_at
from runs
join latest using (document_id, extraction_id)

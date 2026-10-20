-- Every extracted field with the human decision applied.
-- final_value: a correction wins, otherwise the extracted value.
-- field_status: reviewed | auto_accepted (confident enough to skip review) | needs_review
with fields as (
    select * from {{ ref('stg_land__extraction_fields') }}
),

reviews as (
    select * from {{ ref('stg_land__reviews') }}
)

select
    fields.document_id,
    fields.field_name,
    fields.extracted_value,
    fields.confidence,
    fields.llm_confidence,
    fields.evidence,
    fields.validation_notes,
    reviews.action as review_action,
    reviews.corrected_value,
    reviews.reviewer,
    reviews.reviewed_at,
    case when reviews.action = 'correct' then reviews.corrected_value
         else fields.extracted_value end as final_value,
    case when reviews.action is not null then 'reviewed'
         when fields.confidence >= {{ var('review_threshold') }} then 'auto_accepted'
         else 'needs_review' end as field_status
from fields
left join reviews using (document_id, field_name)

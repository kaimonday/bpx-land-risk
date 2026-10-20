-- The most recent human decision for each document field.
select
    document_id,
    field_name,
    arg_max(action, reviewed_at) as action,
    arg_max(corrected_value, reviewed_at) as corrected_value,
    arg_max(reviewer, reviewed_at) as reviewer,
    arg_max(note, reviewed_at) as note,
    max(reviewed_at) as reviewed_at
from {{ source('raw', 'lease_reviews') }}
group by 1, 2

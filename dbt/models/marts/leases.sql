-- One row per lease document: the resolved fields pivoted wide and typed.
{%- set fields = var('lease_fields') %}

with resolved as (
    select * from {{ ref('int_lease_fields_resolved') }}
),

wide as (
    select
        document_id,
        {%- for f in fields %}
        max(case when field_name = '{{ f.name }}' then final_value end) as {{ f.name }}_raw,
        max(case when field_name = '{{ f.name }}' then confidence end) as {{ f.name }}_confidence,
        {%- endfor %}
        count(*) filter (where field_status = 'needs_review') as fields_needing_review,
        count(*) filter (where field_status = 'reviewed') as fields_reviewed,
        min(confidence) as min_confidence
    from resolved
    group by 1
),

typed as (
    select
        document_id,
        {%- for f in fields %}
        {%- if f.kind == 'date' %}
        try_cast({{ f.name }}_raw as date) as {{ f.name }},
        {%- elif f.kind == 'int' %}
        try_cast({{ f.name }}_raw as integer) as {{ f.name }},
        {%- elif f.kind == 'decimal' %}
        try_cast({{ f.name }}_raw as double) as {{ f.name }},
        {%- elif f.kind == 'bool' %}
        {{ f.name }}_raw = 'true' as {{ f.name }},
        {%- else %}
        {{ f.name }}_raw as {{ f.name }},
        {%- endif %}
        {{ f.name }}_confidence,
        {%- endfor %}
        fields_needing_review,
        fields_reviewed,
        min_confidence
    from wide
)

select
    typed.*,
    cast(effective_date + to_months(primary_term_months) as date) as primary_term_end,
    shut_in_payment_per_acre is not null and shut_in_grace_days is not null as has_shut_in_clause,
    case when fields_needing_review > 0 then 'needs_review'
         when fields_reviewed > 0 then 'reviewed'
         else 'auto_accepted' end as extraction_status,
    docs.file_name
from typed
left join {{ source('raw', 'lease_documents') }} as docs using (document_id)

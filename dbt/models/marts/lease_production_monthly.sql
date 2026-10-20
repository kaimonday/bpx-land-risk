-- Production attributed to each lease: tract -> RRC lease(s) -> monthly volumes
-- times the tract's allocation share.
with leases as (
    select document_id, tract_id from {{ ref('leases') }}
),

xref as (
    select * from {{ ref('stg_land__tract_xref') }}
),

production as (
    select * from {{ ref('stg_rrc__production_monthly') }}
)

select
    leases.document_id,
    production.production_month,
    sum(production.liquids_bbl * xref.allocation_pct) as liquids_bbl,
    sum(production.gas_mcf * xref.allocation_pct) as gas_mcf,
    sum(production.boe * xref.allocation_pct) as boe,
    string_agg(distinct production.rrc_lease_key, ', ') as rrc_lease_keys
from leases
-- Real leases have no tract id and are linked by document id instead.
join xref on xref.tract_id in (leases.tract_id, leases.document_id)
join production using (rrc_lease_key)
group by 1, 2

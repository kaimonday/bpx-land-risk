-- The tracts mapped to one RRC lease can't claim more than all of its production.
select rrc_lease_key, sum(allocation_pct) as total_allocation
from {{ ref('stg_land__tract_xref') }}
group by 1
having sum(allocation_pct) > 1.0001

select
    tract_id,
    oil_gas_code || '-' || district_no || '-' || lease_no as rrc_lease_key,
    allocation_pct
from {{ source('raw', 'tract_rrc_xref') }}

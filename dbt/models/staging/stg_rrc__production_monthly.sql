-- Monthly production per RRC lease, typed. Oil + condensate = liquids (bbl);
-- gas-well gas + casinghead gas = gas (mcf); BOE uses 6 mcf = 1 bbl.
with src as (
    select * from {{ source('raw', 'rrc_lease_cycle') }}
),

typed as (
    select
        oil_gas_code,
        district_no,
        lease_no,
        oil_gas_code || '-' || district_no || '-' || lease_no as rrc_lease_key,
        make_date(cast(left(cycle_year_month, 4) as integer),
                  cast(right(cycle_year_month, 2) as integer), 1) as production_month,
        operator_no,
        operator_name,
        lease_name,
        field_name,
        coalesce(try_cast(lease_oil_prod_vol as bigint), 0)
            + coalesce(try_cast(lease_cond_prod_vol as bigint), 0) as liquids_bbl,
        coalesce(try_cast(lease_gas_prod_vol as bigint), 0)
            + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0) as gas_mcf,
        -- Loads from before the flag was kept count as filed.
        coalesce(prod_report_filed_flag, 'Y') = 'Y' as report_filed
    from src
)

select
    *,
    liquids_bbl + gas_mcf / 6.0 as boe
from typed

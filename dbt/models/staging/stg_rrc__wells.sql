-- Wells on the loaded RRC leases. Texas API numbers are 42 (state) + county + unique number.
select
    oil_gas_code || '-' || district_no || '-' || lease_no as rrc_lease_key,
    oil_gas_code,
    district_no,
    lease_no,
    well_no,
    '42-' || api_county_code || '-' || api_unique_no as api_number,
    -- Title case each word (DuckDB has no initcap): "LA SALLE" -> "La Salle"
    array_to_string(list_transform(string_split(lower(county_name), ' '),
                                   w -> upper(left(w, 1)) || substr(w, 2)), ' ') as county,
    nullif(well_shutin_dt, '') as well_shutin_yyyymm
from {{ source('raw', 'rrc_well_completion') }}

-- Wells on the loaded RRC leases. Texas API numbers are 42 (state) + county + unique number.
-- One wellbore can report to several RRC leases (an oil and a gas lease, or upper and
-- lower completions), so the API number alone isn't a key. The dump also lists some
-- wells twice, once with a shut-in date and once with the 000000 placeholder for none;
-- keep one row per well with its latest real shut-in date.
select
    oil_gas_code || '-' || district_no || '-' || lease_no as rrc_lease_key,
    oil_gas_code,
    district_no,
    lease_no,
    well_no,
    '42-' || api_county_code || '-' || api_unique_no as api_number,
    -- Title case each word (DuckDB has no initcap): "LA SALLE" -> "La Salle"
    array_to_string(list_transform(string_split(lower(any_value(county_name)), ' '),
                                   w -> upper(left(w, 1)) || substr(w, 2)), ' ') as county,
    max(nullif(nullif(well_shutin_dt, ''), '000000')) as well_shutin_yyyymm
from {{ source('raw', 'rrc_well_completion') }}
group by oil_gas_code, district_no, lease_no, well_no, api_county_code, api_unique_no

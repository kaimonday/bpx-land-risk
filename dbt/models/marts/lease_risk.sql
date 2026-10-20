-- One row per lease with its risk flags, severity, next deadline and a
-- recommended action.
--
-- Production gaps are measured against the latest month RRC has reported
-- (data lags 2-3 months), not against today, so the reporting lag alone never
-- raises a flag.
{%- set as_of = "cast('" ~ var('as_of_date') ~ "' as date)" %}
{#- A dataset not linked to RRC production can't tell "never produced" from
    "unknown", so the production rules are switched off for it. #}
{%- set linked = 'true' if var('production_linked') else 'false' %}

with leases as (
    select * from {{ ref('leases') }}
),

production as (
    select
        document_id,
        min(production_month) filter (where boe > 0) as first_production_month,
        max(production_month) filter (where boe > 0) as last_production_month,
        sum(boe) as cumulative_boe
    from {{ ref('lease_production_monthly') }}
    group by 1
),

latest as (
    select max(production_month) as latest_reported_month
    from {{ ref('stg_rrc__production_monthly') }}
),

xref as (
    select tract_id, count(*) as rrc_lease_count
    from {{ ref('stg_land__tract_xref') }}
    group by 1
),

base as (
    select
        leases.document_id,
        leases.lessor,
        leases.county,
        leases.tract_id,
        leases.gross_acres,
        leases.royalty_rate,
        leases.effective_date,
        leases.primary_term_months,
        leases.primary_term_end,
        leases.has_shut_in_clause,
        leases.shut_in_payment_per_acre,
        leases.shut_in_grace_days,
        leases.pugh_clause,
        leases.extraction_status,
        leases.fields_needing_review,
        coalesce(xref.rrc_lease_count, 0) as rrc_lease_count,
        production.first_production_month,
        production.last_production_month,
        coalesce(production.cumulative_boe, 0) as cumulative_boe,
        latest.latest_reported_month,
        {{ as_of }} as as_of_date,
        {{ as_of }} < leases.primary_term_end as in_primary_term,
        production.last_production_month is not null as has_produced,
        date_diff('month', production.last_production_month, latest.latest_reported_month)
            as months_since_production,
        date_diff('day', {{ as_of }}, leases.primary_term_end) as days_to_term_end,
        -- Production is treated as ceasing at the start of the month after the last producing month.
        case when leases.has_shut_in_clause
              and production.last_production_month < latest.latest_reported_month
             then cast(production.last_production_month + interval 1 month
                       + to_days(leases.shut_in_grace_days) as date)
        end as shut_in_due_date,
        leases.shut_in_payment_per_acre * leases.gross_acres as shut_in_payment_amount
    from leases
    cross join latest
    left join production using (document_id)
    left join xref using (tract_id)
),

flagged as (
    select
        *,
        {{ linked }} and not in_primary_term and not has_produced as f_expired_undeveloped,
        {{ linked }} and in_primary_term and not has_produced
            and days_to_term_end <= {{ var('expiring_window_days') }} as f_expiring_undeveloped,
        {{ linked }} and not in_primary_term and has_produced
            and months_since_production >= {{ var('hbp_gap_months') }} as f_hbp_production_gap,
        {{ linked }} and not in_primary_term and shut_in_due_date is not null
            and shut_in_due_date < as_of_date as f_shut_in_overdue,
        {{ linked }} and not in_primary_term and shut_in_due_date is not null
            and shut_in_due_date between as_of_date
                and as_of_date + to_days({{ var('shut_in_warning_days') }}) as f_shut_in_due_soon,
        not {{ linked }} and not coalesce(in_primary_term, false) as f_production_not_linked,
        fields_needing_review > 0 as f_unverified
    from base
)

select
    document_id,
    lessor,
    county,
    tract_id,
    gross_acres,
    royalty_rate,
    effective_date,
    primary_term_months,
    primary_term_end,
    case when in_primary_term then 'primary_term'
         when not {{ linked }} then 'production_not_linked'
         when has_produced then 'held_by_production'
         else 'past_primary_term' end as lease_status,
    has_produced,
    first_production_month,
    last_production_month,
    months_since_production,
    latest_reported_month,
    cumulative_boe,
    rrc_lease_count,
    has_shut_in_clause,
    shut_in_due_date,
    shut_in_payment_amount,
    pugh_clause,
    extraction_status,
    fields_needing_review,
    as_of_date,
    list_filter([
        case when f_shut_in_overdue then 'shut_in_payment_overdue' end,
        case when f_expired_undeveloped then 'expired_undeveloped' end,
        case when f_hbp_production_gap then 'hbp_production_gap' end,
        case when f_expiring_undeveloped then 'expiring_undeveloped' end,
        case when f_shut_in_due_soon then 'shut_in_payment_due_soon' end,
        case when f_production_not_linked then 'production_not_linked' end,
        case when f_unverified then 'unverified_extraction' end
    ], x -> x is not null) as risk_flags,
    case
        when f_shut_in_overdue or f_expired_undeveloped then 'high'
        when f_hbp_production_gap and (months_since_production >= 6 or not has_shut_in_clause) then 'high'
        when f_expiring_undeveloped and days_to_term_end <= 90 then 'high'
        when f_hbp_production_gap or f_expiring_undeveloped or f_shut_in_due_soon then 'medium'
        when f_unverified or f_production_not_linked then 'low'
        else 'none'
    end as severity,
    case
        when f_shut_in_overdue or f_expired_undeveloped then 3
        when f_hbp_production_gap and (months_since_production >= 6 or not has_shut_in_clause) then 3
        when f_expiring_undeveloped and days_to_term_end <= 90 then 3
        when f_hbp_production_gap or f_expiring_undeveloped or f_shut_in_due_soon then 2
        when f_unverified or f_production_not_linked then 1
        else 0
    end as severity_rank,
    case
        when f_shut_in_overdue then
            'Shut-in payment was due ' || strftime(shut_in_due_date, '%b %d, %Y')
            || '. Confirm it was paid or that production resumed; otherwise the lease may have terminated.'
        when f_expired_undeveloped then
            'Primary term ended ' || strftime(primary_term_end, '%b %d, %Y')
            || ' with no production on record. Confirm termination and whether a release is owed.'
        when f_hbp_production_gap then
            'No reported production for ' || months_since_production
            || ' months. Check well status and whether a shut-in payment or other savings clause applies.'
        when f_expiring_undeveloped then
            'Primary term ends ' || strftime(primary_term_end, '%b %d, %Y') || ' (' || days_to_term_end
            || ' days). Commence a well, pool the tract, or negotiate an extension.'
        when f_shut_in_due_soon then
            'Shut-in payment of $' || printf('%,.2f', shut_in_payment_amount) || ' due '
            || strftime(shut_in_due_date, '%b %d, %Y') || '.'
        when f_unverified then
            fields_needing_review || ' extracted field(s) still need review before this record is trusted.'
        when f_production_not_linked then
            coalesce('Primary term ended ' || strftime(primary_term_end, '%b %d, %Y') || '. ', 'Primary term end unknown. ')
            || 'Link this lease to its RRC lease to see whether production holds it.'
        else 'No action needed.'
    end as recommended_action,
    least(
        case when in_primary_term and not has_produced then primary_term_end end,
        case when not in_primary_term then shut_in_due_date end
    ) as next_deadline
from flagged

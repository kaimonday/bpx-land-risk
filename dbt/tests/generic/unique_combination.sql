{% test unique_combination(model, columns) %}
-- Fails for any combination of the given columns that appears more than once.
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

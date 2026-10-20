{% test value_between(model, column_name, min_value=none, max_value=none) %}
-- Fails for non-null values outside [min_value, max_value].
select *
from {{ model }}
where {{ column_name }} is not null
  and (
      {% if min_value is not none %} {{ column_name }} < {{ min_value }} {% else %} false {% endif %}
      or
      {% if max_value is not none %} {{ column_name }} > {{ max_value }} {% else %} false {% endif %}
  )
{% endtest %}

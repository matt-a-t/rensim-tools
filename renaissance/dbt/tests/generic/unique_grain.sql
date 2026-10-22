-- A fact is unique at its grain: no two rows share the columns that say what one row means.
-- Like dbt's `unique`, but over several columns. A test returns the rows that break the rule, so
-- it passes when this returns nothing. GROUP BY puts NULLs together, so a NULL grain column
-- (claim_id on prices from turn reports) still counts as one value.
{% test unique_grain(model, columns) %}
SELECT {{ columns | join(', ') }}, COUNT(*) AS n_rows
FROM {{ model }}
GROUP BY {{ columns | join(', ') }}
HAVING COUNT(*) > 1
{% endtest %}

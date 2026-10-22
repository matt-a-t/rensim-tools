WITH monthly AS (    -- MAX: one holder per role per month, even if two reports overlap
    SELECT location_key AS place_key, role, SUBSTR(game_date, 1, 7) AS turn_month, MAX(holder) AS holder
    FROM {{ source('game', 'turn_location_officials') }}
    GROUP BY location_key, role, SUBSTR(game_date, 1, 7)
), flagged AS (
    SELECT place_key, role, turn_month, holder,
           CASE WHEN LAG(turn_month) OVER w_holder IS NULL
                  OR COALESCE(holder, '') <> COALESCE(LAG(holder) OVER w_holder, '') THEN 1 ELSE 0 END AS starts_version
    FROM monthly
    WINDOW w_holder AS (PARTITION BY place_key, role ORDER BY turn_month)
), numbered AS (
    SELECT place_key, role, turn_month, holder,
           SUM(starts_version) OVER (PARTITION BY place_key, role ORDER BY turn_month ROWS UNBOUNDED PRECEDING) AS version
    FROM flagged
), versions AS (
    SELECT place_key, role, version, holder, MIN(turn_month) AS valid_from, MAX(turn_month) AS last_seen
    FROM numbered
    GROUP BY place_key, role, version, holder
), ranged AS (
    SELECT place_key, role, version, holder, valid_from,
           LEAD(valid_from) OVER (PARTITION BY place_key, role ORDER BY version) AS valid_to, last_seen
    FROM versions
)
SELECT place_key, role, version, holder, valid_from, valid_to, last_seen,
       CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END AS is_current
FROM ranged

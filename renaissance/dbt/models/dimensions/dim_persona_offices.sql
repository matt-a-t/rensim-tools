WITH monthly AS (    -- MAX: one office per persona per month, even if two reports overlap
    SELECT persona_id, SUBSTR(game_date, 1, 7) AS turn_month, MAX(office) AS office
    FROM {{ source('game', 'turn_persona_status') }}
    GROUP BY persona_id, SUBSTR(game_date, 1, 7)
), flagged AS (
    SELECT persona_id, turn_month, office,
           CASE WHEN LAG(turn_month) OVER w_office IS NULL
                  OR COALESCE(office, '') <> COALESCE(LAG(office) OVER w_office, '') THEN 1 ELSE 0 END AS starts_version
    FROM monthly
    WINDOW w_office AS (PARTITION BY persona_id ORDER BY turn_month)
), numbered AS (
    SELECT persona_id, turn_month, office,
           SUM(starts_version) OVER (PARTITION BY persona_id ORDER BY turn_month ROWS UNBOUNDED PRECEDING) AS version
    FROM flagged
), versions AS (
    SELECT persona_id, version, office, MIN(turn_month) AS valid_from, MAX(turn_month) AS last_seen
    FROM numbered
    GROUP BY persona_id, version, office
), ranged AS (
    SELECT persona_id, version, office, valid_from,
           LEAD(valid_from) OVER (PARTITION BY persona_id ORDER BY version) AS valid_to, last_seen
    FROM versions
)
SELECT persona_id, version, office, valid_from, valid_to, last_seen,
       CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END AS is_current
FROM ranged

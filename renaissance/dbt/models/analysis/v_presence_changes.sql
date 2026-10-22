WITH p AS (
    SELECT t.turn_no, f.account, f.player_family, f.game_date, f.location_key, pl.name AS location,
           f.inside_or_outside, f.persona_id, d.given_name, d.family, f.troops
    FROM {{ ref('fact_presence') }} f
    JOIN {{ ref('v_turns') }} t ON t.source_id = f.source_id
    LEFT JOIN {{ source('game', 'dim_personae') }} d ON d.persona_id = f.persona_id
    LEFT JOIN {{ source('game', 'dim_places') }} pl ON pl.place_key = f.location_key
)
SELECT cur.game_date, cur.location, 'arrived' AS change, cur.persona_id, cur.given_name,
       cur.family, cur.troops, cur.inside_or_outside
FROM p cur
WHERE cur.turn_no > 1
  AND NOT EXISTS (SELECT 1 FROM p prev
                  WHERE prev.account = cur.account AND prev.player_family = cur.player_family AND prev.turn_no = cur.turn_no - 1
                    AND prev.location_key = cur.location_key AND prev.persona_id = cur.persona_id
                    AND prev.inside_or_outside = cur.inside_or_outside)
UNION ALL
SELECT nxt_t.game_date, prev.location, 'left' AS change, prev.persona_id, prev.given_name,
       prev.family, prev.troops, prev.inside_or_outside
FROM p prev
JOIN {{ ref('v_turns') }} nxt_t ON nxt_t.account = prev.account AND nxt_t.player_family = prev.player_family AND nxt_t.turn_no = prev.turn_no + 1
WHERE NOT EXISTS (SELECT 1 FROM p cur
                  WHERE cur.account = prev.account AND cur.player_family = prev.player_family AND cur.turn_no = prev.turn_no + 1
                    AND cur.location_key = prev.location_key AND cur.persona_id = prev.persona_id
                    AND cur.inside_or_outside = prev.inside_or_outside)

SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       location_key, inside_or_outside, persona_id, family_key, troops, ships
FROM {{ source('game', 'turn_presence') }}

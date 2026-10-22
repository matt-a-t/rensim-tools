SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       place, place_key, family_key, is_set_up, lat, lon, miles_from_home
FROM {{ source('game', 'turn_lookups') }}

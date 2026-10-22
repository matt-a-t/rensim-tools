SELECT source_id, NULL AS claim_id, location_key AS place_key, SUBSTR(game_date, 1, 7) AS turn_month,
       game_date AS as_of, side, item, price, quantity, bq
FROM {{ source('game', 'turn_market') }}
UNION ALL
SELECT source_id, NULL, city_key, SUBSTR(as_of, 1, 7), as_of, side, item, price, quantity, bq
FROM {{ source('game', 'ref_city_market') }}
UNION ALL
SELECT source_id, claim_id, location_key, SUBSTR(game_date, 1, 7), game_date, side, item, price, NULL, bq
FROM {{ source('game', 'claim_market') }}

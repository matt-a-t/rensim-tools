SELECT turn_month,
       CAST(SUBSTR(turn_month, 1, 4) AS INTEGER) AS year,
       CAST(SUBSTR(turn_month, 6, 2) AS INTEGER) AS month,
       CAST(SUBSTR(turn_month, 1, 4) AS INTEGER) * 12 + CAST(SUBSTR(turn_month, 6, 2) AS INTEGER) - 1 AS month_index
FROM (SELECT SUBSTR(game_date, 1, 7) AS turn_month FROM {{ source('game', 'sources') }} WHERE LENGTH(game_date) >= 7
      UNION SELECT game_month FROM {{ source('game', 'orders_sent') }}
      UNION SELECT SUBSTR(game_date, 1, 7) FROM {{ source('game', 'claims') }} WHERE LENGTH(game_date) >= 7
      UNION SELECT SUBSTR(game_date, 1, 7) FROM {{ source('game', 'claim_market') }} WHERE LENGTH(game_date) >= 7
      UNION SELECT SUBSTR(as_of, 1, 7) FROM {{ source('game', 'ref_city_market') }} WHERE LENGTH(as_of) >= 7) m
WHERE turn_month IS NOT NULL

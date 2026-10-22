-- SELECT * because which columns exist depends on the sources loaded.
SELECT *, SUBSTR(game_date, 1, 7) AS turn_month
FROM {{ source('game', 'sources') }}

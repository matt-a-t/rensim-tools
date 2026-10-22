SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       persona_id, asset, quantity
FROM {{ source('game', 'turn_persona_assets') }}

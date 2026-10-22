SELECT source_id, game_month AS turn_month, account, family AS player_family, persona_id,
       start_location, start_location_key, line_no, seq, code, order_group, explanation
FROM {{ source('game', 'orders_sent') }}

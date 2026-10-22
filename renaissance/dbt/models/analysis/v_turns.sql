SELECT source_id, account, player_family, game_date, turn_month, real_date, actions_used,
       ROW_NUMBER() OVER (PARTITION BY account, player_family ORDER BY game_date) AS turn_no
FROM {{ ref('dim_sources') }}
WHERE kind = 'turn_report'

SELECT source_id, issue, game_month AS turn_month, seq, event_type, actor, actor_given,
       actor_family_key, actor_title, place_key, place_from_key, place_to_key, route_key,
       miles, quantity, target_family_key, truncated, raw_text
FROM {{ source('game', 'news_events') }}

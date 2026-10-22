SELECT e.issue, e.turn_month AS game_month, e.seq, e.event_type, e.actor,
       e.actor_family_key AS actor_family, e.actor_title, p.name AS place,
       pf.name AS route_from, pt.name AS route_to, e.miles, e.quantity,
       e.target_family_key AS target_family, e.truncated, e.raw_text
FROM {{ ref('fact_events') }} e
LEFT JOIN {{ source('game', 'dim_places') }} p  ON p.place_key  = e.place_key
LEFT JOIN {{ source('game', 'dim_places') }} pf ON pf.place_key = e.place_from_key
LEFT JOIN {{ source('game', 'dim_places') }} pt ON pt.place_key = e.place_to_key

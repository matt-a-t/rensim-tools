SELECT f.*, d.given_name, d.family, p.name AS location
FROM {{ ref('fact_persona_status') }} f
JOIN {{ source('game', 'dim_personae') }} d ON d.persona_id = f.persona_id
LEFT JOIN {{ source('game', 'dim_places') }} p ON p.place_key = f.location_key
WHERE f.source_id = (SELECT source_id FROM {{ ref('v_turns') }} WHERE account = (SELECT account FROM {{ source('game', 'my_settings') }}) ORDER BY game_date DESC LIMIT 1)

SELECT f.persona_id, d.given_name, d.family, f.game_date,
       f.morale,     LAG(f.morale)     OVER w AS prev_morale,
       f.popularity, LAG(f.popularity) OVER w AS prev_popularity,
       p.name AS location, LAG(p.name) OVER w AS prev_location,
       f.office,     LAG(f.office)     OVER w AS prev_office
FROM {{ ref('fact_persona_status') }} f
JOIN {{ source('game', 'dim_personae') }} d ON d.persona_id = f.persona_id
LEFT JOIN {{ source('game', 'dim_places') }} p ON p.place_key = f.location_key
WINDOW w AS (PARTITION BY f.account, f.persona_id ORDER BY f.game_date)

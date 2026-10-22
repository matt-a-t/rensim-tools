SELECT c.source_id, s.title AS source_title, s.kind AS source_kind, c.claim_id, c.claim_type,
       c.subject_name, c.subject_family_key AS family, c.predicate, c.object,
       COALESCE(p.name, c.place) AS place, c.game_date, c.hedge, c.claimed_by, c.quote, c.refs
FROM {{ ref('fact_claims') }} c
JOIN {{ ref('dim_sources') }} s ON s.source_id = c.source_id
LEFT JOIN {{ source('game', 'dim_places') }} p ON p.place_key = c.place_key

SELECT k.persona_id, d.given_name, k.skill, k.game_date, k.level,
       LAG(k.level) OVER (PARTITION BY k.persona_id, k.skill ORDER BY k.game_date) AS prev_level
FROM {{ ref('fact_persona_skills') }} k
JOIN {{ source('game', 'dim_personae') }} d ON d.persona_id = k.persona_id

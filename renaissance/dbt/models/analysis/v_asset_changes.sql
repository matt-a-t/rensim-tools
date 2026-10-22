SELECT a.persona_id, d.given_name, a.asset, a.game_date, a.quantity,
       LAG(a.quantity) OVER (PARTITION BY a.persona_id, a.asset ORDER BY a.game_date) AS prev_quantity
FROM {{ ref('fact_persona_assets') }} a
JOIN {{ source('game', 'dim_personae') }} d ON d.persona_id = a.persona_id

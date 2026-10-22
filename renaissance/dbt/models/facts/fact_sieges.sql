SELECT s.source_id, s.issue, s.game_month AS turn_month, s.place_key, s.besieger, s.besieger_given,
       s.besieger_family_key, CAST(s.besieger_persona_id AS BIGINT) AS besieger_persona_id, s.besieger_title, r.outcome, r.attrition_pct, r.detail, s.raw_text
FROM {{ source('game', 'news_sieges') }} s
LEFT JOIN {{ source('game', 'news_siege_results') }} r ON r.issue = s.issue AND r.place_key = s.place_key

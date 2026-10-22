SELECT source_id, claim_id,
       CASE WHEN LENGTH(game_date) >= 7 THEN SUBSTR(game_date, 1, 7) END AS turn_month, game_date,
       claim_type, subject_name, subject_given, subject_family_key,
       CAST(subject_persona_id AS BIGINT) AS subject_persona_id, subject_place_key,
       predicate, object, place, place_key, hedge, claimed_by, quote, refs
FROM {{ source('game', 'claims') }}

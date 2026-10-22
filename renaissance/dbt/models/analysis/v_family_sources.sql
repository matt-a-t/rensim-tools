SELECT family_key, spellings, official_rows, claim_rows, in_official, in_claims
FROM {{ source('game', 'dim_families') }}

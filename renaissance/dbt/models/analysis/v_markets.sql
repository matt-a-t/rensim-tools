WITH priced AS (
    SELECT f.*, s.reliability, s.title,
           CASE s.reliability WHEN 'official' THEN 1 WHEN 'ally_compiled' THEN 2 ELSE 3 END AS trust
    FROM {{ ref('fact_market_prices') }} f
    JOIN {{ ref('dim_sources') }} s ON s.source_id = f.source_id
), best AS (
    SELECT place_key, MIN(trust) AS trust FROM priced GROUP BY place_key
), latest_official AS (
    SELECT place_key, MAX(as_of) AS as_of FROM priced WHERE trust = 1 GROUP BY place_key
)
SELECT p.name AS place, x.place_key, x.side, x.item, x.price, x.quantity, x.bq, x.as_of, x.reliability,
       CASE x.trust WHEN 1 THEN 'my turn report' WHEN 2 THEN 'ally reference' ELSE x.title END AS source,
       p.miles_from_home
FROM priced x
JOIN best b ON b.place_key = x.place_key AND b.trust = x.trust
LEFT JOIN latest_official l ON l.place_key = x.place_key
JOIN {{ source('game', 'dim_places') }} p ON p.place_key = x.place_key
WHERE x.trust <> 1 OR x.as_of = l.as_of

SELECT p.name AS location, f.side, f.item, f.as_of AS game_date, f.price, f.quantity,
       LAG(f.price)    OVER w_market AS prev_price,
       LAG(f.quantity) OVER w_market AS prev_quantity
FROM {{ ref('fact_market_prices') }} f
JOIN {{ ref('dim_sources') }} s ON s.source_id = f.source_id
JOIN {{ source('game', 'dim_places') }} p ON p.place_key = f.place_key
WHERE s.kind = 'turn_report'
WINDOW w_market AS (PARTITION BY f.place_key, f.side, f.item ORDER BY f.as_of)

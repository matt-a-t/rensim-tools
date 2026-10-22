SELECT e.route_key,
       pa.name || ' - ' || pb.name                AS route,
       COUNT(*)                                   AS work_reports,
       COUNT(DISTINCT e.actor_family_key)         AS families_working,
       MIN(e.miles)                               AS shortest_reported_miles,
       MAX(e.miles)                               AS longest_reported_miles,
       MAX(e.issue)                               AS last_issue
FROM {{ ref('fact_events') }} e
JOIN {{ source('game', 'dim_places') }} pa ON pa.place_key = substr(e.route_key, 1, instr(e.route_key, '|') - 1)
JOIN {{ source('game', 'dim_places') }} pb ON pb.place_key = substr(e.route_key, instr(e.route_key, '|') + 1)
WHERE e.event_type = 'road_shortened'
GROUP BY e.route_key, pa.name, pb.name

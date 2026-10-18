-- Example questions. Each block starts with "-- name: <name>" and a one-line description.
-- Run them all with:  python ask.py      or one with:  python ask.py what_changed

-- name: what_changed
-- Everything that changed for my position between my last two turns.
WITH last2 AS (SELECT game_date FROM v_turns ORDER BY game_date DESC LIMIT 2),
latest AS (SELECT MAX(game_date) AS d FROM last2)
SELECT 'presence' AS kind, given_name || ' ' || family AS who,
       change || ' ' || location || COALESCE(' with ' || troops || ' T.', '') AS detail
FROM v_presence_changes WHERE game_date = (SELECT d FROM latest)
UNION ALL
SELECT 'morale', given_name, prev_morale || ' -> ' || morale
FROM v_persona_changes WHERE game_date = (SELECT d FROM latest) AND morale <> prev_morale
UNION ALL
SELECT 'popularity', given_name, prev_popularity || ' -> ' || popularity
FROM v_persona_changes WHERE game_date = (SELECT d FROM latest) AND popularity <> prev_popularity
UNION ALL
SELECT 'location', given_name, prev_location || ' -> ' || location
FROM v_persona_changes WHERE game_date = (SELECT d FROM latest) AND location <> prev_location
UNION ALL
SELECT 'skill', given_name, skill || ': ' || COALESCE(prev_level, 0) || ' -> ' || level
FROM v_skill_changes WHERE game_date = (SELECT d FROM latest) AND (prev_level IS NULL OR level <> prev_level)
UNION ALL
SELECT 'asset', given_name, asset || ': ' || COALESCE(prev_quantity, 0) || ' -> ' || quantity
FROM v_asset_changes WHERE game_date = (SELECT d FROM latest) AND (prev_quantity IS NULL OR quantity <> prev_quantity)
UNION ALL
SELECT 'market', location, side || ' ' || item || ' price ' || prev_price || ' -> ' || price
FROM v_market_changes WHERE game_date = (SELECT d FROM latest) AND price <> prev_price;

-- name: who_is_here
-- Everyone inside or outside my location on the latest turn, mine and others.
SELECT pr.location, pr.inside_or_outside, pr.persona_id, pr.given_name, pr.family, pr.troops,
       CASE WHEN d.is_mine THEN 'mine' ELSE 'other' END AS whose
FROM turn_presence pr
JOIN dim_personae d ON d.persona_id = pr.persona_id
WHERE pr.game_date = (SELECT MAX(game_date) FROM turn_presence)
ORDER BY pr.inside_or_outside, pr.persona_id;

-- name: best_at_each_skill
-- For each skill, which of my characters is strongest (latest turn).
SELECT k.skill, s.given_name, k.level
FROM turn_persona_skills k
JOIN v_personae_now s ON s.persona_id = k.persona_id AND s.game_date = k.game_date
WHERE k.level = (SELECT MAX(k2.level) FROM turn_persona_skills k2
                 WHERE k2.skill = k.skill AND k2.game_date = k.game_date)
ORDER BY k.level DESC, k.skill, s.given_name;

-- name: family_wealth
-- Ducats held by each of my characters, largest first.
SELECT s.given_name, s.family, a.quantity AS ducats
FROM turn_persona_assets a
JOIN v_personae_now s ON s.persona_id = a.persona_id AND s.game_date = a.game_date
WHERE a.asset = 'ducats'
ORDER BY ducats DESC;

-- name: nearest_places
-- Places my lookups found, by straight-line distance from home.
SELECT l.place, l.is_set_up, l.lat, l.lon, l.miles_from_home, l.game_date AS looked_up
FROM turn_lookups l
ORDER BY l.is_set_up DESC, l.miles_from_home;

-- name: busiest_roads
-- Routes with the most road-building reported in the GM newsletter.
SELECT route, work_reports, families_working, shortest_reported_miles, longest_reported_miles
FROM v_roads
WHERE work_reports > 1
ORDER BY work_reports DESC, route;

-- name: most_active_families
-- Families appearing most often in official newsletter events.
SELECT actor_family, COUNT(*) AS events,
       SUM(CASE WHEN event_type = 'road_shortened' THEN 1 ELSE 0 END) AS road_work,
       SUM(CASE WHEN event_type <> 'road_shortened' THEN 1 ELSE 0 END) AS other_events
FROM news_events
WHERE actor_family IS NOT NULL
GROUP BY actor_family
HAVING COUNT(*) > 1
ORDER BY events DESC, actor_family;

-- name: conflict
-- Sieges, their results, and captures from the GM newsletter.
SELECT s.issue, 'siege' AS kind, p.name AS place, s.besieger || COALESCE(', ' || s.besieger_title, '') AS by_whom,
       r.outcome || ', defenders lost ' || r.attrition_pct || '%' AS result
FROM news_sieges s
JOIN dim_places p ON p.place_key = s.place_key
LEFT JOIN news_siege_results r ON r.issue = s.issue AND r.place_key = s.place_key
UNION ALL
SELECT issue, event_type, place, actor || COALESCE(', ' || actor_title, ''),
       target_family || ' tower taken with ' || quantity || ' auxiliaries'
FROM v_events WHERE event_type = 'tower_captured'
ORDER BY 1, 2, 3, 4;

-- name: spelling_variants
-- Places written more than one way across the sources, now unified under one key.
SELECT name, spellings, mentions
FROM dim_places
WHERE spelling_count > 1
ORDER BY mentions DESC, name;

-- name: claims_by_source
-- How each player publication hedges its claims: plain assertion vs "reportedly" vs "it is said".
SELECT s.title, COUNT(*) AS claims,
       SUM(CASE WHEN c.hedge = 'asserted' THEN 1 ELSE 0 END) AS asserted,
       SUM(CASE WHEN c.hedge = 'reported' THEN 1 ELSE 0 END) AS reported,
       SUM(CASE WHEN c.hedge = 'rumored'  THEN 1 ELSE 0 END) AS rumored,
       SUM(CASE WHEN c.hedge = 'denied'   THEN 1 ELSE 0 END) AS denied
FROM claims c JOIN sources s ON s.source_id = c.source_id
GROUP BY s.title
ORDER BY claims DESC, s.title;

-- name: disputed_titles
-- Titles granted, contested or declared void, and who says so.
SELECT subject_name, predicate, object, hedge, claimed_by, source_title
FROM v_claims
WHERE claim_type = 'title_dispute'
ORDER BY subject_name, source_title, claim_id;

-- name: who_holds_what
-- Offices and titles players claim someone holds, with how firmly they say it.
SELECT subject_name, family, object AS office, place, hedge, game_date, source_title
FROM v_claims
WHERE claim_type = 'office'
ORDER BY family IS NULL, family, subject_name, office;

-- name: families_official_and_claimed
-- Families that show up in both the GM's record and player claims (confirmed spelling aliases applied).
SELECT family_key, spellings, official_rows, claim_rows
FROM dim_families
WHERE in_official AND in_claims
ORDER BY family_key;

-- name: price_comparison
-- Prices a player newsletter reports elsewhere vs the GM's prices at my location, per good.
SELECT cm.item, cm.location AS reported_at, cm.price AS reported_price, cm.game_date AS reported_for,
       tm.location AS my_location, tm.price AS my_price,
       tm.price - cm.price AS my_price_minus_reported
FROM claim_market cm
JOIN turn_market tm ON tm.item = cm.item AND tm.side = cm.side
WHERE tm.game_date = (SELECT MAX(game_date) FROM turn_market)
ORDER BY ABS(tm.price - cm.price) DESC, cm.item, cm.location;

-- name: wealth_over_time
-- Total ducats held by my characters each turn, across both of my positions.
SELECT a.game_date, s.player_family, SUM(a.quantity) AS ducats, COUNT(DISTINCT a.persona_id) AS characters
FROM turn_persona_assets a
JOIN sources s ON s.source_id = a.source_id
WHERE a.asset = 'ducats' AND s.account = (SELECT account FROM my_settings)
GROUP BY a.game_date, s.player_family
ORDER BY a.game_date;

-- name: conquests
-- Towns, towers and de facto control taken by force, per the GM's newsletters.
SELECT game_month, event_type, place, actor, actor_title, quantity AS auxiliaries, target_family
FROM v_events
WHERE event_type IN ('location_captured', 'tower_captured', 'de_facto_control')
ORDER BY game_month, place;

-- name: where_to_buy_grain
-- Places that supply grain or food goods, nearest first (prices are per unit; check as_of).
SELECT place, item, price, quantity, as_of, reliability, miles_from_home
FROM v_markets
WHERE side = 'supply' AND item IN ('grain', 'fish', 'cattle', 'sheep', 'fruit', 'oil', 'rice', 'wheat')
  AND miles_from_home IS NOT NULL
ORDER BY miles_from_home, price, item
LIMIT 15;

-- name: best_place_to_sell
-- For each good, the place paying the most, how far it is, and how fresh the price is.
SELECT v.item, v.place, v.price, v.as_of, v.reliability, v.miles_from_home
FROM v_markets v
WHERE v.side = 'demand' AND v.price > 0
  AND v.price = (SELECT MAX(w.price) FROM v_markets w WHERE w.side = 'demand' AND w.item = v.item)
ORDER BY v.item, v.place;

-- name: my_past_orders
-- What I ordered each turn, persona by persona, in plain English (from orders/*.txt).
SELECT o.game_month, o.persona_id, p.given_name, o.start_location, o.code, o.explanation
FROM orders_sent o
LEFT JOIN (SELECT DISTINCT persona_id, given_name FROM v_personae_now) p USING (persona_id)
ORDER BY o.game_month DESC, o.persona_id, o.line_no, o.seq;

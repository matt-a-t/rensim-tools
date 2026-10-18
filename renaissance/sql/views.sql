-- Views on top of the loaded tables. Plain SQL that runs on both DuckDB and SQLite.
-- Raw tables hold one row per fact per turn (a "snapshot"); these views turn the snapshots
-- into the questions you actually ask: what's current, what changed, who's doing what.

-- Turn reports in order, so "previous turn" is easy to find.
DROP VIEW IF EXISTS v_turns;
CREATE VIEW v_turns AS
SELECT source_id, account, player_family, game_date, real_date, actions_used,
       ROW_NUMBER() OVER (PARTITION BY account, player_family ORDER BY game_date) AS turn_no
FROM sources
WHERE kind = 'turn_report';

-- Each of my characters as of my latest turn (my account's newest report).
DROP VIEW IF EXISTS v_personae_now;
CREATE VIEW v_personae_now AS
SELECT s.*
FROM turn_persona_status s
WHERE s.source_id = (SELECT source_id FROM v_turns WHERE account = (SELECT account FROM my_settings) ORDER BY game_date DESC LIMIT 1);

-- Morale / popularity / location / office, with the previous turn's value alongside.
DROP VIEW IF EXISTS v_persona_changes;
CREATE VIEW v_persona_changes AS
SELECT persona_id, given_name, family, game_date,
       morale,     LAG(morale)     OVER w AS prev_morale,
       popularity, LAG(popularity) OVER w AS prev_popularity,
       location,   LAG(location)   OVER w AS prev_location,
       office,     LAG(office)     OVER w AS prev_office
FROM turn_persona_status
WINDOW w AS (PARTITION BY account, persona_id ORDER BY game_date);

-- Skill levels turn over turn (prev_level is NULL for a newly learned skill).
DROP VIEW IF EXISTS v_skill_changes;
CREATE VIEW v_skill_changes AS
SELECT k.persona_id, s.given_name, k.skill, k.game_date, k.level,
       LAG(k.level) OVER (PARTITION BY k.persona_id, k.skill ORDER BY k.game_date) AS prev_level
FROM turn_persona_skills k
JOIN turn_persona_status s ON s.persona_id = k.persona_id AND s.game_date = k.game_date;

-- Asset quantities turn over turn.
DROP VIEW IF EXISTS v_asset_changes;
CREATE VIEW v_asset_changes AS
SELECT a.persona_id, s.given_name, a.asset, a.game_date, a.quantity,
       LAG(a.quantity) OVER (PARTITION BY a.persona_id, a.asset ORDER BY a.game_date) AS prev_quantity
FROM turn_persona_assets a
JOIN turn_persona_status s ON s.persona_id = a.persona_id AND s.game_date = a.game_date;

-- Who arrived at / left a location between consecutive turns.
DROP VIEW IF EXISTS v_presence_changes;
CREATE VIEW v_presence_changes AS
WITH p AS (
    SELECT t.turn_no, pr.*
    FROM turn_presence pr JOIN v_turns t ON t.source_id = pr.source_id
)
SELECT cur.game_date, cur.location, 'arrived' AS change, cur.persona_id, cur.given_name,
       cur.family, cur.troops, cur.inside_or_outside
FROM p cur
WHERE cur.turn_no > 1
  AND NOT EXISTS (SELECT 1 FROM p prev
                  WHERE prev.account = cur.account AND prev.player_family = cur.player_family AND prev.turn_no = cur.turn_no - 1
                    AND prev.location_key = cur.location_key AND prev.persona_id = cur.persona_id
                    AND prev.inside_or_outside = cur.inside_or_outside)
UNION ALL
SELECT nxt_t.game_date, prev.location, 'left' AS change, prev.persona_id, prev.given_name,
       prev.family, prev.troops, prev.inside_or_outside
FROM p prev
JOIN v_turns nxt_t ON nxt_t.account = prev.account AND nxt_t.player_family = prev.player_family AND nxt_t.turn_no = prev.turn_no + 1
WHERE NOT EXISTS (SELECT 1 FROM p cur
                  WHERE cur.account = prev.account AND cur.player_family = prev.player_family AND cur.turn_no = prev.turn_no + 1
                    AND cur.location_key = prev.location_key AND cur.persona_id = prev.persona_id
                    AND cur.inside_or_outside = prev.inside_or_outside);

-- Market prices turn over turn.
DROP VIEW IF EXISTS v_market_changes;
CREATE VIEW v_market_changes AS
SELECT location, side, item, game_date, price, quantity,
       LAG(price)    OVER (PARTITION BY location_key, side, item ORDER BY game_date) AS prev_price,
       LAG(quantity) OVER (PARTITION BY location_key, side, item ORDER BY game_date) AS prev_quantity
FROM turn_market;

-- Road work from the GM newsletter, one row per route (direction ignored).
DROP VIEW IF EXISTS v_roads;
CREATE VIEW v_roads AS
SELECT e.route_key,
       pa.name || ' - ' || pb.name                AS route,
       COUNT(*)                                   AS work_reports,
       COUNT(DISTINCT e.actor_family)             AS families_working,
       MIN(e.miles)                               AS shortest_reported_miles,
       MAX(e.miles)                               AS longest_reported_miles,
       MAX(e.issue)                               AS last_issue
FROM news_events e
JOIN dim_places pa ON pa.place_key = substr(e.route_key, 1, instr(e.route_key, '|') - 1)
JOIN dim_places pb ON pb.place_key = substr(e.route_key, instr(e.route_key, '|') + 1)
WHERE e.event_type = 'road_shortened'
GROUP BY e.route_key, pa.name, pb.name;

-- Every official event, with places resolved to their canonical name.
DROP VIEW IF EXISTS v_events;
CREATE VIEW v_events AS
SELECT e.issue, e.game_month, e.seq, e.event_type, e.actor, e.actor_family, e.actor_title,
       COALESCE(p.name, e.place) AS place, pf.name AS route_from, pt.name AS route_to,
       e.miles, e.quantity, e.target_family, e.truncated, e.raw_text
FROM news_events e
LEFT JOIN dim_places p  ON p.place_key  = e.place_key
LEFT JOIN dim_places pf ON pf.place_key = e.place_from_key
LEFT JOIN dim_places pt ON pt.place_key = e.place_to_key;

-- ---------------------------------------------------------------------------------------
-- Player publications. Claims are never mixed into the official tables above; these views
-- put them side by side with their source, hedge, and the official record where one exists.

-- Every claim with its source and canonical names.
DROP VIEW IF EXISTS v_claims;
CREATE VIEW v_claims AS
SELECT c.source_id, s.title AS source_title, s.kind AS source_kind, c.claim_id, c.claim_type,
       c.subject_name, c.subject_family_key AS family, c.predicate, c.object,
       COALESCE(p.name, c.place) AS place, c.game_date, c.hedge, c.claimed_by, c.quote, c.refs
FROM claims c
JOIN sources s ON s.source_id = c.source_id
LEFT JOIN dim_places p ON p.place_key = c.place_key;

-- For each family: how often it appears in official sources vs player claims.
DROP VIEW IF EXISTS v_family_sources;
CREATE VIEW v_family_sources AS
SELECT family_key, spellings, official_rows, claim_rows, in_official, in_claims
FROM dim_families;

-- ---------------------------------------------------------------------------------------
-- Every market price we know of, one row per place / side / good, from the freshest source:
-- my own turn reports (official), an ally's compiled reference (ally_compiled), and player
-- newsletters (player_claim). Use as_of and reliability before trusting a price.
DROP VIEW IF EXISTS v_markets;
CREATE VIEW v_markets AS
WITH my_latest AS (
    SELECT location_key, MAX(game_date) AS d FROM turn_market GROUP BY location_key
), mine AS (
    SELECT m.location_key AS place_key, m.side, m.item, m.price, m.quantity, m.bq,
           m.game_date AS as_of, 'official' AS reliability, 'my turn report' AS source
    FROM turn_market m JOIN my_latest l ON l.location_key = m.location_key AND l.d = m.game_date
), ally AS (
    SELECT r.city_key, r.side, r.item, r.price, r.quantity, r.bq, r.as_of, 'ally_compiled', 'ally reference'
    FROM ref_city_market r
    WHERE r.city_key NOT IN (SELECT place_key FROM mine)
), claimed AS (
    SELECT c.location_key, c.side, c.item, c.price, NULL, c.bq, c.game_date, 'player_claim', s.title
    FROM claim_market c JOIN sources s ON s.source_id = c.source_id
    WHERE c.location_key NOT IN (SELECT place_key FROM mine)
      AND c.location_key NOT IN (SELECT city_key FROM ref_city_market)
)
SELECT p.name AS place, x.*, p.miles_from_home
FROM (SELECT * FROM mine UNION ALL SELECT * FROM ally UNION ALL SELECT * FROM claimed) x
JOIN dim_places p ON p.place_key = x.place_key;

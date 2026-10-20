-- Views on top of the loaded tables. Plain SQL that runs on both DuckDB and SQLite.
--
-- Three layers (see "Data model" in docs/architecture.md):
--   1. Dimensions: who, where, when, which source. dim_places, dim_personae and dim_families are
--      tables built by normalize.py; the rest are views below.
--   2. Facts: one row per measurement or event, holding keys into the dimensions plus measures.
--      Each fact states its grain: what one row means.
--   3. Analysis views (v_*): the questions you actually ask, written against facts and dimensions.
-- Questions in questions.sql use layers 2 and 3; the raw loaded tables stay available for ad hoc SQL.
--
-- Every fact carries turn_month ('1547-03'), the key into dim_turns. Turn reports are dated at the
-- end of the month, newsletters and orders by month, claims by whatever the publication says.

-- =======================================================================================
-- Dimensions
-- =======================================================================================

-- Every source document once: turn reports, GM newsletters, ally references, player
-- publications, saved orders. reliability is how far to trust it (official / ally_compiled /
-- player_claim / my_orders). SELECT * because which columns exist depends on the sources loaded.
DROP VIEW IF EXISTS dim_sources;
CREATE VIEW dim_sources AS
SELECT *, SUBSTR(game_date, 1, 7) AS turn_month
FROM sources;

-- Every game month any source refers to. month_index counts months, so the calendar month before
-- is month_index - 1 (the previous turn *report* can be further back; see v_turns).
DROP VIEW IF EXISTS dim_turns;
CREATE VIEW dim_turns AS
SELECT turn_month,
       CAST(SUBSTR(turn_month, 1, 4) AS INTEGER) AS year,
       CAST(SUBSTR(turn_month, 6, 2) AS INTEGER) AS month,
       CAST(SUBSTR(turn_month, 1, 4) AS INTEGER) * 12 + CAST(SUBSTR(turn_month, 6, 2) AS INTEGER) - 1 AS month_index
FROM (SELECT SUBSTR(game_date, 1, 7) AS turn_month FROM sources WHERE LENGTH(game_date) >= 7
      UNION SELECT game_month FROM orders_sent
      UNION SELECT SUBSTR(game_date, 1, 7) FROM claims WHERE LENGTH(game_date) >= 7
      UNION SELECT SUBSTR(game_date, 1, 7) FROM claim_market WHERE LENGTH(game_date) >= 7
      UNION SELECT SUBSTR(as_of, 1, 7) FROM ref_city_market WHERE LENGTH(as_of) >= 7) m
WHERE turn_month IS NOT NULL;

-- Slowly changing (type 2): one row per version of a persona's office. A new version starts
-- whenever the office differs from the persona's previous turn report. valid_to is the month the
-- next version starts (exclusive), NULL for the version in force at the latest report.
-- Join a fact to the version in force at its month:
--   f.turn_month >= o.valid_from AND (f.turn_month < o.valid_to OR o.valid_to IS NULL)
DROP VIEW IF EXISTS dim_persona_offices;
CREATE VIEW dim_persona_offices AS
WITH monthly AS (    -- MAX: one office per persona per month, even if two reports overlap
    SELECT persona_id, SUBSTR(game_date, 1, 7) AS turn_month, MAX(office) AS office
    FROM turn_persona_status
    GROUP BY persona_id, SUBSTR(game_date, 1, 7)
), flagged AS (
    SELECT persona_id, turn_month, office,
           CASE WHEN LAG(turn_month) OVER w_office IS NULL
                  OR COALESCE(office, '') <> COALESCE(LAG(office) OVER w_office, '') THEN 1 ELSE 0 END AS starts_version
    FROM monthly
    WINDOW w_office AS (PARTITION BY persona_id ORDER BY turn_month)
), numbered AS (
    SELECT persona_id, turn_month, office,
           SUM(starts_version) OVER (PARTITION BY persona_id ORDER BY turn_month ROWS UNBOUNDED PRECEDING) AS version
    FROM flagged
), versions AS (
    SELECT persona_id, version, office, MIN(turn_month) AS valid_from, MAX(turn_month) AS last_seen
    FROM numbered
    GROUP BY persona_id, version, office
), ranged AS (
    SELECT persona_id, version, office, valid_from,
           LEAD(valid_from) OVER (PARTITION BY persona_id ORDER BY version) AS valid_to, last_seen
    FROM versions
)
SELECT persona_id, version, office, valid_from, valid_to, last_seen,
       CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END AS is_current
FROM ranged;

-- Slowly changing (type 2): who holds each office at a place (SOVEREIGN, FEUDATORY, MAGISTRATE,
-- bishops...), one row per holder per place and role. Same versioning as dim_persona_offices.
DROP VIEW IF EXISTS dim_place_officials;
CREATE VIEW dim_place_officials AS
WITH monthly AS (    -- MAX: one holder per role per month, even if two reports overlap
    SELECT location_key AS place_key, role, SUBSTR(game_date, 1, 7) AS turn_month, MAX(holder) AS holder
    FROM turn_location_officials
    GROUP BY location_key, role, SUBSTR(game_date, 1, 7)
), flagged AS (
    SELECT place_key, role, turn_month, holder,
           CASE WHEN LAG(turn_month) OVER w_holder IS NULL
                  OR COALESCE(holder, '') <> COALESCE(LAG(holder) OVER w_holder, '') THEN 1 ELSE 0 END AS starts_version
    FROM monthly
    WINDOW w_holder AS (PARTITION BY place_key, role ORDER BY turn_month)
), numbered AS (
    SELECT place_key, role, turn_month, holder,
           SUM(starts_version) OVER (PARTITION BY place_key, role ORDER BY turn_month ROWS UNBOUNDED PRECEDING) AS version
    FROM flagged
), versions AS (
    SELECT place_key, role, version, holder, MIN(turn_month) AS valid_from, MAX(turn_month) AS last_seen
    FROM numbered
    GROUP BY place_key, role, version, holder
), ranged AS (
    SELECT place_key, role, version, holder, valid_from,
           LEAD(valid_from) OVER (PARTITION BY place_key, role ORDER BY version) AS valid_to, last_seen
    FROM versions
)
SELECT place_key, role, version, holder, valid_from, valid_to, last_seen,
       CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END AS is_current
FROM ranged;

-- =======================================================================================
-- Facts
-- =======================================================================================

-- Periodic snapshot. Grain: one persona in one turn report. Names come from dim_personae.
DROP VIEW IF EXISTS fact_persona_status;
CREATE VIEW fact_persona_status AS
SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       persona_id, location_key, title, religion, status, office, morale, popularity, piety
FROM turn_persona_status;

-- Periodic snapshot. Grain: one skill of one persona in one turn report.
DROP VIEW IF EXISTS fact_persona_skills;
CREATE VIEW fact_persona_skills AS
SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       persona_id, skill, level
FROM turn_persona_skills;

-- Periodic snapshot. Grain: one asset of one persona in one turn report.
DROP VIEW IF EXISTS fact_persona_assets;
CREATE VIEW fact_persona_assets AS
SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       persona_id, asset, quantity
FROM turn_persona_assets;

-- Periodic snapshot. Grain: one persona seen inside or outside one location in one turn report.
DROP VIEW IF EXISTS fact_presence;
CREATE VIEW fact_presence AS
SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       location_key, inside_or_outside, persona_id, family_key, troops, ships
FROM turn_presence;

-- Transaction. Grain: one lookup answered in one turn report. `place` is kept as asked, because
-- a lookup that found nothing (is_set_up false) names no real place.
DROP VIEW IF EXISTS fact_lookups;
CREATE VIEW fact_lookups AS
SELECT source_id, account, player_family, SUBSTR(game_date, 1, 7) AS turn_month, game_date,
       place, place_key, family_key, is_set_up, lat, lon, miles_from_home
FROM turn_lookups;

-- Periodic snapshot. Grain: one price for one good, one side, at one place, from one source
-- (and one claim, for player publications). Three origins, told apart by dim_sources.reliability:
-- turn reports (official), an ally's reference (ally_compiled), player claims (player_claim).
DROP VIEW IF EXISTS fact_market_prices;
CREATE VIEW fact_market_prices AS
SELECT source_id, NULL AS claim_id, location_key AS place_key, SUBSTR(game_date, 1, 7) AS turn_month,
       game_date AS as_of, side, item, price, quantity, bq
FROM turn_market
UNION ALL
SELECT source_id, NULL, city_key, SUBSTR(as_of, 1, 7), as_of, side, item, price, quantity, bq
FROM ref_city_market
UNION ALL
SELECT source_id, claim_id, location_key, SUBSTR(game_date, 1, 7), game_date, side, item, price, NULL, bq
FROM claim_market;

-- Transaction. Grain: one event in one GM newsletter (issue, seq). Newsletter actors have no
-- persona ID, so actor names stay on the fact; families are keyed.
DROP VIEW IF EXISTS fact_events;
CREATE VIEW fact_events AS
SELECT source_id, issue, game_month AS turn_month, seq, event_type, actor, actor_given,
       actor_family_key, actor_title, place_key, place_from_key, place_to_key, route_key,
       miles, quantity, target_family_key, truncated, raw_text
FROM news_events;

-- Transaction. Grain: one siege reported in one GM newsletter, with its result when reported.
DROP VIEW IF EXISTS fact_sieges;
CREATE VIEW fact_sieges AS
SELECT s.source_id, s.issue, s.game_month AS turn_month, s.place_key, s.besieger, s.besieger_given,
       s.besieger_family_key, s.besieger_title, r.outcome, r.attrition_pct, r.detail, s.raw_text
FROM news_sieges s
LEFT JOIN news_siege_results r ON r.issue = s.issue AND r.place_key = s.place_key;

-- Transaction. Grain: one order code sent (line_no, seq) in one month's orders file.
-- start_location is kept for any place the dimension doesn't know.
DROP VIEW IF EXISTS fact_orders;
CREATE VIEW fact_orders AS
SELECT source_id, game_month AS turn_month, account, family AS player_family, persona_id,
       start_location, start_location_key, line_no, seq, code, order_group, explanation
FROM orders_sent;

-- Transaction. Grain: one claim in one player publication. Claims name people as the page does
-- (no persona ID), and `place` keeps the page's wording for a place no key was made from.
-- turn_month is NULL for claims dated only by year.
DROP VIEW IF EXISTS fact_claims;
CREATE VIEW fact_claims AS
SELECT source_id, claim_id,
       CASE WHEN LENGTH(game_date) >= 7 THEN SUBSTR(game_date, 1, 7) END AS turn_month, game_date,
       claim_type, subject_name, subject_given, subject_family_key, subject_place_key,
       predicate, object, place, place_key, hedge, claimed_by, quote, refs
FROM claims;

-- =======================================================================================
-- Analysis views: snapshots become "now" and "what changed"; claims sit beside the record.
-- =======================================================================================

-- Turn reports in order, so "previous turn" is easy to find.
DROP VIEW IF EXISTS v_turns;
CREATE VIEW v_turns AS
SELECT source_id, account, player_family, game_date, turn_month, real_date, actions_used,
       ROW_NUMBER() OVER (PARTITION BY account, player_family ORDER BY game_date) AS turn_no
FROM dim_sources
WHERE kind = 'turn_report';

-- Each of my characters as of my latest turn (my account's newest report).
DROP VIEW IF EXISTS v_personae_now;
CREATE VIEW v_personae_now AS
SELECT f.*, d.given_name, d.family, p.name AS location
FROM fact_persona_status f
JOIN dim_personae d ON d.persona_id = f.persona_id
LEFT JOIN dim_places p ON p.place_key = f.location_key
WHERE f.source_id = (SELECT source_id FROM v_turns WHERE account = (SELECT account FROM my_settings) ORDER BY game_date DESC LIMIT 1);

-- Morale / popularity / location / office, with the previous turn's value alongside.
DROP VIEW IF EXISTS v_persona_changes;
CREATE VIEW v_persona_changes AS
SELECT f.persona_id, d.given_name, d.family, f.game_date,
       f.morale,     LAG(f.morale)     OVER w AS prev_morale,
       f.popularity, LAG(f.popularity) OVER w AS prev_popularity,
       p.name AS location, LAG(p.name) OVER w AS prev_location,
       f.office,     LAG(f.office)     OVER w AS prev_office
FROM fact_persona_status f
JOIN dim_personae d ON d.persona_id = f.persona_id
LEFT JOIN dim_places p ON p.place_key = f.location_key
WINDOW w AS (PARTITION BY f.account, f.persona_id ORDER BY f.game_date);

-- Skill levels turn over turn (prev_level is NULL for a newly learned skill).
DROP VIEW IF EXISTS v_skill_changes;
CREATE VIEW v_skill_changes AS
SELECT k.persona_id, d.given_name, k.skill, k.game_date, k.level,
       LAG(k.level) OVER (PARTITION BY k.persona_id, k.skill ORDER BY k.game_date) AS prev_level
FROM fact_persona_skills k
JOIN dim_personae d ON d.persona_id = k.persona_id;

-- Asset quantities turn over turn.
DROP VIEW IF EXISTS v_asset_changes;
CREATE VIEW v_asset_changes AS
SELECT a.persona_id, d.given_name, a.asset, a.game_date, a.quantity,
       LAG(a.quantity) OVER (PARTITION BY a.persona_id, a.asset ORDER BY a.game_date) AS prev_quantity
FROM fact_persona_assets a
JOIN dim_personae d ON d.persona_id = a.persona_id;

-- Who arrived at / left a location between consecutive turns.
DROP VIEW IF EXISTS v_presence_changes;
CREATE VIEW v_presence_changes AS
WITH p AS (
    SELECT t.turn_no, f.account, f.player_family, f.game_date, f.location_key, pl.name AS location,
           f.inside_or_outside, f.persona_id, d.given_name, d.family, f.troops
    FROM fact_presence f
    JOIN v_turns t ON t.source_id = f.source_id
    LEFT JOIN dim_personae d ON d.persona_id = f.persona_id
    LEFT JOIN dim_places pl ON pl.place_key = f.location_key
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

-- Market prices from turn reports, turn over turn.
DROP VIEW IF EXISTS v_market_changes;
CREATE VIEW v_market_changes AS
SELECT p.name AS location, f.side, f.item, f.as_of AS game_date, f.price, f.quantity,
       LAG(f.price)    OVER w_market AS prev_price,
       LAG(f.quantity) OVER w_market AS prev_quantity
FROM fact_market_prices f
JOIN dim_sources s ON s.source_id = f.source_id
JOIN dim_places p ON p.place_key = f.place_key
WHERE s.kind = 'turn_report'
WINDOW w_market AS (PARTITION BY f.place_key, f.side, f.item ORDER BY f.as_of);

-- Road work from the GM newsletter, one row per route (direction ignored).
DROP VIEW IF EXISTS v_roads;
CREATE VIEW v_roads AS
SELECT e.route_key,
       pa.name || ' - ' || pb.name                AS route,
       COUNT(*)                                   AS work_reports,
       COUNT(DISTINCT e.actor_family_key)         AS families_working,
       MIN(e.miles)                               AS shortest_reported_miles,
       MAX(e.miles)                               AS longest_reported_miles,
       MAX(e.issue)                               AS last_issue
FROM fact_events e
JOIN dim_places pa ON pa.place_key = substr(e.route_key, 1, instr(e.route_key, '|') - 1)
JOIN dim_places pb ON pb.place_key = substr(e.route_key, instr(e.route_key, '|') + 1)
WHERE e.event_type = 'road_shortened'
GROUP BY e.route_key, pa.name, pb.name;

-- Every official event, with places resolved to their canonical name.
DROP VIEW IF EXISTS v_events;
CREATE VIEW v_events AS
SELECT e.issue, e.turn_month AS game_month, e.seq, e.event_type, e.actor,
       e.actor_family_key AS actor_family, e.actor_title, p.name AS place,
       pf.name AS route_from, pt.name AS route_to, e.miles, e.quantity,
       e.target_family_key AS target_family, e.truncated, e.raw_text
FROM fact_events e
LEFT JOIN dim_places p  ON p.place_key  = e.place_key
LEFT JOIN dim_places pf ON pf.place_key = e.place_from_key
LEFT JOIN dim_places pt ON pt.place_key = e.place_to_key;

-- ---------------------------------------------------------------------------------------
-- Player publications. Claims are never mixed into the official facts above; these views
-- put them side by side with their source, hedge, and the official record where one exists.

-- Every claim with its source and canonical names.
DROP VIEW IF EXISTS v_claims;
CREATE VIEW v_claims AS
SELECT c.source_id, s.title AS source_title, s.kind AS source_kind, c.claim_id, c.claim_type,
       c.subject_name, c.subject_family_key AS family, c.predicate, c.object,
       COALESCE(p.name, c.place) AS place, c.game_date, c.hedge, c.claimed_by, c.quote, c.refs
FROM fact_claims c
JOIN dim_sources s ON s.source_id = c.source_id
LEFT JOIN dim_places p ON p.place_key = c.place_key;

-- For each family: how often it appears in official sources vs player claims.
DROP VIEW IF EXISTS v_family_sources;
CREATE VIEW v_family_sources AS
SELECT family_key, spellings, official_rows, claim_rows, in_official, in_claims
FROM dim_families;

-- ---------------------------------------------------------------------------------------
-- Every market price we know of, one row per place / side / good, from the most trusted source
-- that has the place: turn reports (official, latest report only), then an ally's compiled
-- reference (ally_compiled), then player newsletters (player_claim). Use as_of and reliability
-- before trusting a price.
DROP VIEW IF EXISTS v_markets;
CREATE VIEW v_markets AS
WITH priced AS (
    SELECT f.*, s.reliability, s.title,
           CASE s.reliability WHEN 'official' THEN 1 WHEN 'ally_compiled' THEN 2 ELSE 3 END AS trust
    FROM fact_market_prices f
    JOIN dim_sources s ON s.source_id = f.source_id
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
JOIN dim_places p ON p.place_key = x.place_key
WHERE x.trust <> 1 OR x.as_of = l.as_of;

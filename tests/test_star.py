"""The star schema in views.sql: each fact is unique at its stated grain, every key in a fact finds
its dimension row, and the type 2 dimensions version history correctly."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from renaissance.load import connect, load_sqlite
from renaissance.run import parse_all

# fact -> the columns that identify one row (its grain, as commented in views.sql).
GRAIN = {
    "fact_persona_status": ["source_id", "persona_id"],
    "fact_persona_skills": ["source_id", "persona_id", "skill"],
    "fact_persona_assets": ["source_id", "persona_id", "asset"],
    "fact_presence": ["source_id", "location_key", "inside_or_outside", "persona_id"],
    "fact_lookups": ["source_id", "place"],
    "fact_market_prices": ["source_id", "claim_id", "place_key", "side", "item"],
    "fact_events": ["source_id", "seq"],
    "fact_sieges": ["source_id", "place_key"],
    "fact_orders": ["source_id", "line_no", "seq"],
    "fact_claims": ["source_id", "claim_id"],
}

# (fact, column, dimension, dimension key). NULL keys are allowed; a key that's set must exist.
# fact_orders.start_location_key is left out: orders aren't sightings, so they don't add places.
FOREIGN_KEYS = [
    (fact, "source_id", "dim_sources", "source_id") for fact in GRAIN
] + [
    (fact, "turn_month", "dim_turns", "turn_month") for fact in GRAIN
] + [
    ("fact_persona_status", "persona_id", "dim_personae", "persona_id"),
    ("fact_persona_status", "location_key", "dim_places", "place_key"),
    ("fact_persona_skills", "persona_id", "dim_personae", "persona_id"),
    ("fact_persona_assets", "persona_id", "dim_personae", "persona_id"),
    ("fact_presence", "persona_id", "dim_personae", "persona_id"),
    ("fact_presence", "location_key", "dim_places", "place_key"),
    ("fact_presence", "family_key", "dim_families", "family_key"),
    ("fact_lookups", "place_key", "dim_places", "place_key"),
    ("fact_lookups", "family_key", "dim_families", "family_key"),
    ("fact_market_prices", "place_key", "dim_places", "place_key"),
    ("fact_events", "place_key", "dim_places", "place_key"),
    ("fact_events", "place_from_key", "dim_places", "place_key"),
    ("fact_events", "place_to_key", "dim_places", "place_key"),
    ("fact_events", "actor_family_key", "dim_families", "family_key"),
    ("fact_events", "target_family_key", "dim_families", "family_key"),
    ("fact_sieges", "place_key", "dim_places", "place_key"),
    ("fact_sieges", "besieger_family_key", "dim_families", "family_key"),
    ("fact_orders", "persona_id", "dim_personae", "persona_id"),
    ("fact_claims", "place_key", "dim_places", "place_key"),
    ("fact_claims", "subject_place_key", "dim_places", "place_key"),
    ("fact_claims", "subject_family_key", "dim_families", "family_key"),
]


def grain_violations(con) -> list[str]:
    out = []
    for fact, cols in GRAIN.items():
        # COALESCE so a NULL in a grain column (claim_id on turn prices) still groups.
        keys = ", ".join(f"COALESCE(CAST({c} AS TEXT), '')" for c in cols)
        n = con.execute(f"SELECT COUNT(*) FROM (SELECT {keys} FROM {fact} GROUP BY {keys} HAVING COUNT(*) > 1) d").fetchone()[0]
        if n:
            out.append(f"{fact}: {n} duplicated grain value(s) on {cols}")
    return out


def orphan_keys(con) -> list[str]:
    out = []
    for fact, col, dim, key in FOREIGN_KEYS:
        n = con.execute(f"SELECT COUNT(*) FROM {fact} f WHERE f.{col} IS NOT NULL "
                        f"AND NOT EXISTS (SELECT 1 FROM {dim} d WHERE d.{key} = f.{col})").fetchone()[0]
        if n:
            out.append(f"{fact}.{col}: {n} row(s) with no match in {dim}")
    return out


class StarSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "test.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            load_sqlite(parse_all(), db)
        cls.con = connect("sqlite", db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_every_fact_has_rows(self):
        for fact in GRAIN:
            with self.subTest(fact=fact):
                self.assertGreater(self.con.execute(f"SELECT COUNT(*) FROM {fact}").fetchone()[0], 0)

    def test_facts_are_unique_at_their_grain(self):
        self.assertEqual(grain_violations(self.con), [])

    def test_every_key_finds_its_dimension(self):
        self.assertEqual(orphan_keys(self.con), [])

    def test_turn_months(self):
        rows = self.con.execute("SELECT turn_month, year, month, month_index FROM dim_turns "
                                "WHERE turn_month IN ('1547-01', '1547-04') ORDER BY 1").fetchall()
        self.assertEqual(rows, [("1547-01", 1547, 1, 1547 * 12), ("1547-04", 1547, 4, 1547 * 12 + 3)])

    def test_office_unchanged_is_one_version(self):
        rows = self.con.execute("SELECT version, office, valid_from, valid_to, is_current "
                                "FROM dim_persona_offices WHERE persona_id = 104").fetchall()
        self.assertEqual(rows, [(1, "MAGISTRATE of Carlisle", "1547-01", None, 1)])

    def test_place_officials(self):
        rows = self.con.execute("SELECT role, holder FROM dim_place_officials "
                                "WHERE place_key = 'carlisle' AND is_current = 1 ORDER BY role").fetchall()
        self.assertIn(("MAGISTRATE", "Donald VERNON"), rows)


class OfficeHistory(unittest.TestCase):
    """Type 2 versioning on made-up history: Donald loses the magistracy in April and regains it
    in May, which must be three versions (a value coming back is still a new version)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "test.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            load_sqlite(parse_all(), db)
        self.con = connect("sqlite", db)
        for date, office in (("1547-04-30", None), ("1547-05-31", "MAGISTRATE of Carlisle")):
            self.con.execute("INSERT INTO turn_persona_status (source_id, game_date, account, player_family, "
                             "persona_id, office) VALUES (?, ?, 'TST', 'VERNON', 104, ?)",
                             (f"turn:TST:{date}", date, office))

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_versions(self):
        rows = self.con.execute("SELECT version, office, valid_from, valid_to, last_seen, is_current "
                                "FROM dim_persona_offices WHERE persona_id = 104 ORDER BY version").fetchall()
        self.assertEqual(rows, [
            (1, "MAGISTRATE of Carlisle", "1547-01", "1547-04", "1547-03", 0),
            (2, None, "1547-04", "1547-05", "1547-04", 0),
            (3, "MAGISTRATE of Carlisle", "1547-05", None, "1547-05", 1),
        ])

    def test_fact_joins_the_version_in_force(self):
        """A fact dated April joins version 2: Donald held no office that month."""
        office = self.con.execute("""
            SELECT o.office FROM dim_persona_offices o
            WHERE o.persona_id = 104
              AND '1547-04' >= o.valid_from AND ('1547-04' < o.valid_to OR o.valid_to IS NULL)""").fetchall()
        self.assertEqual(office, [(None,)])


if __name__ == "__main__":
    unittest.main()

"""The star schema built by the dbt project (renaissance/dbt/). The dbt tests check each fact is
unique at its grain, every key finds its dimension row and categories hold known values; these
check the history in the type 2 dimensions on made-up scenarios, which reads better in Python."""
import tempfile
import unittest
from pathlib import Path

import duckdb

from renaissance.dbt_runner import dbt
from renaissance.load import DATASET, connect

from . import copy_db, fixture_db

FACTS = ["fact_persona_status", "fact_persona_skills", "fact_persona_assets", "fact_presence", "fact_lookups",
         "fact_market_prices", "fact_events", "fact_sieges", "fact_orders", "fact_claims"]


class DbtTests(unittest.TestCase):
    """Its own class, with no connection open: DuckDB won't let dbt write to a file this process is reading."""

    def test_dbt_tests_pass(self):
        """Grain, keys and accepted values (the YAML files in renaissance/dbt/models/)."""
        with tempfile.TemporaryDirectory() as work:
            self.assertTrue(dbt(["--quiet", "test"], fixture_db(), work=Path(work)), "dbt test failed (output above)")


class StarSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = connect(fixture_db())

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_every_fact_has_rows(self):
        for fact in FACTS:
            with self.subTest(fact=fact):
                self.assertGreater(self.con.execute(f"SELECT COUNT(*) FROM {fact}").fetchone()[0], 0)

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
        self.assertIn(("MAGISTRATE", "Eddard STARK"), rows)


class OfficeHistory(unittest.TestCase):
    """Type 2 versioning on made-up history: Eddard loses the magistracy in April and regains it
    in May, which must be three versions (a value coming back is still a new version)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = duckdb.connect(str(copy_db(Path(self.tmp.name))))   # writable: adds two turns
        self.con.execute(f"SET schema = '{DATASET}'")
        for date, office in (("1547-04-30", None), ("1547-05-31", "MAGISTRATE of Carlisle")):
            # _dlt_load_id and _dlt_id: dlt's bookkeeping columns, required on every loaded row
            self.con.execute("INSERT INTO turn_persona_status (source_id, game_date, account, player_family, "
                             "persona_id, office, _dlt_load_id, _dlt_id) "
                             "VALUES (?, ?, 'TST', 'STARK', 104, ?, 'test', ?)",
                             (f"turn:TST:{date}", date, office, f"test-{date}"))

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
        """A fact dated April joins version 2: Eddard held no office that month."""
        office = self.con.execute("""
            SELECT o.office FROM dim_persona_offices o
            WHERE o.persona_id = 104
              AND '1547-04' >= o.valid_from AND ('1547-04' < o.valid_to OR o.valid_to IS NULL)""").fetchall()
        self.assertEqual(office, [(None,)])


if __name__ == "__main__":
    unittest.main()

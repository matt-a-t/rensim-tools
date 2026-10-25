"""End to end on the made-up TST data: parse everything, load DuckDB, build the dbt models, answer every saved question,
and pass the data quality checks. Plus the parser details a new player would trip over first."""
import contextlib
import io
import unittest

from renaissance.ask import load_questions
from renaissance.common import detect_kind, read_text
from renaissance.load import connect
from renaissance.parse_turn import address_block, parse_turn
from renaissance.quality import run_checks
from renaissance.run import parse_all
from renaissance.settings import RAW

from . import FIXTURES, fixture_db


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.tables = parse_all()
        cls.con = connect(fixture_db())

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_every_question_runs(self):
        for name, (_, sql) in load_questions().items():
            with self.subTest(question=name):
                self.con.execute(sql).fetchall()

    def test_my_personae_now(self):
        rows = self.con.execute("SELECT persona_id, given_name, location FROM v_personae_now ORDER BY persona_id").fetchall()
        self.assertEqual([r[1] for r in rows], ["Arya", "Jon", "Sansa", "Eddard"])
        self.assertEqual({r[2] for r in rows}, {"Carlisle"})

    def test_orders_sent_are_loaded(self):
        n = self.con.execute("SELECT COUNT(*) FROM orders_sent WHERE game_month = '1547-04'").fetchone()[0]
        self.assertEqual(n, 7)

    def test_quality_checks_pass(self):
        self.assertEqual({k: v for k, v in run_checks(self.con).items() if v}, {})

    def test_home_distances(self):
        miles = dict(self.con.execute("SELECT name, miles_from_home FROM dim_places").fetchall())
        self.assertEqual(miles["Carlisle"], 0)
        self.assertGreater(miles["Kelso"], 40)


DOWNLOAD = FIXTURES / "downloads" / "tst_1547-01.txt"   # a turn as the GM sends it, address and all


class TurnParser(unittest.TestCase):
    t = parse_turn(RAW / "turns" / "tst_1547-03.txt")

    def test_billing_and_address_are_dropped(self):
        text = repr(parse_turn(DOWNLOAD))
        self.assertNotIn("Example Street", text)
        self.assertNotIn("CREDIT", text)

    def test_address_block(self):
        lines = read_text(DOWNLOAD).split("\n")
        start, end = address_block(lines)
        self.assertTrue(lines[start - 1].startswith("ACTIONS="))
        self.assertTrue(lines[end].startswith("Copyright"))
        self.assertIn("Example Street", "\n".join(lines[start:end]))
        self.assertIsNone(address_block(["ACCOUNT CODE: TST", "ACTIONS= STARK: 1", "no copyright"]))

    def test_detect_kind(self):
        self.assertEqual(detect_kind(RAW / "turns" / "tst_1547-03.txt"), "turn")
        self.assertEqual(detect_kind(RAW / "newsletters" / "NEWSL901"), "newsletter")
        self.assertIsNone(detect_kind(RAW.parent / "aliases" / "families.csv"))

    def test_header(self):
        s = self.t["sources"][0]
        self.assertEqual((s["account"], s["player_family"], s["game_date"], s["real_date"]),
                         ("TST", "STARK", "1547-03-31", "2026-10-01"))

    def test_numbers_and_coordinates(self):
        ducats = {a["persona_id"]: a["quantity"] for a in self.t["persona_assets"] if a["asset"] == "ducats"}
        self.assertEqual(ducats[102], 300_000)
        loc = self.t["location_status"][0]
        self.assertEqual((loc["location"], loc["lat"], loc["lon"]), ("Carlisle", 54.9, -2.9333))

    def test_lookups(self):
        found = {l["place"]: l["is_set_up"] for l in self.t["lookups"]}
        self.assertEqual(found, {"Kelso": True, "Edinburgh": True, "NOWHERESVILLE": False})

    def test_magistrate_and_market(self):
        eddard = next(p for p in self.t["persona_status"] if p["persona_id"] == 104)
        self.assertEqual(eddard["office"], "MAGISTRATE of Carlisle")
        horse = next(m for m in self.t["market"] if m["item"] == "horse")
        self.assertEqual((horse["side"], horse["price"], horse["quantity"]), ("supply", 1, 450))


if __name__ == "__main__":
    unittest.main()

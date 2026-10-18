"""Data quality checks: the made-up TST turns pass, and each kind of broken turn file fails.
The broken files are written to a temp folder from a fixture turn with one deliberate fault each."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from renaissance.load import connect, load_sqlite
from renaissance.normalize import combine, normalize
from renaissance.parse_turn import parse_turn
from renaissance.quality import known_gaps, run_checks
from renaissance.settings import RAW

JAN, FEB, MAR = (RAW / "turns" / f"tst_1547-0{m}.txt" for m in (1, 2, 3))


def read(path: Path) -> str:
    return path.read_bytes().decode("cp1252")


class Quality(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def broken(self, text: str) -> Path:
        path = self.dir / "broken.txt"
        path.write_bytes(text.encode("cp1252"))
        return path

    def problems(self, *turns: Path) -> dict[str, list[str]]:
        db = self.dir / "check.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):   # load_sqlite prints row counts
            load_sqlite(normalize(combine([parse_turn(p) for p in turns])), db)
        con = connect("sqlite", db)
        try:
            return run_checks(con)
        finally:
            con.close()

    def test_good_turns_pass(self):
        self.assertEqual({k: v for k, v in self.problems(JAN, FEB, MAR).items() if v}, {})

    def test_missing_month_fails(self):
        may = self.broken(read(MAR).replace("March 31, 1547", "May 31, 1547", 1))
        self.assertEqual(self.problems(JAN, FEB, may)["check_turn_months"],
                         ["TST has no turn report for 1547-03", "TST has no turn report for 1547-04"])

    def test_duplicate_month_fails(self):
        self.assertIn("TST has 2 turn reports for 1547-03",
                      self.problems(JAN, FEB, MAR, self.broken(read(MAR).replace(
                          "March 31, 1547", "March 30, 1547", 1)))["check_turn_months"])

    def test_truncated_turn_fails_row_counts(self):
        lines = read(MAR).split("\n")
        cut = self.broken("\n".join(lines[:next(i for i, l in enumerate(lines) if l.startswith("Copyright")) + 1]))
        found = self.problems(JAN, FEB, cut)["check_row_counts"]
        self.assertIn("turn:TST:1547-03-31: 0 rows in turn_persona_status, expected 1-12", found)

    def test_zero_price_fails(self):
        text = read(MAR).replace("FOOD                   963", "FOOD                     0", 1)
        self.assertEqual(self.problems(JAN, FEB, self.broken(text))["check_prices"],
                         ["turn_market: turn:TST:1547-03-31 demand food has price 0"])

    def test_unknown_gm_message_fails(self):
        text = read(FEB).rstrip("\n") + "\nVernon         Your courier was lost at sea.\n"
        found = self.problems(JAN, self.broken(text), MAR)["check_unparsed"]
        self.assertEqual(len(found), 1)
        self.assertIn("Your courier was lost at sea.", found[0])


    def test_known_gaps_from_config(self):
        gaps = known_gaps([{"account": "abc", "from": "1546-11", "to": "1547-02", "why": "changed family"}])
        self.assertEqual(sorted(m for _, m in gaps), ["1546-11", "1546-12", "1547-01", "1547-02"])
        self.assertEqual(gaps[("ABC", "1547-01")], "changed family")

    def test_a_known_gap_is_not_a_problem(self):
        # TST's fixture config lists Jul-Dec 1546 as a known gap; a June 1546 turn then January 1547 is fine.
        june = self.broken(read(JAN).replace("January 31, 1547", "June 30, 1546", 1))
        self.assertEqual(self.problems(june, JAN, FEB, MAR)["check_turn_months"], [])


if __name__ == "__main__":
    unittest.main()

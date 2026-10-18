"""The backend comparison hides engine quirks but still catches a changed answer."""
import datetime
import unittest
from decimal import Decimal

from renaissance.compare import diff, normalize_rows


class Compare(unittest.TestCase):
    def test_engine_quirks_are_equal(self):
        duck = [("Hawick", True, Decimal("5.30"), datetime.date(1547, 2, 28), 963),
                ("Kelso", False, 0.30000000000000004, None, 2)]
        lite = [("Kelso", 0, 0.3, None, 2.0), ("Hawick", 1, 5.3, "1547-02-28", 963)]
        self.assertEqual(normalize_rows(duck), normalize_rows(lite))

    def test_a_changed_answer_is_reported(self):
        a = {"q": normalize_rows([("Hawick", 5.3), ("Kelso", 23.9)])}
        b = {"q": normalize_rows([("Hawick", 5.3), ("Kelso", 24.9)])}
        lines = diff(a, b)
        self.assertEqual(lines[0], "== q: 2 row(s) in duckdb, 2 in sqlite")
        self.assertIn("  only in duckdb: Kelso | 23.9", lines)
        self.assertIn("  only in sqlite: Kelso | 24.9", lines)

    def test_integer_division_is_not_hidden(self):
        self.assertNotEqual(normalize_rows([(3,)]), normalize_rows([(3.5,)]))


if __name__ == "__main__":
    unittest.main()

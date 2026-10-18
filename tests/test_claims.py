"""Claims files must be well-formed and must not say more than their sources do (made-up fixture)."""
import copy
import unittest

from renaissance.claims import check_quotes, load_claims, load_family_aliases, read_claim_files, validate


class Claims(unittest.TestCase):
    doc = read_claim_files()[0]

    def test_fixture_is_valid_and_quoted(self):
        self.assertEqual(validate(self.doc), [])
        self.assertEqual(check_quotes(self.doc), [])

    def test_a_misquote_is_caught(self):
        bad = copy.deepcopy(self.doc)
        bad["claims"][0]["quote"] = "Lord Edgar Ostrander now holds the Kingdom of Scotland"
        self.assertEqual(len(check_quotes(bad)), 1)

    def test_bad_type_and_hedge_are_caught(self):
        bad = copy.deepcopy(self.doc)
        bad["claims"][0].update(type="gossip", hedge="certain")
        self.assertEqual(len(validate(bad)), 2)

    def test_rows(self):
        out = load_claims()
        self.assertEqual(len(out["claims"]), 2)
        self.assertEqual(out["claim_market"][0]["price"], 40)
        self.assertEqual(out["sources"][0]["reliability"], "player_claim")

    def test_aliases(self):
        self.assertEqual(load_family_aliases(), {"OSTRANDERS": "OSTRANDER"})


if __name__ == "__main__":
    unittest.main()

"""The order checker against the made-up TST turns: every kind of code parses, mistakes are caught."""
import unittest

from renaissance.orders import check, match_code, send_text

# One of each kind of code (made up for the tests, covering every group).
SAMPLE = """t102grain12 s0foograin300 s2foosheep200 bglass999 bhorse10y pws98 pwq34 pl102 pa103 px104 p-l105
pp1000 pr50 pmw9910 i103m i104k pfDupont pffar ph pi102 ps102 ps pbq2 ptx33 ptf ptg2 pe+Paris pe# peParis pg
pverm5 pverd5 pcx10 aSmith a-Smith n@Smith n%Paris n#Paris llParis gom gcm ggeAlbret gdiFoix ggtFoix gdtFoix
lt+4-3 lx lh lax102 lrm lcd0 lcme ttoValois mdboats md mi mlBesancon* msParis mfChioggia mrParis mg102 ma mo
mto mti d999"""


class Codes(unittest.TestCase):
    def test_every_kind_of_code_parses(self):
        self.assertEqual([t for t in SAMPLE.split() if not match_code(t)], [])

    def test_explanations(self):
        self.assertEqual(match_code("ptx33").explanation, "Recruit 33 auxiliaries")
        self.assertEqual(match_code("i3996k").explanation, "Instruct persona 3996 in assassination")
        self.assertEqual(match_code("lt+4-3").explanation, "Change tariffs: import +4%, export -3%")
        self.assertEqual(match_code("mlCarlisle*").group, 5)


class Checking(unittest.TestCase):
    def test_valid_orders_are_clean(self):
        rep = check("/TST *VERNON\n*Carlisle .101 i102l mlEdinburgh*\n*Carlisle .102 i104c pffar\n")
        self.assertEqual(rep.errors, [])

    def test_mistakes_are_caught(self):
        rep = check("/TST *VERNON\n*Edinburgh .102 pffar\n*Carlisle .999 lx\n"
                    "*Carlisle .101 s0clobows500 zzz9\t\n")
        text = " ".join(rep.errors)
        for expected in ("starts in Carlisle", "isn't one of yours", "can't sell 500", "isn't a valid order code", "tab"):
            self.assertIn(expected, text)

    def test_group_order_warning(self):
        rep = check("/TST *VERNON\n*Carlisle .103 mlCarlisle pffar\n")
        self.assertTrue(any("comes after a movement/battle order" in w for w in rep.warnings))

    def test_send_strips_notes(self):
        self.assertEqual(send_text("# note\n/TST *VERNON\n\n*Carlisle .103  pffar   # why\n"),
                         "/TST *VERNON\n*Carlisle .103 pffar\n")


class Cargo(unittest.TestCase):
    # Colin (103) holds 600 bows and no horses on the March 1547 report.
    def test_goods_without_horses_cannot_move(self):
        rep = check("/TST *VERNON\n*Carlisle .103 mlKelso*\n")
        self.assertTrue(any("600 cargo" in e for e in rep.errors), rep.errors)

    def test_leaving_goods_behind_fixes_it(self):
        rep = check("/TST *VERNON\n*Carlisle .103 t104bows600 mlKelso*\n")
        self.assertEqual(rep.errors, [])

    def test_buying_horses_fixes_it(self):
        rep = check("/TST *VERNON\n*Carlisle .103 bhorse300 mlKelso*\n")
        self.assertEqual(rep.errors, [])

    def test_buying_horses_cannot_carry_more(self):
        rep = check("/TST *VERNON\n*Carlisle .103 bhorse299 mlKelso*\n")
        self.assertTrue(rep.errors)


class SavedOrders(unittest.TestCase):
    def test_saved_orders_load(self):
        from renaissance.orders import parse_orders_file
        from renaissance.settings import ORDERS_DIR
        out = parse_orders_file(ORDERS_DIR / "tst_1547-04.txt")
        rows = out["orders_sent"]
        self.assertEqual([r["code"] for r in rows if r["order_group"] is None], [])
        moves = {r["persona_id"]: r["code"] for r in rows if r["order_group"] == "movement/battle"}
        self.assertEqual(moves, {101: "mlKelso*", 102: "mlEdinburgh*", 103: "mlKelso*"})
        self.assertEqual((rows[0]["account"], rows[0]["family"], rows[0]["game_month"]), ("TST", "VERNON", "1547-04"))
        self.assertIn("Donald stays home", out["sources"][0]["notes"])

    def test_saved_orders_pass_the_checker(self):
        from renaissance.settings import ORDERS_DIR
        rep = check((ORDERS_DIR / "tst_1547-04.txt").read_text(encoding="utf-8"))
        self.assertEqual(rep.errors, [])
        self.assertFalse(any("database already has co-ordinates" in w for w in rep.warnings))

    def test_wrong_account_is_caught(self):
        rep = check("/AMP *VERNON\n*Carlisle .101 mlKelso*\n")
        self.assertTrue(any("your account is TST" in e for e in rep.errors), rep.errors)

    def test_fee_estimate_uses_my_bills(self):
        from renaissance.orders import last_fee_per_action
        self.assertEqual(last_fee_per_action(), 0.5)

if __name__ == "__main__":
    unittest.main()


class Training(unittest.TestCase):
    def test_plan_follows_the_rules(self):
        from renaissance.training import plan
        _, personae, orders = plan()
        pairs = [(o["trainer"]["persona_id"], o["trainee"]["persona_id"]) for o in orders]
        self.assertEqual(len(pairs), len(set(pairs)), "a pair trains at most once per turn")
        for o in orders:
            self.assertGreater(o["gain"], 0)
            self.assertEqual(o["trainer"]["location"], o["trainee"]["location"])

    def test_checker_rejects_a_repeated_pair(self):
        rep = check("/TST *VERNON\n*Carlisle .103 i104c i104a\n")
        self.assertTrue(any("once per pair" in e for e in rep.errors))

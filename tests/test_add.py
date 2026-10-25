"""rensim add: fixture files are checked, named from what's inside them, have the mailing address removed
and are saved under a temp data/raw."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from renaissance import add, orders
from renaissance.add import ADDRESS_REMOVED, Refused, add_file
from renaissance.parse_turn import parse_turn
from renaissance.settings import RAW

from . import FIXTURES

TURNS = sorted((RAW / "turns").glob("*.txt"))
JAN = FIXTURES / "downloads" / "tst_1547-01.txt"   # as the GM sends it, address and all
NEWS = RAW / "newsletters" / "NEWSL901"


class Add(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.raw = self.dir / "raw"

    def tearDown(self):
        self.tmp.cleanup()

    def download(self, data: bytes, name: str = "download.txt") -> Path:
        """A file as it might arrive: any name, outside the data folder."""
        path = self.dir / name
        path.write_bytes(data)
        return path

    def add(self, path: Path, force: bool = False, keep_address: bool = False) -> tuple[Path, bool]:
        with contextlib.redirect_stdout(io.StringIO()):
            return add_file(path, self.raw, force, keep_address)

    def main(self, *argv: str, stdin: bytes = b"") -> tuple[int, str]:
        out = io.StringIO()
        stdin = type("Stdin", (), {"buffer": io.BytesIO(stdin)})()
        with patch.object(add, "RAW", self.raw), patch.object(add.sys, "stdin", stdin), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return add.main(list(argv)), out.getvalue()

    def test_turn_is_named_from_its_account_and_month(self):
        dest, written = self.add(self.download(JAN.read_bytes(), "turn (3).txt"))
        self.assertEqual(dest, self.raw / "turns" / "tst_1547-01.txt")
        self.assertTrue(written)

    def test_newsletter_is_named_from_its_issue(self):
        dest, _ = self.add(self.download(NEWS.read_bytes()))
        self.assertEqual(dest, self.raw / "newsletters" / "NEWSL901")

    def test_identical_file_is_left_alone(self):
        self.add(self.download(JAN.read_bytes()))
        _, written = self.add(self.download(JAN.read_bytes(), "again.txt"))
        self.assertFalse(written)

    def test_different_file_needs_force(self):
        self.add(self.download(JAN.read_bytes()))
        changed = self.download(JAN.read_bytes() + b"\r\nA GM message added later\r\n", "changed.txt")
        with self.assertRaisesRegex(Refused, "already exists"):
            self.add(changed)
        dest, written = self.add(changed, force=True)
        self.assertTrue(written)
        self.assertIn(b"A GM message added later", dest.read_bytes())

    def test_address_is_removed_and_fees_kept(self):
        dest, _ = self.add(JAN)
        saved = dest.read_bytes().decode("cp1252")
        self.assertNotIn("Example Street", saved)
        self.assertNotIn("Samwell", saved)
        self.assertNotIn("CREDIT", saved)
        self.assertIn("TOTAL ACTION FEES:    5.000\r\n", saved)
        self.assertIn("NET PRIOR BALANCE:", saved)
        self.assertIn(f"{ADDRESS_REMOVED}\r\nCopyright", saved)
        original = JAN.read_bytes().decode("cp1252")
        self.assertEqual(saved[saved.index("Copyright"):], original[original.index("Copyright"):])

    def test_saved_turn_is_the_fixture(self):
        dest, _ = self.add(JAN)
        self.assertEqual(dest.read_bytes(), (RAW / "turns" / JAN.name).read_bytes())

    def test_turn_parses_the_same_without_its_address(self):
        dest, _ = self.add(JAN)   # same file name, so even sources.file_name matches
        before, after = parse_turn(JAN), parse_turn(dest)
        # Only the line numbers of unread lines move up, by the address lines taken out.
        for row in before["unparsed_lines"] + after["unparsed_lines"]:
            row.pop("line_no")
        self.assertEqual(after, before)

    def test_removing_the_address_twice_changes_nothing(self):
        dest, _ = self.add(JAN)
        _, written = self.add(self.download(dest.read_bytes()))
        self.assertFalse(written)

    def test_keep_address(self):
        dest, _ = self.add(JAN, keep_address=True)
        self.assertEqual(dest.read_bytes(), JAN.read_bytes())

    def test_no_copyright_line_warns_and_keeps_the_file(self):
        data = JAN.read_bytes().replace(b"Copyright notice", b"Notice")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dest, _ = add_file(self.download(data), self.raw)
        self.assertIn("address wasn't found", out.getvalue())
        self.assertEqual(dest.read_bytes(), data)

    def test_fee_estimate_still_works_on_added_turns(self):
        expected = orders.last_fee_per_action()
        self.assertIsNotNone(expected)
        for turn in TURNS:
            self.add(turn)
        with patch.object(orders, "TURNS", self.raw / "turns"):
            self.assertEqual(orders.last_fee_per_action(), expected)

    def test_unknown_format_is_refused(self):
        with self.assertRaisesRegex(Refused, "not a turn result"):
            self.add(self.download(b"Dear GM, here are my orders"))
        self.assertFalse(self.raw.exists())

    def test_unreadable_turn_is_refused(self):
        with self.assertRaisesRegex(Refused, "turn parser couldn't read it"):
            self.add(self.download(b"ACCOUNT CODE: TST sometime last year\r\n"))

    def test_other_account_warns_but_is_saved(self):
        ally = self.download(JAN.read_bytes().replace(b"ACCOUNT CODE: TST", b"ACCOUNT CODE: ALY"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dest, _ = add_file(ally, self.raw)
        self.assertEqual(dest.name, "aly_1547-01.txt")
        self.assertIn("isn't yours (TST)", out.getvalue())

    def test_stdin(self):
        code, out = self.main("-", stdin=JAN.read_bytes())
        self.assertEqual(code, 0)
        self.assertIn("saved as", out)
        self.assertIn("rensim run", out)
        self.assertTrue((self.raw / "turns" / "tst_1547-01.txt").exists())

    def test_one_bad_file_fails_the_command_but_not_the_others(self):
        code, out = self.main(str(self.download(b"not a game file", "notes.txt")), str(NEWS),
                              str(self.dir / "missing.txt"))
        self.assertEqual(code, 1)
        self.assertIn("not added", out)
        self.assertIn("no such file", out)
        self.assertTrue((self.raw / "newsletters" / "NEWSL901").exists())

    def test_run_only_when_something_new_was_saved(self):
        self.add(self.download(JAN.read_bytes()))
        with patch("renaissance.run.main") as run:
            code, out = self.main(str(JAN), "--run")
        run.assert_not_called()
        self.assertIn("already saved, identical", out)
        with patch("renaissance.run.main", return_value=0) as run:
            self.main(str(NEWS), "--run")
        run.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()

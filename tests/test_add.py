"""rensim add: fixture files are checked, named from what's inside them and saved under a temp data/raw."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from renaissance import add
from renaissance.add import Refused, add_file
from renaissance.settings import RAW

JAN = RAW / "turns" / "tst_1547-01.txt"
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

    def add(self, path: Path, force: bool = False) -> tuple[Path, bool]:
        with contextlib.redirect_stdout(io.StringIO()):
            return add_file(path, self.raw, force)

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
        self.assertEqual(dest.read_bytes(), JAN.read_bytes())

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
        self.assertEqual(dest.read_bytes(), changed.read_bytes())

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

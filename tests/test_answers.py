"""Every saved question gives the same answer on the fixtures as it did before a refactor.

The expected answers live in tests/fixtures/expected_answers.json. After a change that is meant to
alter an answer, regenerate them and review the diff:

    UPDATE_ANSWERS=1 python -m unittest tests.test_answers
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from renaissance.ask import load_questions
from renaissance.load import connect, load_sqlite
from renaissance.run import parse_all

from . import FIXTURES

EXPECTED = FIXTURES / "expected_answers.json"


def answers(con) -> dict[str, list[list]]:
    """name -> rows, sorted so a question without ORDER BY still compares the same."""
    out = {}
    for name, (_, sql) in load_questions().items():
        rows = [list(r) for r in con.execute(sql).fetchall()]
        out[name] = sorted(rows, key=repr)
    return out


class SavedAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "test.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            load_sqlite(parse_all(), db)
        con = connect("sqlite", db)
        cls.got = answers(con)
        con.close()
        if os.environ.get("UPDATE_ANSWERS"):
            EXPECTED.write_text(json.dumps(cls.got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_answers(self):
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.got), sorted(expected), "questions added or removed")
        for name, rows in expected.items():
            with self.subTest(question=name):
                self.assertEqual(self.got[name], rows)

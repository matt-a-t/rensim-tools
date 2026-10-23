"""Drafting claims with the Claude API (a fake client: the tests never call the API) and scoring
drafts against a hand-written claims file, on a made-up publication."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from renaissance import llm_claims
from renaissance.claims import check_quotes, validate
from renaissance.llm_claims import extract, overlap, score_claims

from . import FIXTURES

GOLD_PATH = FIXTURES / "eval" / "test_herald.json"
GOLD = json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def reply_claim(id, type, quote, hedge="asserted", data_json=None, **kw):
    """One claim as Claude returns it (SCHEMA's shape: every field present, nulls for blanks)."""
    subject = {"name": None, "given": None, "family": None, "place": "Kelso"} | kw.pop("subject", {})
    return {"id": id, "type": type, "subject": subject, "predicate": "p", "object": "o", "place": None,
            "game_date": None, "hedge": hedge, "claimed_by": None, "quote": quote, "refs": [],
            "data_json": data_json} | kw


REPLY = {
    "source": {"kind": "player_newsletter", "title": "The King's Landing Herald", "publisher": "Petyr Baelish",
               "publisher_family": "BAELISH", "game_date": "1547-03", "notes": None},
    "claims": [
        reply_claim("c1", "office", "Lord Eddard Stark, Hand of the King",
                    subject={"name": "Eddard Stark", "given": "Eddard", "family": "STARK", "place": None}),
        reply_claim("c2", "action", "rode to Kelso and granted lands"),               # not in the text
        reply_claim("c3", "action", "It is said that Lord Stannis", hedge="certain"),  # not a hedge
        reply_claim("c4", "location_report", "the market at Kelso buys horses", data_json="{not json"),
        reply_claim("c5", "location_report", "the market at Kelso buys horses for 120 ducats", hedge="reported",
                    data_json='{"demands": [{"item": "horses", "bq": 1.0, "price": 120}]}'),
    ],
}


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, streams back a canned reply."""
    def __init__(self, reply, stop_reason="end_turn"):
        self.requests = []
        message = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(reply))])
        stream = SimpleNamespace(get_final_message=lambda: message)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: self.requests.append(kw) or contextlib.nullcontext(stream)))


class Extract(unittest.TestCase):
    def test_bad_claims_are_rejected(self):
        doc, rejected = extract("player_publications/test_herald.pdf", FakeClient(REPLY))
        self.assertEqual([c["id"] for c in doc["claims"]], ["c1", "c5"])
        self.assertEqual(len(rejected), 3)
        self.assertIn("c2: quote not in source", rejected[0])
        self.assertIn("unknown hedge", rejected[1])
        self.assertIn("not valid JSON", rejected[2])
        # What's kept passes the same checks as `rensim claims`.
        self.assertEqual(validate(doc), [])
        self.assertEqual(check_quotes(doc), [])

    def test_draft_reads_like_a_hand_written_file(self):
        doc, _ = extract("player_publications/test_herald.pdf", FakeClient(REPLY), {"real_date": "2026-10-20"})
        self.assertEqual(doc["source"], {
            "source_id": "pub:test_herald", "file": "player_publications/test_herald.pdf",
            "kind": "player_newsletter", "title": "The King's Landing Herald", "publisher": "Petyr Baelish",
            "publisher_family": "BAELISH", "game_date": "1547-03", "real_date": "2026-10-20"})
        c1, c5 = doc["claims"]
        self.assertEqual(c1["subject"], {"name": "Eddard Stark", "given": "Eddard", "family": "STARK"})
        self.assertNotIn("place", c1)
        self.assertNotIn("refs", c1)
        self.assertEqual(c5["data"]["demands"][0]["price"], 120)

    def test_request_follows_the_guide(self):
        client = FakeClient(REPLY)
        extract("player_publications/test_herald.pdf", client)
        request = client.requests[0]
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertIn("# Extracting claims from player publications", request["system"])
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertIn("granted lands to the Night's Watch", request["messages"][0]["content"])

    def test_a_refusal_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            extract("player_publications/test_herald.pdf", FakeClient(REPLY, stop_reason="refusal"))

    def test_command_writes_a_draft_and_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(llm_claims, "DRAFTS", Path(tmp)), \
                patch.object(llm_claims, "make_client", lambda: FakeClient(REPLY)), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(llm_claims.main(["player_publications/test_herald.pdf"]), 0)
            draft = json.loads((Path(tmp) / "test_herald.json").read_text(encoding="utf-8"))
            self.assertEqual(len(draft["claims"]), 2)
            self.assertIn("2 claim(s) written", out.getvalue())
            self.assertEqual(llm_claims.main(["player_publications/test_herald.pdf"]), 1)   # needs --force
            self.assertEqual(llm_claims.main(["player_publications/test_herald.pdf", "--force"]), 0)


class Score(unittest.TestCase):
    def test_gold_is_valid_and_quoted(self):
        self.assertEqual(validate(GOLD), [])
        self.assertEqual(check_quotes(GOLD), [])

    def test_a_copy_scores_perfectly(self):
        r = score_claims(GOLD, GOLD)
        self.assertEqual((r["precision"], r["recall"], r["hedges_agree"]), (1.0, 1.0, 6))

    def test_overlap(self):
        self.assertEqual(overlap("Stannis Baratheon has taken", "It is said that Lord Stannis  Baratheon has taken Dragonstone"), 1.0)
        self.assertAlmostEqual(overlap("buys horses at Kelso", "buys wool at Kelso"), 0.6)
        self.assertEqual(overlap("Hand of the King", "a market at Kelso"), 0.0)

    def test_misses_extras_and_wrong_types(self):
        draft = copy.deepcopy(GOLD)
        c1, c2, c3, c4, c5, c6 = draft["claims"]
        c1["type"] = "family"                                         # wrong type: no match
        c3["quote"] = "Lord Stannis Baratheon has taken Dragonstone"   # shorter quote still matches
        c4["hedge"] = "asserted"                                      # matched, hedge differs
        extra = {"id": "c9", "type": "action", "hedge": "asserted", "quote": "Our factors report"}
        draft["claims"] = [c1, c2, c3, c4, c5, extra]                 # c6 missed
        r = score_claims(draft, GOLD)
        self.assertEqual(r["precision"], 4 / 6)
        self.assertEqual(r["recall"], 4 / 6)
        self.assertEqual(r["hedges_agree"], 3)
        self.assertEqual({c["id"] for c in r["false_positives"]}, {"c1", "c9"})
        self.assertEqual({c["id"] for c in r["false_negatives"]}, {"c1", "c6"})

    def test_the_rumor_and_its_denial_pair_up_one_to_one(self):
        draft = {"claims": [c for c in GOLD["claims"] if c["id"] in ("c4", "c5")]}
        r = score_claims(draft, GOLD)
        self.assertEqual([(d["id"], g["id"]) for d, g in r["matched"]], [("c4", "c4"), ("c5", "c5")])

    def test_eval_command_with_a_draft(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(llm_claims.evaluate([str(GOLD_PATH), "--draft", str(GOLD_PATH)]), 0)
        self.assertIn("precision 1.00  recall 1.00  hedges agree 6 of 6", out.getvalue())

    def test_eval_command_extracts_without_a_draft(self):
        with patch.object(llm_claims, "make_client", lambda: FakeClient(REPLY)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(llm_claims.evaluate([str(GOLD_PATH)]), 0)
        # The fake keeps c1 (office) and c5 (location_report): both match, and nothing else is drafted.
        self.assertIn("precision 1.00  recall 0.33", out.getvalue())
        self.assertIn("missed: c2 action", out.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Entity resolution: Splink proposes look-alike names, measured against a made-up labelled sample.
The sample has the usual OCR and spelling slips in player publications, and the trap from a real
game: HABSBURG, HABSBOURG and HAPSBOURG all hold towers in one city, so they must never pair."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import splink  # noqa: F401
except ImportError:
    splink = None

from renaissance import claims
from renaissance.load import connect
from renaissance.match import apply, pair_key, precision_recall, propose, read_sightings
from renaissance.normalize import normalize

from . import fixture_db


def family(name, place, gm=True, rows=3):
    return [{"name": name, "place": place, "gm": gm}] * rows


def place(key, spelling, lat=None, lon=None, gm=True, rows=3):
    return [{"name": key, "spelling": spelling, "gm": gm, "lat": lat, "lon": lon}] * rows


def persona(given, fam, where, pid=None, gm=True, rows=2):
    return [{"persona_id": pid, "given": given, "family": fam, "place": where, "gm": gm}] * rows


GM_FAMILIES = [("LANNISTER", "kelso"), ("STARK", "carlisle"), ("TYRELL", "hexham"), ("ARRYN", "berwick"),
               ("BOLTON", "dumfries"), ("GREYJOY", "pyke"), ("MEDICI", "florence"), ("SFORZA", "milan"),
               ("BORGIA", "rome"), ("ORSINI", "rome"), ("COLONNA", "rome"), ("ESTE", "ferrara"),
               ("GONZAGA", "mantua"), ("HABSBURG", "vienna"), ("HABSBOURG", "vienna"), ("HAPSBOURG", "vienna")]
FAMILIES = [s for name, at in GM_FAMILIES for s in family(name, at)] + [
    # spellings only player publications use
    *family("LANISTER", "kelso", gm=False, rows=1), *family("STARKE", "carlisle", gm=False, rows=1),
    *family("TYREL", "hexham", gm=False, rows=1), *family("GRAYJOY", "pyke", gm=False, rows=1),
    *family("C0LONNA", "rome", gm=False, rows=1), *family("BORGLA", "rome", gm=False, rows=1),
    *family("MEDINA", "madrid", gm=False, rows=1),
]
PLACES = [
    *place("edinburgh", "Edinburgh", 55.95, -3.19), *place("kelso", "Kelso", 55.60, -2.43),
    *place("carlisle", "Carlisle", 54.89, -2.93), *place("newcastle", "Newcastle", 54.97, -1.61),
    *place("jedburgh", "Jedburgh", 55.48, -2.55), *place("dumfries", "Dumfries", 55.07, -3.61),
    *place("berwick", "Berwick", 55.77, -2.00), *place("hexham", "Hexham", 54.97, -2.10),
    *place("durham", "Durham", 54.78, -1.57), *place("york", "York", 53.96, -1.08),
    *place("edinburg", "Edinburg", gm=False, rows=1), *place("kelsoe", "Kelsoe", gm=False, rows=1),
    *place("carlile", "Carlile", gm=False, rows=1), *place("dumfreis", "Dumfreis", 55.07, -3.61, gm=False, rows=1),
    *place("newark", "Newark", 53.07, -0.81, gm=False, rows=1),
]
PERSONAE = [
    *persona("Arya", "STARK", "carlisle", 101), *persona("Jon", "STARK", "carlisle", 102),
    *persona("Sansa", "STARK", "carlisle", 103), *persona("Eddard", "STARK", "kelso", 104),
    *persona("Tyrion", "LANNISTER", "kelso", 201), *persona("Jaime", "LANNISTER", "jedburgh", 202),
    *persona("Cersei", "LANNISTER", "jedburgh", 203), *persona("Loras", "TYRELL", "hexham", 301),
    # named without an ID: a GM newsletter (spelt right) and player publications (not always)
    *persona("Sansa", "STARK", "durham", rows=1), *persona("Loras", "TYRELL", "hexham", rows=1),
    *persona("Tirion", "LANNISTER", "kelso", gm=False, rows=1), *persona("Jamie", "LANNISTER", None, gm=False, rows=1),
    *persona("Aria", "STARK", "carlisle", gm=False, rows=1), *persona("Jory", "STARK", "carlisle", gm=False, rows=1),
]


def label(kind, name, match, is_match, persona_id=""):
    return {"kind": kind, "name": name, "match": match, "persona_id": str(persona_id), "is_match": str(is_match)}


LABELS = [
    label("family", "LANISTER", "LANNISTER", 1), label("family", "STARKE", "STARK", 1),
    label("family", "TYREL", "TYRELL", 1), label("family", "GRAYJOY", "GREYJOY", 1),
    label("family", "C0LONNA", "COLONNA", 1), label("family", "BORGLA", "BORGIA", 1),
    label("family", "HABSBURG", "HABSBOURG", 0), label("family", "HABSBOURG", "HAPSBOURG", 0),
    label("family", "HABSBURG", "HAPSBOURG", 0), label("family", "MEDINA", "MEDICI", 0),
    label("place", "Edinburg", "Edinburgh", 1), label("place", "Kelsoe", "Kelso", 1),
    label("place", "Carlile", "Carlisle", 1), label("place", "Dumfreis", "Dumfries", 1),
    label("place", "Newark", "Newcastle", 0), label("place", "Jedburgh", "Edinburgh", 0),
    label("persona", "STARK, Sansa", "", 1, 103), label("persona", "TYRELL, Loras", "", 1, 301),
    label("persona", "LANNISTER, Tirion", "", 1, 201), label("persona", "LANNISTER, Jamie", "", 1, 202),
    label("persona", "STARK, Aria", "", 1, 101), label("persona", "LANNISTER, Tirion", "", 0, 202),
    label("persona", "STARK, Jory", "", 0, 102), label("persona", "STARK, Jory", "", 0, 104),
]


@unittest.skipUnless(splink, 'needs Splink: pip install "rensim-tools[match]"')
class LabelledSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proposals = propose("family", FAMILIES) + propose("place", PLACES) + propose("persona", PERSONAE)
        cls.result = precision_recall(cls.proposals, LABELS)

    def test_measured(self):
        # The measurement, not a target: if a change to the model moves it, update these and say why.
        r = self.result
        print(f"\n  labelled sample: precision {r['precision']:.2f}, recall {r['recall']:.2f}, "
              f"false positives {r['false_positives']}, false negatives {r['false_negatives']}")
        self.assertEqual((round(r["precision"], 2), round(r["recall"], 2)), (1.0, 1.0))

    def test_two_gm_spellings_never_pair(self):
        habsburgs = {"HABSBURG", "HABSBOURG", "HAPSBOURG"}
        self.assertEqual([p for p in self.proposals if {p["name"], p["match"]} <= habsburgs], [])
        self.assertFalse([p for p in self.proposals if p["kind"] == "place" and {p["name"], p["match"]} == {"Jedburgh", "Edinburgh"}])

    def test_gm_spelling_is_canonical(self):
        lan = next(p for p in self.proposals if p["kind"] == "family" and "LANISTER" in (p["name"], p["match"]))
        self.assertEqual((lan["name"], lan["match"]), ("LANISTER", "LANNISTER"))

    def test_rejected_pairs_stay_rejected(self):
        rejected = {pair_key(label("family", "LANNISTER", "LANISTER", 0))}
        self.assertFalse([p for p in propose("family", FAMILIES, rejected=rejected) if "LANISTER" in (p["name"], p["match"])])


@unittest.skipUnless(splink, 'needs Splink: pip install "rensim-tools[match]"')
class OnTheFixtures(unittest.TestCase):
    def test_newsletter_actor_matches_her_id(self):
        con = connect(fixture_db())
        try:
            sightings = read_sightings(con)
        finally:
            con.close()
        self.assertIn("LANNISTER", {s["name"] for s in sightings["family"]})
        self.assertIn("kelso", {s["name"] for s in sightings["place"]})
        found = propose("persona", sightings["persona"])
        self.assertIn(("STARK, Sansa", "103"), [(p["name"], str(p["persona_id"])) for p in found])


class Apply(unittest.TestCase):
    def test_decisions_move_to_their_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows = [{"kind": "family", "name": "LANISTER", "match": "LANNISTER", "decision": "y"},
                    {"kind": "place", "name": "Kelsoe", "match": "Kelso", "decision": "Y"},
                    {"kind": "persona", "name": "LANNISTER, Tirion", "match": "LANNISTER, Tyrion",
                     "persona_id": "201", "decision": "yes"},
                    {"kind": "family", "name": "HABSBURG", "match": "HABSBOURG", "decision": "n"},
                    {"kind": "family", "name": "TYREL", "match": "TYRELL", "decision": ""}]
            with (folder / "proposed.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, ["kind", "name", "match", "persona_id", "probability", "why", "decision"])
                w.writeheader()
                w.writerows(rows)
            self.assertEqual(apply(folder), (3, 1, 1))
            read = lambda name: (folder / name).read_text().splitlines()
            self.assertEqual(read("families.csv"), ["alias,canonical", "LANISTER,LANNISTER"])
            self.assertEqual(read("places.csv"), ["alias,canonical", "Kelsoe,Kelso"])
            self.assertEqual(read("personae.csv"), ["given,family,persona_id", "Tirion,LANNISTER,201"])
            self.assertEqual(read("rejected.csv"), ["kind,name,match,persona_id", "family,HABSBURG,HABSBOURG,"])
            self.assertEqual(len(read("proposed.csv")), 2)   # header + the undecided TYREL row
            self.assertEqual(apply(folder), (0, 0, 1))        # applying again changes nothing

    def test_normalize_uses_accepted_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "places.csv").write_text("alias,canonical\nKelsoe,Kelso\n")
            (folder / "personae.csv").write_text("given,family,persona_id\nTirion,LANNISTER,201\n")
            tables = {"claims": [{"subject_given": "tirion", "subject_family": "LANNISTER", "place": "Kelsoe",
                                  "subject_place": None}],
                      "turn_market": [{"location": "Kelso"}]}
            with patch.object(claims, "PLACE_ALIASES", folder / "places.csv"), \
                 patch.object(claims, "PERSONA_ALIASES", folder / "personae.csv"):
                out = normalize(tables)
        self.assertEqual(out["claims"][0]["place_key"], "kelso")
        self.assertEqual(out["claims"][0]["subject_persona_id"], 201)
        self.assertEqual([(p["place_key"], p["spellings"]) for p in out["dim_places"]], [("kelso", "Kelso | Kelsoe")])


if __name__ == "__main__":
    unittest.main()

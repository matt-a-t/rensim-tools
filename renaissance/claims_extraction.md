# Extracting claims from player publications

Player newsletters and proclamations are propaganda: written in character, often hedged
("it is said", "reportedly"), sometimes false on purpose. So nothing in them is stored as fact.
Each statement becomes a **claim**: who said what, about whom, how confidently, quoted from the source.

This is the guide Claude follows to draw claims from a publication the player shared. Claims
are saved to `data/claims/<source>.json`, one file per publication.

## Workflow

1. `intake` (pasted or attached text) or `intake_inbox` (a file in inbox/) saves the
   publication and its text. For a photo or scan, pass the text you read from it, word for word:
   that text is what quotes are checked against.
2. Draw every claim from the text following the rules below.
3. `save_claims` with the `file` intake returned, the `source` fields and the claims. It refuses
   the lot if any quote isn't word for word in the text (whitespace aside) or a field is missing,
   so claims can't say more than the page does. Fix what it lists and send them all again.
4. Tell the player in a sentence or two what the publication claims and how firmly.

## File format

```json
{
  "source": {
    "source_id": "pub:grimani:147-3",
    "file": "player_publications/grimani_1547-03.pdf",
    "kind": "player_newsletter | proclamation | player_letter",
    "title": "Grimani Newsletter, Vol. 147 No. 3",
    "publisher": "David B. Sweet (House Grimani)",
    "publisher_family": "GRIMANI",
    "game_date": "1547-03",
    "real_date": "2026-10-03",
    "notes": "how it was received, motto, anything that bears on reliability"
  },
  "claims": [ { ...one object per claim, below... } ]
}
```

### A claim

| Field | Meaning |
|---|---|
| `id` | short unique id within the file: `c1`, `c2`, ... |
| `type` | one of the types below |
| `subject` | who or what the claim is about: `{"name": "Giovanni Grimani", "given": "Giovanni", "family": "GRIMANI"}`, or `{"name": "Landshut", "place": "Landshut"}` |
| `predicate` | short verb phrase in snake_case: `granted_indulgences`, `holds_office`, `captured` |
| `object` | what the predicate points at, as plain text (`"Archbishop of Narbonne"`) |
| `place` | place the claim concerns, if any, as written in the source |
| `game_date` | when it happened, in game time (`1547-02`, `1518-06`); default the issue's month |
| `hedge` | how the source states it: `asserted` (plain statement), `reported` ("reportedly", "sources indicated"), `rumored` ("it is said", "noised abroad"), `denied` (the source says it is false) |
| `claimed_by` | who is making the claim if not the publisher (e.g. the proclamation's issuer) |
| `quote` | verbatim words from the source that support the claim (whitespace may differ) |
| `refs` | other documents the claim cites, e.g. `["Renaissance Newsletter #306"]` |
| `data` | optional structured detail (coordinates, officials, market figures) |

### Types

- `office` — someone holds (or held) an office or title: archbishop, magistrate, baron, king.
- `title_dispute` — a title is granted, revoked, contested or called false.
- `action` — someone did something this month: granted indulgences, captured a city.
- `history` — a past event the source recounts (with its own `game_date`).
- `family` — a fact about a family as a whole: origin, members, holdings.
- `heraldry` — a shield / coat of arms description.
- `location_report` — reported facts about a place: coordinates, officials, population,
  port depth, tariffs, market prices. Put the numbers in `data`.

### Rules

- One claim per fact. "X, Archbishop of Y, granted indulgences" is two claims:
  an `office` claim and an `action` claim.
- Keep names as spelled in the source; families in CAPITALS. Spelling differences
  are resolved later through `data/aliases/` (`propose_matches`; the player says yes or no),
  not by editing. Look-alikes aren't always one family: Habsburg, HABSBOURG and HAPSBOURG are three.
- The hedge follows the source's own words, not your opinion of the source.
- When a source reports someone else's claim in order to deny it (the proclamation), record both:
  the reported claim with `hedge: "rumored"` and the denial as its own claim with `hedge: "denied"`.
- Don't invent persona IDs, dates or families the source doesn't give.

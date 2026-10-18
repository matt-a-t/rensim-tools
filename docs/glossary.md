# Glossary

Terms that appear in turn reports, newsletters and the database, checked against the
GM's January 1547 Player Communication (not included here: ask the GM for the current rules).
Line numbers below point into its text.

| Term | Meaning | Where it shows up |
|---|---|---|
| **FAS** | Fortification against assault. Towers have only FAS; garrisons start each turn at 10% of it. (rules ~l.915) | `turn_location_status.fas`, GM tower-capture events |
| **FAR** | Fortification against artillery. Bombardment only damages walls when the gunner's rating beats FAR. (~l.888) | `turn_location_status.far` |
| **BQ** | Base quantity: market quantities are BQ × population, not units on hand. Grimani's city reports quote BQ too. (~l.539) | `turn_market.bq`, `claim_market.bq` |
| **T.** | Troops a persona has with him, in the "personae inside/outside" list. Matches each character's auxiliaries in his own block. | `turn_presence.troops` |
| **Ingress** | The right to enter a location. Without it, entry requires a successful attack, and the external market may be closed to you. (~l.721) | `turn_location_ingress` |
| **Sovereign / Feudatory / Tenant / Magistrate / Bishop** | The layers of control over a location, from overlord down to the local garrison holder and elected or appointed magistrate. (~l.593) | `turn_location_officials` |
| **Feudal elevation** | HRE, Pope, kings and sovereign dukes may each raise personae one feudal rank a year. This is why a false HRE grant (the Upper Alsace proclamation) matters. (~l.63) | `claims` of type `title_dispute` |
| **Morale, popularity** | Persona ratings, 0–999 like all ratings. Popularity affects troop tenacity and dealings with non-player locals. | `turn_persona_status` |
| **Wealth, loyalty** | Location ratings that rise when market demand is satisfied; feudal levies cost 10 loyalty. (~l.73, l.255) | `turn_location_status` |
| **Coordinates** | Degrees.minutes, so `N55.57` = 55°57' = 55.95°. Confirmed against Edinburgh, Ayr and Carlisle in your lookups, and Landshut in the Grimani newsletter, all within a few miles of their real positions. Request any location's with order `N%<location>`. | `lat`/`lon` columns, `dim_places` |
| **Persona ID** | The number in `Alan( 101)VERNON`. Stable across turns, so it's the key for characters. | `dim_personae.persona_id` |

## Source reliability

| `sources.reliability` | Sources | How the database treats it |
|---|---|---|
| `official` | Your turn results, the GM's newsletter | Facts (as of that turn) |
| `ally_compiled` | An ally's transcription of GM reports (master city data) | Treated like official data, but second-hand and only as fresh as each city's `as_of` date |
| `player_claim` | Player newsletters, proclamations, letters | Claims with a hedge (`asserted` / `reported` / `rumored` / `denied`), never merged into the official tables |

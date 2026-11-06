"""Plan training orders for personae who start the turn together (the `plan_training` tool).

Rules (Skills section): personae who start the turn at the same location may train each other;
no persona may train a given other persona more than once per turn; when the trainer's rating is
higher, the trainee gains sqrt(trainer - trainee). Training runs before movement, so personae can
train first and still move away the same turn.

The planner walks the persona sheets in order (lowest number first, the order the orders file
uses), and for each trainer -> trainee pair picks the skill with the biggest gain, using ratings
as already raised by earlier training in the same turn. Order code: I<trainee number><skill letter>.
"""
from __future__ import annotations

import math

from .common import place_key
from .orders import SKILL_CODES, latest_position

LETTER = {skill: letter for letter, skill in SKILL_CODES.items()}
MAX_RATING = 9999  # rules: "Neither skill ratings nor popularity ratings can exceed 9,999"


def plan(only_skills: set[str] | None = None):
    game_date, _, turn = latest_position()
    personae = sorted(turn["persona_status"], key=lambda p: p["persona_id"])
    skills = {p["persona_id"]: {} for p in personae}
    for s in turn["persona_skills"]:
        skills[s["persona_id"]][s["skill"]] = s["level"]
    # What a trainee could gain from each trainer, simulated in sheet order.
    current = {pid: dict(sk) for pid, sk in skills.items()}
    orders = []
    for trainer in personae:
        for trainee in personae:
            if trainee is trainer or place_key(trainee.get("location")) != place_key(trainer.get("location")):
                continue
            t_id, s_id = trainer["persona_id"], trainee["persona_id"]
            best = None
            for skill, level in current[t_id].items():
                if only_skills and skill not in only_skills:
                    continue
                if skill not in LETTER:
                    continue
                have = current[s_id].get(skill, 0)
                if level > have:
                    gain = math.sqrt(level - have)
                    if best is None or gain > best[1]:
                        best = (skill, gain, have)
            if best:
                skill, gain, have = best
                new = min(MAX_RATING, have + gain)
                current[s_id][skill] = new
                orders.append({"trainer": trainer, "trainee": trainee, "skill": skill,
                               "from": have, "to": new, "gain": new - have,
                               "code": f"i{s_id}{LETTER[skill]}"})
    return game_date, personae, orders

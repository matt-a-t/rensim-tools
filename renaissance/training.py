"""Plan training orders for personae who start the turn together.

    rensim training                      # best plan for every co-located pair
    rensim training --skills fortification,assassination

Rules (Skills section): personae who start the turn at the same location may train each other;
no persona may train a given other persona more than once per turn; when the trainer's rating is
higher, the trainee gains sqrt(trainer - trainee). Training runs before movement, so personae can
train first and still move away the same turn.

The planner walks the persona sheets in order (lowest number first, the order the orders file
uses), and for each trainer -> trainee pair picks the skill with the biggest gain, using ratings
as already raised by earlier training in the same turn. Order code: I<trainee number><skill letter>.
"""
from __future__ import annotations

import argparse
import math
import sys

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rensim training")
    ap.add_argument("--skills", help="comma-separated skills to train (default: any)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    only = set(args.skills.split(",")) if args.skills else None
    game_date, personae, orders = plan(only)
    print(f"Training plan from the {game_date} turn report ({len(orders)} orders):\n")
    print(f"  {'trainer':<10} {'trainee':<10} {'skill':<15} {'from':>5} {'to':>6}  code")
    for o in orders:
        print(f"  {o['trainer']['given_name']:<10} {o['trainee']['given_name']:<10} {o['skill']:<15} "
              f"{o['from']:>5.0f} {o['to']:>6.1f}  {o['code']}")
    print(f"\n  Total gain: {sum(o['gain'] for o in orders):.0f} skill points.\n")
    print("Order codes per persona (add after '*Location .number'):")
    for p in personae:
        codes = [o["code"] for o in orders if o["trainer"] is p]
        if codes:
            print(f"  *{p['location']} .{p['persona_id']} {' '.join(codes)}")


if __name__ == "__main__":
    main()

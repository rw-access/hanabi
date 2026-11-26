"""Paired test of switching from group conventions to literal play after a misplay."""

import argparse
from collections import Counter
import json
from statistics import mean

from .arena import MAX_DEALS, deal_tape, play
from .evaluate import paired_lift


def _team(decks, candidate, partner):
    orders = (("self", (candidate, candidate)),) if partner == "self" else (
        ("first", (candidate, partner)), ("second", (partner, candidate)),
    )
    return [
        {position: play(deck, policies, streams) for position, policies in orders}
        for deck, streams in decks
    ]


def _summary(rows):
    games = [outcome for row in rows for outcome in row.values()]
    endings = Counter(end for _, end in games)
    return {"games": len(games), "mean_score": mean(score for score, _ in games),
            "perfect": endings["perfect"], "fuse": endings["fuse"]}


def paired_report(original, candidate, literal, seed, partner):
    # Seats on the same deck are dependent. Sample each whole deal as a unit.
    differences = [mean(new[k][0] - old[k][0] for k in old)
                   for old, new in zip(original, candidate, strict=True)]
    interval = paired_lift(differences, f"hanabi-adaptive-v1 / bootstrap / {seed} / {partner}")
    return {"group": _summary(original), "adaptive": _summary(candidate),
            "always_lantern": _summary(literal), "mean_lift": interval["mean"],
            "ci95": interval["ci95"],
            "per_deal": [{"group": old, "adaptive": new, "always_lantern": base}
                         for old, new, base in zip(original, candidate, literal, strict=True)]}


def compare(deals, seed):
    if not 1 <= deals <= MAX_DEALS:
        raise ValueError(f"deals must be in 1..{MAX_DEALS}")
    decks = deal_tape(deals, seed)
    results = {}
    for partner in ("self", "lantern", "spark"):
        original = _team(decks, "group", partner)
        candidate = _team(decks, "adaptive", partner)
        literal = _team(decks, "lantern", partner)
        results[partner] = paired_report(original, candidate, literal, seed, partner)
    return {"format": "hanabi-partner-probe-v1", "deals": deals, "seed": seed,
            "deck_and_seat_seeds": decks, "partners": results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deals", type=int, default=128)
    p.add_argument("--seed", type=int, default=20261126)
    args = p.parse_args()
    try:
        report = compare(args.deals, args.seed)
    except ValueError as error:
        p.error(str(error))
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()

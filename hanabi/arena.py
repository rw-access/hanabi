"""Bounded, deterministic cooperation benchmark for Hanabi teammates."""

import argparse
from collections import Counter
from itertools import combinations_with_replacement
import json
from random import Random

from .bots import POLICIES, choose
from .game import Game


MAX_DEALS = 256
MAX_TURNS = 256  # All non-hint moves draw until exhausted; clues have a finite supply.


def play(deal_seed, policies, seat_seeds):
    """Use the same observation/action boundary as the browser game."""
    game = Game(seed=deal_seed)
    streams = tuple(Random(seed) for seed in seat_seeds)
    for _ in range(MAX_TURNS):
        if game.over:
            break
        seat = game.current
        view = game.view(seat)
        game.apply(choose(view, policies[seat], streams[seat]))
    if not game.over:
        raise RuntimeError("Game exceeded arena turn limit")
    return game.score, game.end


def benchmark(policies, deals, seed):
    if not 1 <= deals <= MAX_DEALS:
        raise ValueError(f"Number of deck seeds must be in 1..{MAX_DEALS}")
    if not policies or len(set(policies)) != len(policies) or any(p not in POLICIES for p in policies):
        raise ValueError("Use a nonempty list of unique known policy names")
    shuffles = Random(f"hanabi-v1 / deck / {seed}")
    actions = Random(f"hanabi-v1 / actions / {seed}")
    tape = [
        (shuffles.getrandbits(64), (actions.getrandbits(64), actions.getrandbits(64)))
        for _ in range(deals)
    ]
    matchups = []
    for a, b in combinations_with_replacement(policies, 2):
        outcomes = Counter()
        total = 0
        for deck, streams in tape:
            orders = ((a, b),) if a == b else ((a, b), (b, a))
            for order in orders:
                score, end = play(deck, order, streams)
                total += score
                outcomes[end] += 1
        count = sum(outcomes.values())
        matchups.append({
            "partners": [a, b],
            "games": count,
            "mean_score": round(total / count, 4),
            "perfect_rate": outcomes["perfect"] / count,
            "fuse_out_rate": outcomes["fuse"] / count,
        })
    return {"format": "hanabi-arena-v1", "seed": seed, "deals_per_pair": deals, "matchups": matchups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--games", type=int, default=32, metavar="N",
        help=f"number of shared deck seeds per pairing (1..{MAX_DEALS}); cross-play uses both seats",
    )
    parser.add_argument("--seed", type=int, default=0, help="root seed for the shared deck suite")
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--json", action="store_true", help="write machine-readable results")
    args = parser.parse_args()
    try:
        report = benchmark(args.policies, args.games, args.seed)
    except ValueError as e:
        parser.error(str(e))
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"Hanabi cross-play | {args.games} shared decks | suite seed {args.seed}")
    print("Same policy = self-play; distinct policies play both seats. All are local baselines.")
    print(f"{'Partners':<22} {'Games':>5} {'Mean /25':>9} {'Perfect':>9} {'Fuse-outs':>9}")
    for row in report["matchups"]:
        pair = " + ".join(row["partners"])
        print(
            f"{pair:<22} {row['games']:5d} {row['mean_score']:9.2f}"
            f" {row['perfect_rate']:9.1%} {row['fuse_out_rate']:9.1%}"
        )


if __name__ == "__main__":
    main()

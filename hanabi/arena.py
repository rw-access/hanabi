"""Bounded, deterministic cooperation benchmark for Hanabi teammates."""

import argparse
from collections import Counter
from itertools import combinations_with_replacement
import json
from random import Random

from .bots import POLICIES, choose
from .conventions import PlayTally, literal_failure_probability
from .game import Game


MAX_DEALS = 256
MAX_TURNS = 256  # All non-hint moves draw until exhausted; clues have a finite supply.


def play(deal_seed, policies, seat_seeds, *, play_audits=()):
    """Use the same observation/action boundary as the browser game."""
    game = Game(seed=deal_seed)
    streams = tuple(Random(seed) for seed in seat_seeds)
    for _ in range(MAX_TURNS):
        if game.over:
            break
        seat = game.current
        view = game.view(seat)
        action = choose(view, policies[seat], streams[seat])
        risk = None
        if play_audits:
            for audit in play_audits[seat]:
                audit.turns += 1
            if action["type"] == "play":
                risk = literal_failure_probability(view, action["index"])
        game.apply(action)
        if risk is not None:
            failed = not game.view(seat)["history"][-1]["success"]
            for audit in play_audits[seat]:
                audit.add(risk, failed)
    if not game.over:
        raise RuntimeError("Game exceeded arena turn limit")
    return game.score, game.end


def benchmark(policies, deals, seed, *, convention_gap=False):
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
        if convention_gap:
            pooled = PlayTally()
            by_seat = {(seat, policy): PlayTally() for seat in (0, 1) for policy in sorted({a, b})}
        for deck, streams in tape:
            orders = ((a, b),) if a == b else ((a, b), (b, a))
            for order in orders:
                if convention_gap:
                    audits = tuple((pooled, by_seat[seat, policy]) for seat, policy in enumerate(order))
                    score, end = play(deck, order, streams, play_audits=audits)
                else:
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
        if convention_gap:
            matchups[-1]["convention_gap"] = {
                "method": "literal-full-hand-v1",
                **pooled.report(),
                "by_seat": [{"seat": seat, "policy": policy, **tally.report()}
                            for (seat, policy), tally in by_seat.items()],
            }
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
    parser.add_argument("--convention-gap", action="store_true",
                        help="audit plays against the exact literal-risk reference (slower)")
    args = parser.parse_args()
    try:
        report = benchmark(args.policies, args.games, args.seed, convention_gap=args.convention_gap)
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

    if args.convention_gap:
        def printable(value, *, scale=1, signed=False):
            if value is None:
                return "n/a"
            return f"{scale * value:+.1f}" if signed else f"{scale * value:.1f}"

        print("\nConvention gap (play-weighted): literal risk minus actual failure rate")
        print(f"{'Partners / seat':<28} {'Plays':>6} {'Literal %':>10} {'Failed %':>10} "
              f"{'Gap (pp)':>9} {'p=0 %':>7} {'p>0 gap':>8}")
        for row in report["matchups"]:
            group = row["convention_gap"]
            rows = [(" + ".join(row["partners"]), group)]
            rows += [(f"  seat {s['seat']}: {s['policy']}", s) for s in group["by_seat"]]
            for label, stats in rows:
                print(f"{label:<28} {stats['plays']:6d} "
                      f"{printable(stats['literal_failure_rate'], scale=100):>10} "
                      f"{printable(stats['actual_failure_rate'], scale=100):>10} "
                      f"{printable(stats['gap_pp'], signed=True):>9} "
                      f"{printable(stats['literal_safe_share'], scale=100):>7} "
                      f"{printable(stats['nonzero_risk_gap_pp'], signed=True):>8}")
        print("A descriptive diagnostic, not bits of information, causal evidence, or a training objective.")


if __name__ == "__main__":
    main()

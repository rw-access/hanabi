"""Compare frozen self-play policies on matched, held-out deals."""

import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import platform
from random import Random
from tempfile import NamedTemporaryFile

from .arena import MAX_TURNS, play
from .bots import POLICIES
from .game import Game


FORMAT = "hanabi-selfplay-eval-v1"
DEFAULT_POLICIES = ("group", "lantern", "spark")
BOOTSTRAPS = 2000


def _seed(split, root, lane, index=0):
    key = f"{FORMAT}/{split}/{root}/{lane}/{index}".encode("ascii")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def deal_seeds(split, root, index):
    # The trainer uses the upper half of the 64-bit deck seed space.
    if split not in ("validation", "test"):
        raise ValueError("Choose validation or test as the evaluation split")
    deck = _seed(split, root, "deck", index) % (1 << 62)
    if split == "test":
        deck += 1 << 62
    return deck, [_seed(split, root, f"seat{seat}", index) for seat in (0, 1)]


def play_checkpoint(model, deck, seat_seeds, *, action_counts=None):
    import numpy as np

    game = Game(seed=deck)
    states = [model.initial_state(), model.initial_state()]
    streams = [np.random.Generator(np.random.PCG64(seed)) for seed in seat_seeds]
    for _ in range(MAX_TURNS):
        if game.over:
            break
        seat = game.current
        action, states[seat], _ = model.act(game.view(seat), states[seat], streams[seat])
        game.apply(action)
        if action_counts is not None:
            action_counts[action["type"]] += 1
    if not game.over:
        raise RuntimeError("Game exceeded arena turn limit")
    return game.score, game.end


def paired_lift(differences, seed):
    """Percentile interval, resampling complete matched deals with replacement."""
    count = len(differences)
    rng = Random(seed)
    means = sorted(sum(rng.choices(differences, k=count)) / count for _ in range(BOOTSTRAPS))

    def percentile(q):
        position = (BOOTSTRAPS - 1) * q
        lo = int(position)
        return means[lo] + (means[lo + 1] - means[lo]) * (position - lo)

    return {"mean": sum(differences) / count, "ci95": [percentile(0.025), percentile(0.975)]}


def evaluate(checkpoints, policies, deals, seed, split):
    if deals < 1:
        raise ValueError("Use at least one matched deal")
    if not checkpoints and not policies:
        raise ValueError("Specify at least one checkpoint or heuristic")
    if len(set(policies)) != len(policies) or any(p not in POLICIES for p in policies):
        raise ValueError("Use distinct known heuristic names")
    names = [name for name, _ in checkpoints]
    if len(set(names)) != len(names) or any(not name.strip() or name in POLICIES for name in names):
        raise ValueError("Checkpoint names must be nonempty, unique and distinct from heuristic names")

    models = {}
    participants = {policy: {"kind": "heuristic"} for policy in policies}
    sources = ["game.py", "bots.py", "group_policy.py", "arena.py", "evaluate.py"]
    runtime = {"python": platform.python_version()}
    if checkpoints:
        import numpy as np
        from .learner import Learner

        runtime["numpy"] = np.__version__
        sources.append("learner.py")
        for name, path in checkpoints:
            data = Path(path).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            models[name] = Learner.load(io.BytesIO(data))
            participants[name] = {"kind": "checkpoint", "sha256": digest}

    rows = []
    action_counts = {name: Counter() for name in models}
    for i in range(deals):
        deck, streams = deal_seeds(split, seed, i)
        outcomes = {}
        for name in participants:
            if name in models:
                score, end = play_checkpoint(models[name], deck, streams, action_counts=action_counts[name])
            else:
                score, end = play(deck, (name, name), streams)
            outcomes[name] = {"score": score, "end": end}
        rows.append({"index": i, "deck_seed": deck, "seat_seeds": streams, "outcomes": outcomes})

    summaries = {}
    bootstrap_seed = _seed(split, seed, "paired-bootstrap")
    for name in participants:
        outcomes = [row["outcomes"][name] for row in rows]
        summary = {
            "games": deals,
            "mean_score": sum(p["score"] for p in outcomes) / deals,
            "perfect_rate": sum(p["end"] == "perfect" for p in outcomes) / deals,
            "fuse_out_rate": sum(p["end"] == "fuse" for p in outcomes) / deals,
        }
        if name in action_counts:
            summary["action_counts"] = {kind: action_counts[name][kind] for kind in ("play", "discard", "hint")}
        if "group" in policies and name != "group":
            differences = [row["outcomes"][name]["score"] - row["outcomes"]["group"]["score"]
                           for row in rows]
            summary["paired_lift_vs_group"] = paired_lift(differences, bootstrap_seed)
        summaries[name] = summary
    return {
        "format": FORMAT,
        "split": split,
        "seed": seed,
        "deals_per_policy": deals,
        "participants": participants,
        "runtime": runtime,
        "source_sha256": {s: hashlib.sha256(Path(__file__).with_name(s).read_bytes()).hexdigest()
                          for s in sources},
        "bootstrap": {"method": "paired-deal-percentile", "samples": BOOTSTRAPS,
                      "seed": bootstrap_seed},
        "summary": summaries,
        "deals": rows,
    }


def write_report(report, output):
    """Publish a complete JSON file atomically, including against concurrent writers."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            json.dump(report, temp, allow_nan=False, separators=(",", ":"))
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
            os.link(temp_path, path)
        finally:
            temp_path.unlink()


def _checkpoint(spec):
    name, separator, path = spec.partition("=")
    if not separator or not path or not name.strip():
        raise argparse.ArgumentTypeError("Use NAME=PATH for each checkpoint")
    return name, Path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", action="append", type=_checkpoint, default=[], metavar="NAME=PATH")
    parser.add_argument("--policies", nargs="*", choices=POLICIES, default=DEFAULT_POLICIES)
    parser.add_argument("--deals", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--split", choices=("validation", "test"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    try:
        report = evaluate(args.checkpoint, args.policies, args.deals, args.seed, args.split)
        write_report(report, args.output)
    except (ValueError, OSError, ImportError) as error:
        parser.error(str(error))
    for name, summary in report["summary"].items():
        print(f"{name}: {summary['mean_score']:.2f}/25, {summary['perfect_rate']:.1%} perfect, "
              f"{summary['fuse_out_rate']:.1%} fused ({summary['games']} games)")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()

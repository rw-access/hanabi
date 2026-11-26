"""Train a shared recurrent Hanabi policy on exactly N legal environment actions."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform

from .arena import MAX_TURNS
from .game import COLORS, Game


SEED_DOMAIN = "hanabi-train-v1"


def game_seeds(seed, index):
    """Each game has an independent deck and two independent action streams."""
    def derive(lane):
        key = f"{SEED_DOMAIN}/{seed}/{index}/{lane}".encode("ascii")
        return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")

    # Evaluation owns the lower half of the 64-bit deck seed space.
    return derive("deck") | (1 << 63), (derive("seat0"), derive("seat1"))


def hand_labels(game, seat):
    """Private supervision only. Call after selection and before applying the move."""
    return tuple(COLORS.index(card["color"]) * 5 + card["rank"] - 1
                 for card in game.training_hand(seat))


def _rollout(game, learner, streams, step_limit, metrics=None):
    hidden = [learner.initial_state(), learner.initial_state()]
    episode = []
    while len(episode) < step_limit and not game.over:
        if game.turn >= MAX_TURNS:
            raise RuntimeError("Game exceeded arena turn limit")
        seat = game.current
        action, hidden[seat], record = learner.act(game.view(seat), hidden[seat], streams[seat])
        labels = hand_labels(game, seat)
        if metrics is not None:
            metrics.add(record, labels)
        before = game.score
        game.apply(action)
        episode.append((seat, record, game.score - before, labels))
    return episode


def _source_hashes():
    here = Path(__file__).parent
    return {
        name: hashlib.sha256((here / name).read_bytes()).hexdigest()
        for name in ("game.py", "beliefs.py", "arena.py", "learner.py", "train.py")
    }


def train(steps, seed, output, batch_games=16):
    import numpy as np
    from .learner import Learner, PredictionMetrics

    if steps < 1 or batch_games < 1:
        raise ValueError("steps and batch-games must both be positive")
    if seed < 0:
        raise ValueError("seed must be nonnegative")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    learner = Learner(seed)
    metrics = PredictionMetrics()
    learner.save(output / "initial.npz")

    spent = complete_steps = games = updates = score_sum = tail = 0
    endings = Counter()
    batch = []
    with (output / "games.jsonl").open("x") as game_log, (output / "updates.jsonl").open("x") as update_log:
        while spent < steps:
            deck, action_seeds = game_seeds(seed, games)
            game = Game(seed=deck, empty_clues=True)
            streams = [np.random.Generator(np.random.PCG64(s)) for s in action_seeds]
            episode = _rollout(game, learner, streams, steps - spent, metrics)
            spent += len(episode)
            game_log.write(json.dumps({
                "index": games, "deck_seed": deck, "seat_seeds": action_seeds,
                "steps": len(episode), "end": game.end,
                "score": game.score if game.over else None,
            }) + "\n")
            if game.over:
                batch.append(episode)
                games += 1
                complete_steps += len(episode)
                score_sum += game.score
                endings[game.end] += 1
            else:
                tail = len(episode)

            if batch and (len(batch) == batch_games or spent == steps):
                learner.update(batch)
                updates += 1
                update_log.write(json.dumps({
                    "update": updates, "environment_steps": spent, "trained_steps": complete_steps,
                    "completed_games": games, "batch_games": len(batch), "score_sum": score_sum,
                    "endings": dict(endings),
                }) + "\n")
                update_log.flush()
                game_log.flush()
                batch.clear()

    learner.save(output / "final.npz")
    report = {
        "format": "hanabi-train-v2", "status": "complete", "empty_clues": True,
        "seed": seed, "seed_domain": SEED_DOMAIN, "batch_games": batch_games,
        "step_budget": steps, "environment_steps": spent, "trained_steps": complete_steps,
        "incomplete_game_steps": tail, "completed_games": games, "updates": updates,
        "score_sum": score_sum, "mean_training_score": score_sum / games if games else None,
        "endings": dict(endings), "policy": learner.config(), "card_prediction": metrics.report(),
        "python_version": platform.python_version(), "numpy_version": np.__version__,
        "source_sha256": _source_hashes(),
        "checkpoints_sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in ("initial.npz", "final.npz")
        },
    }
    # Publish the completion marker only after both checkpoints and the log files exist.
    pending = output / "training.json.tmp"
    pending.write_text(json.dumps(report, indent=2) + "\n")
    pending.replace(output / "training.json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, required=True, help="exact number of Game.apply calls")
    parser.add_argument("--seed", type=int, required=True, help="nonnegative training root seed")
    parser.add_argument("--output", type=Path, required=True, help="new directory for this run")
    parser.add_argument("--batch-games", type=int, default=16, help="complete games per policy update")
    args = parser.parse_args()
    # Set before importing NumPy; a CLI invocation uses one numerical worker.
    for setting in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS", "OMP_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS"):
        os.environ[setting] = "1"
    try:
        report = train(args.steps, args.seed, args.output, args.batch_games)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    except ModuleNotFoundError as exc:
        if exc.name != "numpy":
            raise
        parser.error("The self-play trainer requires NumPy")
    print(f"{report['environment_steps']} steps, {report['completed_games']} complete games, "
          f"{report['updates']} updates. Report in {args.output / 'training.json'}")


if __name__ == "__main__":
    main()

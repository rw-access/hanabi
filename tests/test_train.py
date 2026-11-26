import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hanabi.game import Game
from hanabi.train import _rollout, game_seeds, train


class TrainingRolloutTests(unittest.TestCase):
    def test_public_views_seat_local_memory_and_official_fuse_reward(self):
        class BlindPlayer:
            def initial_state(self):
                return 0

            def act(self, view, hidden, rng):
                return {"type": "play", "index": 0}, hidden + 1, (hidden, rng, view)

        player = BlindPlayer()
        for _ in range(2):
            game = Game(seed=7)
            episode = _rollout(game, player, ("first", "second"), 20)
            self.assertEqual(game.end, "fuse")
            self.assertEqual([reward for _, _, reward in episode], [1, 0, 0, 1, -2])
            self.assertEqual(sum(reward for _, _, reward in episode), game.score)
            for turn, (seat, (hidden, rng, view), _) in enumerate(episode):
                self.assertEqual((view["seat"], view["turn"]), (seat, turn))
                self.assertEqual(hidden, turn // 2)
                self.assertEqual(rng, ("first", "second")[seat])
                self.assertTrue(all(set(slot) == {"colors", "ranks"} for slot in view["hand"]))

    def test_seed_schedule_is_separate_for_each_deal_and_each_seat(self):
        deck, seats = game_seeds(19, 10)
        self.assertEqual((deck, seats), game_seeds(19, 10))
        self.assertTrue(2**63 <= deck < 2**64)
        self.assertNotEqual(seats[0], seats[1])
        self.assertNotEqual(deck, game_seeds(19, 11)[0])
        self.assertNotEqual(deck, game_seeds(20, 10)[0])


@unittest.skipUnless(importlib.util.find_spec("numpy"), "optional training dependency NumPy")
class TrainingRunTests(unittest.TestCase):
    def test_exact_budget_batch_flush_and_reproducible_weights(self):
        import numpy as np

        with TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ("first", "second")]
            reports = [train(301, 5, path, batch_games=4) for path in paths]
            self.assertEqual(reports[0], reports[1])
            report = reports[0]
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["step_budget"], report["environment_steps"])
            self.assertEqual(report["step_budget"], 301)
            self.assertEqual(json.loads((paths[0] / "training.json").read_text()), report)
            self.assertEqual(report["trained_steps"] + report["incomplete_game_steps"], 301)
            self.assertEqual(sum(report["endings"].values()), report["completed_games"])

            games = self.read_log(paths[0], "games.jsonl")
            updates = self.read_log(paths[0], "updates.jsonl")
            self.assertEqual(games, self.read_log(paths[1], "games.jsonl"))
            self.assertEqual(updates, self.read_log(paths[1], "updates.jsonl"))
            complete = [game for game in games if game["end"] is not None]
            self.assertEqual(len(complete), report["completed_games"])
            self.assertEqual(sum(g["score"] for g in complete), report["score_sum"])
            self.assertEqual(sum(g["steps"] for g in complete), report["trained_steps"])
            self.assertEqual(sum(g["steps"] for g in games), 301)
            self.assertEqual(len(updates), (len(complete) + 3) // 4)
            self.assertEqual(sum(row["batch_games"] for row in updates), len(complete))
            self.assertTrue(all(row["batch_games"] == 4 for row in updates[:-1]))
            with np.load(paths[0] / "final.npz") as a, np.load(paths[1] / "final.npz") as b:
                for name in a.files:
                    np.testing.assert_array_equal(a[name], b[name])

    @staticmethod
    def read_log(directory, name):
        return [json.loads(line) for line in (directory / name).read_text().splitlines()]

    def test_single_incomplete_step_and_overwrite_guard(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            with self.assertRaises(ValueError):
                train(0, 1, output)
            self.assertFalse(output.exists())
            report = train(1, 1, output)
            self.assertEqual(report["incomplete_game_steps"], 1)
            self.assertEqual(report["trained_steps"], 0)
            self.assertEqual(report["completed_games"], 0)
            self.assertEqual(report["updates"], 0)
            self.assertIsNone(report["mean_training_score"])
            with self.assertRaises(FileExistsError):
                train(1, 1, output)
            self.assertEqual(json.loads((output / "training.json").read_text()), report)

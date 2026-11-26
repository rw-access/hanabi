import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hanabi import evaluate
from hanabi.arena import play


class MatchedEvaluationTests(unittest.TestCase):
    def test_splits_are_disjoint_and_suite_prefix_is_stable(self):
        for root in (0, 15):
            for i in range(10):
                validation, v_seats = evaluate.deal_seeds("validation", root, i)
                test, t_seats = evaluate.deal_seeds("test", root, i)
                self.assertLess(validation, 1 << 62)
                self.assertLessEqual(1 << 62, test)
                self.assertLess(test, 1 << 63)
                self.assertEqual(len(set(v_seats + t_seats)), 4)
        short = evaluate.evaluate([], ["random"], 2, 7, "validation")
        long = evaluate.evaluate([], ["random"], 3, 7, "validation")
        self.assertEqual(short["deals"], long["deals"][:2])

    def test_report_replays_shared_deals_and_paired_differences(self):
        report = evaluate.evaluate([], ["group", "random"], 4, 5, "test")
        differences = []
        for row in report["deals"]:
            for name in ("group", "random"):
                expected = play(row["deck_seed"], (name, name), row["seat_seeds"])
                actual = row["outcomes"][name]
                self.assertEqual((actual["score"], actual["end"]), expected)
            differences.append(row["outcomes"]["random"]["score"] - row["outcomes"]["group"]["score"])
        lift = report["summary"]["random"]["paired_lift_vs_group"]
        self.assertEqual(lift["mean"], sum(differences) / len(differences))
        self.assertLessEqual(min(differences), lift["ci95"][0])
        self.assertLessEqual(lift["ci95"][1], max(differences))
        self.assertEqual(evaluate.paired_lift([3, 3, 3], 5), {"mean": 3, "ci95": [3, 3]})

    def test_existing_or_incomplete_results_are_never_published_over_existing_data(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "eval.json"
            evaluate.write_report({"complete": 1}, path)
            with self.assertRaises(FileExistsError):
                evaluate.write_report({"complete": 2}, path)
            with self.assertRaises(TypeError):
                evaluate.write_report({"invalid": object()}, path.with_name("other.json"))
            self.assertEqual(json.loads(path.read_text()), {"complete": 1})
            self.assertEqual(list(path.parent.iterdir()), [path])

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for checkpoint evaluation")
    def test_seat_state_stays_private_and_resets_at_every_deal(self):
        case = self

        class Observer:
            def initial_state(self):
                return []

            def act(self, view, hidden, rng):
                seat = view["seat"]
                case.assertEqual(seat, view["current"])
                case.assertEqual(len(hidden), view["turn"] // 2)
                case.assertTrue(all(old_seat == seat for old_seat in hidden))
                case.assertTrue(all(set(slot) == {"colors", "ranks"} for slot in view["hand"]))
                hidden.append(seat)
                return view["legal"][rng.integers(len(view["legal"]))], hidden, None

        model = Observer()
        first = evaluate.play_checkpoint(model, 42, [5, 9])
        self.assertEqual(evaluate.play_checkpoint(model, 42, [5, 9]), first)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for checkpoint evaluation")
    def test_frozen_checkpoint_identity_replay_and_metrics(self):
        from hanabi.learner import Learner

        with TemporaryDirectory() as directory:
            path = Path(directory) / "model.npz"
            Learner(11).save(path)
            report = evaluate.evaluate([("first", path), ("copy", path)], [], 4, 8, "validation")
            self.assertEqual(report["participants"]["first"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            for row in report["deals"]:
                self.assertEqual(row["outcomes"]["first"], row["outcomes"]["copy"])
            self.assertEqual(report["summary"]["first"], report["summary"]["copy"])
            self.assertTrue(report["empty_clues"])
            summary = report["summary"]["first"]
            metrics = summary["card_prediction"]
            self.assertEqual(metrics["decisions"], sum(summary["action_counts"].values()))
            self.assertGreater(metrics["slots"], metrics["decisions"])
            for predictor in ("learned", "deduction_prior"):
                self.assertGreaterEqual(metrics[predictor]["cross_entropy_nats"], 0)
                self.assertTrue(0 <= metrics[predictor]["top1_accuracy"] <= 1)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required for checkpoint evaluation")
    def test_legacy_checkpoint_uses_original_features_rules_and_deals(self):
        folder = Path(__file__).resolve().parents[1] / "docs" / "selfplay-v1"
        archived = json.loads((folder / "test.json").read_text())
        report = evaluate.evaluate([("trained", folder / "seed-11-final.npz")], [], 3,
                                   archived["seed"], "test")
        self.assertFalse(report["empty_clues"])
        self.assertNotIn("card_prediction", report["summary"]["trained"])
        for actual, expected in zip(report["deals"], archived["deals"]):
            self.assertEqual(actual["outcomes"]["trained"], expected["outcomes"]["trained"])


if __name__ == "__main__":
    unittest.main()

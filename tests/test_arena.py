import unittest
from collections import Counter
from random import Random
from unittest.mock import patch

from hanabi import arena
from hanabi.bots import POLICIES, choose
from hanabi.game import COLORS, RANKS, Game


def knowledge(colors=COLORS, ranks=RANKS):
    return {"colors": list(colors), "ranks": list(ranks)}


def sample_view():
    return {
        "hand": [knowledge(ranks=(1,)), knowledge(ranks=(2,))],
        "partner": [
            {"color": "red", "rank": 1, "knowledge": knowledge()},
            {"color": "yellow", "rank": 3, "knowledge": knowledge()},
        ],
        "fireworks": dict.fromkeys(COLORS, 0),
        "discard": [],
        "lives": 3,
        "clues": 3,
        "final_turns": None,
        "legal": [
            {"type": "play", "index": 0}, {"type": "play", "index": 1},
            {"type": "discard", "index": 0}, {"type": "discard", "index": 1},
            {"type": "hint", "kind": "color", "value": "red"},
            {"type": "hint", "kind": "rank", "value": 1},
        ],
    }


class PolicyTests(unittest.TestCase):
    def test_adaptive_fallback_switches_on_public_fuse_count(self):
        game = Game(seed=0, empty_clues=True)
        game.apply({"type": "hint", "kind": "rank", "value": 1})
        game.apply({"type": "hint", "kind": "color", "value": "blue"})
        view = game.view(0)  # The partner already has a literally safe 1.
        self.assertNotEqual(choose(view, "group"), choose(view, "lantern"))
        for lives, policy in ((3, "group"), (2, "lantern"), (1, "lantern")):
            view["lives"] = lives
            self.assertEqual(choose(view, "adaptive"), choose(view, policy))

    def test_bots_take_certain_play_from_rank_hint(self):
        v = sample_view()
        for policy in ("lantern", "literal", "spark"):
            with self.subTest(policy=policy):
                self.assertEqual(choose(v, policy), {"type": "play", "index": 0})

    def test_prefer_clue_that_makes_an_unambiguous_play(self):
        v = sample_view()
        v["hand"][0] = knowledge()
        self.assertEqual(choose(v), {"type": "hint", "kind": "rank", "value": 1})

    def test_counting_eliminates_impossible_identity(self):
        v = sample_view()
        # Both red 2s are visible in the other hand. Ours is therefore
        # blue 2, but a teammate who ignores copy counts can't know it.
        v["hand"][0] = knowledge(colors=("red", "blue"), ranks=(2,))
        v["hand"][1] = knowledge()
        v["fireworks"]["blue"] = 1
        v["partner"] = [
            {"color": "red", "rank": 2, "knowledge": knowledge()},
            {"color": "red", "rank": 2, "knowledge": knowledge()},
        ]
        v["legal"] = [a for a in v["legal"] if a["type"] != "hint"]
        self.assertEqual(choose(v), {"type": "play", "index": 0})
        self.assertEqual(choose(v, "literal")["type"], "discard")

    def test_all_policies_choose_legal_actions(self):
        for seed in range(8):
            for policy in POLICIES:
                game = Game(seed=seed)
                rng = Random(0)
                for _ in range(arena.MAX_TURNS):
                    if game.over:
                        break
                    v = game.view(game.current)
                    action = choose(v, policy, rng)
                    self.assertIn(action, v["legal"])
                    game.apply(action)
                self.assertTrue(game.over)


class ArenaTests(unittest.TestCase):
    def test_shared_seeds_and_seat_swaps(self):
        with patch("hanabi.arena.play", return_value=(25, "perfect")) as play:
            report = arena.benchmark(["lantern", "literal", "random"], deals=3, seed=22)
        calls = [call.args for call in play.call_args_list]
        usage = Counter((deck, streams) for deck, pair, streams in calls)
        self.assertEqual(sorted(usage.values()), [9, 9, 9])
        for deck, streams in usage:
            orders = {pair for d, pair, s in calls if (d, s) == (deck, streams)}
            self.assertEqual(
                orders,
                {(a, b) for a in ("lantern", "literal", "random") for b in ("lantern", "literal", "random")},
            )
        self.assertEqual(sorted(row["games"] for row in report["matchups"]), [3, 3, 3, 6, 6, 6])
        self.assertTrue(all(row["perfect_rate"] == 1 for row in report["matchups"]))

    def test_reproducible_and_metrics_bounded(self):
        args = (["spark", "random"], 3, 7)
        report = arena.benchmark(*args)
        self.assertEqual(report, arena.benchmark(*args))
        for row in report["matchups"]:
            self.assertTrue(0 <= row["mean_score"] <= 25)
            self.assertTrue(0 <= row["perfect_rate"] + row["fuse_out_rate"] <= 1)

    def test_rejects_unbounded_and_duplicate_requests(self):
        with self.assertRaises(ValueError):
            arena.benchmark(["lantern"], arena.MAX_DEALS + 1, 0)
        with self.assertRaises(ValueError):
            arena.benchmark(["literal", "literal"], 2, 0)


if __name__ == "__main__":
    unittest.main()

from collections import Counter
from copy import deepcopy
import random
import unittest

from hanabi.game import Card, COLORS, COPIES, Game, IllegalMove, RANKS, Slot


class GameTests(unittest.TestCase):
    def test_deal_and_conservation(self):
        for seed in range(12):
            game = Game(seed)
            self.assertEqual([len(h) for h in game._hands], [5, 5])
            self.assertEqual(len(game._deck), 40)
            rng = random.Random(seed + 41)
            expected = Counter({Card(c, r): n for c in COLORS for r, n in COPIES.items()})
            while True:
                cards = [s.card for hand in game._hands for s in hand] + game._deck + game.discard
                cards += [Card(c, r) for c in COLORS for r in range(1, game.fireworks[c] + 1)]
                self.assertEqual(Counter(cards), expected)
                self.assertTrue(0 <= game.clues <= 8 and 0 <= game.lives <= 3)
                for hand in game._hands:
                    for s in hand:
                        self.assertIn(s.card.color, s.colors)
                        self.assertIn(s.card.rank, s.ranks)
                if game.over:
                    break
                game.apply(rng.choice(game.view(game.current)["legal"]))
                self.assertLess(game.turn, 160)

    def test_clue_marks_all_matches_and_excludes_all_nonmatches(self):
        g = Game(7)
        g._hands[1] = [Slot(Card("red", r)) for r in (1, 1, 3, 2, 1)]
        g.apply({"type": "hint", "kind": "rank", "value": 1})
        v = g.view(1)
        self.assertEqual(v["history"][-1]["matched"], [0, 1, 4])
        for i, s in enumerate(v["hand"]):
            self.assertEqual(s["ranks"], [1] if i in (0, 1, 4) else [2, 3, 4, 5])
            self.assertEqual(s["colors"], list(COLORS))
        g.apply({"type": "discard", "index": 0})
        self.assertEqual(g.view(1)["hand"][-1], {"colors": list(COLORS), "ranks": list(RANKS)})
        self.assertEqual(g.view(1)["hand"][0]["ranks"], [1])
        self.assertEqual(g.clues, 8)

    def test_illegal_actions_atomic(self):
        g = Game(52)
        g._hands[1] = [Slot(Card("red", 1))] * 5
        initial = g.view(0)
        bad = [None, [], {}, {"type": "play", "index": False}, {"type": "play", "index": 1.0},
               {"type": "play", "index": 5}, {"type": "play", "index": -1},
               {"type": "play", "index": 1, "noise": True}, {"type": "discard", "index": 0},
               {"type": "hint", "kind": "rank", "value": True},
               {"type": "hint", "kind": "color", "value": "blue"}]
        for move in bad:
            with self.subTest(move=move), self.assertRaises(IllegalMove):
                g.apply(move)
            self.assertEqual(g.view(0), initial)
        g.clues = 0
        with self.assertRaises(IllegalMove):
            g.apply({"type": "hint", "kind": "color", "value": "red"})

    def test_fives_award_clue_but_never_exceed_cap(self):
        for clues, after in [(7, 8), (8, 8), (3, 4)]:
            g = Game(1)
            g.clues = clues
            g._hands[0][0].card = Card("red", 5)
            g.fireworks["red"] = 4
            g.apply({"type": "play", "index": 0})
            self.assertEqual((g.clues, g.fireworks["red"], g.score), (after, 5, 5))

    def test_final_draw_allows_two_additional_turns_even_for_hints(self):
        g = Game(7)
        g._deck = [Card("red", 3)]
        g.clues = 7
        g.apply({"type": "discard", "index": 0})
        self.assertEqual((len(g._hands[0]), g.final_turns, g.current), (5, 2, 1))
        first = next(a for a in g.view(1)["legal"] if a["type"] == "hint")
        g.apply(first)
        self.assertEqual(g.final_turns, 1)
        g.apply({"type": "discard", "index": 0})
        self.assertEqual((len(g._hands[0]), g.end, g.final_turns), (4, "deck", 0))
        self.assertEqual(g.view(1)["legal"], [])
        with self.assertRaises(IllegalMove):
            g.apply(first)

    def test_final_turn_fuse_and_perfect_take_precedence(self):
        for perfect in (True, False):
            g = Game(99)
            g.final_turns = 1
            g._deck.clear()
            g._hands[0][1].card = Card("blue", 5)
            g.fireworks = dict.fromkeys(COLORS, 5)
            g.fireworks["blue"] = 4 if perfect else 0
            g.lives = 1
            g.apply({"type": "play", "index": 1})
            self.assertEqual((g.end, g.score), ("perfect", 25) if perfect else ("fuse", 0))
            self.assertEqual(g.discard, [] if perfect else [Card("blue", 5)])

    def test_observation_invariance_and_detachment(self):
        g = Game(14)
        for seat in (0, 1):
            h = deepcopy(g)
            # Swap a privately held card with an unobserved draw; public info is identical.
            h._hands[seat][0].card, h._deck[0] = h._deck[0], h._hands[seat][0].card
            h._deck.reverse()
            self.assertEqual(g.view(seat), h.view(seat))
            self.assertNotIn("seed", g.view(seat))
            self.assertEqual(set(g.view(seat)["hand"][0]), {"colors", "ranks"})
        g.apply(next(a for a in g.view(0)["legal"] if a["type"] == "hint"))
        g.apply({"type": "discard", "index": 1})
        v = g.view(0)
        v["history"][0]["matched"].clear()
        v["history"][1]["card"]["color"] = "fake"
        v["fireworks"]["red"] = 99
        v["hand"][0]["ranks"].clear()
        v["partner"][0]["knowledge"]["ranks"].clear()
        self.assertNotEqual(g.view(0), v)
        self.assertNotEqual(g.view(0)["history"], v["history"])
        with self.assertRaises(ValueError):
            g.view(True)

    def test_equal_seeds_and_actions_replay(self):
        g, twin = Game(42), Game(42)
        rng = random.Random(65)
        while not g.over:
            self.assertEqual(g.view(g.current), twin.view(twin.current))
            action = rng.choice(g.view(g.current)["legal"])
            g.apply(action)
            twin.apply(action)
        self.assertEqual(g.end, twin.end)
        self.assertEqual(g.view(0), twin.view(0))

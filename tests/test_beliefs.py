from collections import Counter
from copy import deepcopy
from itertools import permutations
import unittest

from hanabi.beliefs import hand_beliefs, identity_weights
from hanabi.game import COLORS, COPIES, RANKS, Game


def slot(colors=COLORS, ranks=RANKS):
    return {"colors": list(colors), "ranks": list(ranks)}


def view_with_pool(hand, pool):
    """Build a short endgame where all physical copies outside 'pool' are public."""
    left = Counter(pool)
    discard = [{"color": color, "rank": rank}
               for color in COLORS for rank in RANKS
               for _ in range(COPIES[rank] - left[color, rank])]
    return {"hand": hand, "partner": [], "fireworks": dict.fromkeys(COLORS, 0), "discard": discard}


class HandBeliefTests(unittest.TestCase):
    def test_all_public_locations_reduce_the_deck_and_correlate_color_with_rank(self):
        # The three red 1s are in three different public locations.
        view = {"hand": [slot(["red", "blue"], [1, 5])] * 2,
                "fireworks": dict.fromkeys(COLORS, 0),
                "partner": [{"color": "red", "rank": 1}], "discard": [{"color": "red", "rank": 1}]}
        view["fireworks"]["red"] = 1
        for belief in hand_beliefs(view):
            self.assertEqual(belief["possible"], [4, 15, 19])  # red 5, blue 1, blue 5
            self.assertEqual(belief["probabilities"], [1 / 5, 3 / 5, 1 / 5])
            self.assertEqual(identity_weights(belief), {("red", 5): .2, ("blue", 1): .6, ("blue", 5): .2})

    def test_two_ambiguous_slots_reserve_both_unique_fives(self):
        # No individually known cards. The first two slots nevertheless account
        # for both red 5 and blue 5, proving a green 5 in the third position.
        pair = slot(["red", "blue"], [5])
        view = view_with_pool([pair, pair, slot(["red", "blue", "green"], [5])],
                              [("red", 5), ("blue", 5), ("green", 5), ("yellow", 3)])
        first, second, third = hand_beliefs(view)
        self.assertEqual(first, {"possible": [4, 19], "probabilities": [0.5, 0.5]})
        self.assertEqual(second, first)
        self.assertEqual(third, {"possible": [14], "probabilities": [1.0]})

    def test_matches_enumeration_of_ordered_physical_deals(self):
        pool = [("red", 1)] * 3 + [("red", 2), ("yellow", 2), ("green", 2), ("blue", 5)]
        hand = [slot(["red", "yellow"], [1, 2]), slot(["red", "green"], [2]),
                slot(["red", "yellow", "blue"], [1, 2, 5]), slot(COLORS, [1, 5]), slot()]
        for size in range(1, 6):
            with self.subTest(size=size):
                counts = [Counter() for _ in range(size)]
                # Repeated copies deliberately appear multiple times in these permutations.
                for cards in permutations(pool, size):
                    if all(c in k["colors"] and r in k["ranks"] for (c, r), k in zip(cards, hand)):
                        for occurrences, (color, rank) in zip(counts, cards):
                            occurrences[COLORS.index(color) * 5 + rank - 1] += 1
                beliefs = hand_beliefs(view_with_pool(hand[:size], pool))
                for belief, expected in zip(beliefs, counts):
                    self.assertEqual(belief["possible"], sorted(expected))
                    self.assertEqual(belief["probabilities"],
                                     [expected[k] / expected.total() for k in sorted(expected)])
                    self.assertAlmostEqual(sum(belief["probabilities"]), 1.0)

    def test_hidden_card_swaps_and_result_mutation_do_not_change_beliefs(self):
        game = Game(394)
        twin = deepcopy(game)
        for i in range(5):
            twin._hands[0][i].card, twin._deck[i] = twin._deck[i], twin._hands[0][i].card
        twin._deck.reverse()
        before = hand_beliefs(game.view(0))
        result = hand_beliefs(twin.view(0))
        self.assertEqual(result, before)
        result[0]["possible"].clear()
        result[1]["probabilities"][0] = -1
        self.assertEqual(hand_beliefs(game.view(0)), before)

    def test_empty_and_inconsistent_hands(self):
        self.assertEqual(hand_beliefs(view_with_pool([], [])), [])
        red_five = slot(["red"], [5])
        with self.assertRaisesRegex(ValueError, "consistent"):
            hand_beliefs(view_with_pool([red_five, red_five], [("red", 5), ("blue", 1)]))

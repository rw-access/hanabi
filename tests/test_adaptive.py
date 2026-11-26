from copy import deepcopy
import unittest

from hanabi.bots import choose
from hanabi.game import COLORS, COPIES, RANKS, Game


ONES = {"type": "hint", "kind": "rank", "value": 1}
BLUE = {"type": "hint", "kind": "color", "value": "blue"}


def table(*, last_four=False):
    # Synthetic deal matching the revealed cards and positive touches in the reported opening.
    human = [("red", 2), ("yellow", 4), ("blue", 4), ("white", 3), ("green", 1)]
    bot = [("white", 4), ("blue", 1), ("green", 3), ("yellow", 1), ("blue", 2)]
    if last_four:
        human[0], human[4], bot[0] = ("green", 4), ("red", 2), ("green", 4)
    dealt = [card for pair in zip(human, bot) for card in pair]
    rest = [(color, rank) for color in COLORS for rank in RANKS for _ in range(COPIES[rank])]
    for card in dealt:
        rest.remove(card)
    order = [{"color": color, "rank": rank} for color, rank in dealt + rest]
    return Game.from_draw_order(order, empty_clues=True)


class AdaptiveCooperationTests(unittest.TestCase):
    def test_returns_the_opening_one_clue_before_playing_its_own_one(self):
        game = table()
        game.apply(ONES)
        self.assertEqual(game.view(1)["history"][-1]["matched"], [1, 3])

        # The unchanged Group policy illustrates the missed opportunity.
        old = deepcopy(game)
        for color, index in (("blue", 1), ("yellow", 2)):
            self.assertEqual(choose(old.view(1), "group"), {"type": "play", "index": index})
            old.apply({"type": "play", "index": index})
            self.assertEqual(old.fireworks[color], 1)
            if color == "blue":
                old.apply(BLUE)
                self.assertEqual(old.view(1)["history"][-1]["matched"], [3])
        old.apply({"type": "discard", "index": 4})
        self.assertEqual(old.discard[-1].visible(), {"color": "green", "rank": 1})

        action = choose(game.view(1), "adaptive")
        self.assertEqual(action, ONES)
        game.apply(action)
        human = game.view(0)
        self.assertEqual(human["history"][-1]["matched"], [4])
        self.assertTrue(all(identity % 5 == 0 for identity in human["beliefs"][4]["possible"]))
        game.apply({"type": "play", "index": 4})
        self.assertEqual(game.fireworks["green"], 1)

    def test_still_offers_a_proven_partner_play_after_a_misplay(self):
        game = table()
        game.apply({"type": "hint", "kind": "color", "value": "white"})
        game.apply({"type": "play", "index": 0})
        self.assertEqual(game.lives, 2)
        game.apply(ONES)
        self.assertEqual(choose(game.view(1), "adaptive"), ONES)

    def test_takes_own_play_if_human_already_has_a_proven_play(self):
        game = table()
        game.apply(ONES)
        game.apply(ONES)
        game.apply(BLUE)
        self.assertEqual(choose(game.view(1), "adaptive")["type"], "play")

    def test_takes_own_play_without_tokens_or_a_future_partner_turn(self):
        for clues, final_turns in ((0, None), (7, 1)):
            with self.subTest(clues=clues, final_turns=final_turns):
                game = table()
                game.apply(ONES)
                game.clues = clues
                game.final_turns = final_turns
                self.assertEqual(choose(game.view(1), "adaptive")["type"], "play")

    def test_saves_the_last_reachable_four_on_the_partner_discard_slot(self):
        game = table(last_four=True)
        game.apply(ONES)
        game.apply({"type": "discard", "index": 0})
        self.assertEqual(game.discard[-1].visible(), {"color": "green", "rank": 4})
        game.apply(ONES)
        action = choose(game.view(1), "adaptive")
        self.assertEqual(action, {"type": "hint", "kind": "rank", "value": 4})
        game.apply(action)
        self.assertIn(0, game.view(0)["history"][-1]["matched"])

    def test_hidden_identity_and_draw_order_do_not_change_return_clue(self):
        game = table()
        game.apply(ONES)
        original = game.view(1)
        # An unmarked non-1 can be exchanged with a different unseen non-1.
        slot = game._hands[1][0]
        other = next(i for i, card in enumerate(game._deck) if card.rank != 1 and card != slot.card)
        slot.card, game._deck[other] = game._deck[other], slot.card
        game._deck.reverse()
        self.assertEqual(game.view(1), original)
        self.assertEqual(choose(game.view(1), "adaptive"), choose(original, "adaptive"))

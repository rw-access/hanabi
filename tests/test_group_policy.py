import unittest

from hanabi.bots import choose
from hanabi.game import COLORS, RANKS, Card, Game
from hanabi.group_policy import _replay_marks


def knowledge(colors=COLORS, ranks=RANKS):
    return {"colors": list(colors), "ranks": list(ranks)}


def move(kind, index):
    return {"type": kind, "index": index}


def hint(kind, value):
    return {"type": "hint", "kind": kind, "value": value}


def heard(kind, value, matches):
    return {**hint(kind, value), "player": 1, "matched": matches}


def saw_play(seat, index, color, rank):
    return {**move("play", index), "player": seat, "success": True,
            "card": {"color": color, "rank": rank}}


def view(cards=None):
    cards = cards or [("blue", 3), ("red", 1), ("yellow", 4), ("green", 4), ("red", 3)]
    return {
        "seat": 0, "hand": [knowledge() for _ in cards],
        "partner": [{"color": color, "rank": rank, "knowledge": knowledge()}
                    for color, rank in cards],
        "fireworks": dict.fromkeys(COLORS, 0), "discard": [],
        "history": [], "final_turns": None,
        "legal": [move(t, i) for t in ("play", "discard") for i in range(len(cards))],
    }


class GroupPolicyTests(unittest.TestCase):
    def test_certain_play_goes_before_clues(self):
        v = view()
        v["hand"][3] = knowledge(ranks=(1,))
        v["legal"].append(hint("rank", 1))
        self.assertEqual(choose(v, "group"), move("play", 3))

    def test_color_focus_is_newest_touched_without_playing_bystanders(self):
        v = view()
        v["history"] = [heard("color", "blue", [1, 3])]
        v["hand"][1] = v["hand"][3] = knowledge(colors=("blue",))
        self.assertEqual(choose(v, "group"), move("play", 3))
        marks = _replay_marks(v["history"])[0]
        self.assertTrue(marks[1].painted)
        self.assertIsNone(marks[1].play)

    def test_rank_touching_preclue_chop_saves_and_protects_bystanders(self):
        v = view()
        v["fireworks"]["red"] = 1
        v["history"] = [saw_play(0, 4, "red", 1), heard("rank", 2, [0, 3])]
        v["hand"][0] = v["hand"][3] = knowledge(ranks=(2,))
        self.assertEqual(choose(v, "group"), move("discard", 1))
        marks = _replay_marks(v["history"])[0]
        self.assertTrue(marks[0].painted and marks[3].painted)
        self.assertIsNone(marks[0].play)
        self.assertIsNone(marks[3].play)

    def test_rank_touching_chop_does_not_erase_bystander_prior_signal(self):
        history = [heard("color", "red", [3]), heard("rank", 1, [0, 3])]
        marks = _replay_marks(history)[0]
        self.assertIsNone(marks[0].play)
        self.assertEqual(marks[3].play, frozenset({("red", 1)}))

    def test_existing_clue_moves_with_its_slot_and_draw_is_unpainted(self):
        history = [heard("color", "white", [2, 4]), saw_play(0, 0, "red", 1)]
        marks = _replay_marks(history)[0]
        self.assertTrue(marks[1].painted and marks[3].painted)
        self.assertEqual(marks[3].play, frozenset({("white", 1)}))
        self.assertFalse(marks[4].painted)
        self.assertIsNone(marks[4].play)

    def test_an_old_play_signal_does_not_advance_its_rank(self):
        v = view()
        v["history"] = [heard("color", "red", [3]), saw_play(1, 2, "red", 1)]
        v["fireworks"]["red"] = 1
        v["hand"][3] = knowledge(colors=("red",))
        self.assertEqual(choose(v, "group"), move("discard", 0))

    def test_color_preferred_when_both_send_safe_play(self):
        v = view(cards=[("blue", 3), ("red", 1), ("yellow", 4), ("green", 4), ("white", 2)])
        v["legal"] += [hint("rank", 1), hint("color", "red")]
        self.assertEqual(choose(v, "group"), hint("color", "red"))

    def test_rank_fallback_when_color_would_focus_a_later_wrong_card(self):
        v = view()
        v["legal"] += [hint("rank", 1), hint("color", "red")]
        self.assertEqual(choose(v, "group"), hint("rank", 1))

    def test_clean_play_beats_wasteful_repeat_but_save_beats_discard(self):
        v = view(cards=[("red", 5), ("yellow", 1), ("blue", 2), ("white", 3), ("green", 4)])
        v["legal"] += [hint("rank", 5), hint("color", "yellow")]
        self.assertEqual(choose(v, "group"), hint("rank", 5))
        v["partner"][1]["knowledge"] = knowledge(colors=("yellow",), ranks=(1,))
        self.assertEqual(choose(v, "group"), move("discard", 0))

    def test_at_clue_cap_never_force_an_unsafe_play_signal(self):
        v = view(cards=[("red", 3), ("blue", 2), ("yellow", 4), ("green", 4), ("red", 3)])
        v["legal"] = [move("play", i) for i in range(5)]
        v["legal"] += [hint("color", "red"), hint("rank", 3)]
        self.assertEqual(choose(v, "group"), hint("rank", 3))

    def test_no_clue_when_partner_has_no_turn_left(self):
        v = view()
        v["legal"] += [hint("rank", 1)]
        v["final_turns"] = 1
        self.assertEqual(choose(v, "group"), move("discard", 0))

    def test_hidden_own_cards_and_future_draw_order_cannot_change_action(self):
        game = Game(seed=12345)
        original_view = game.view(0)
        original_move = choose(original_view, "group")
        # Move actual copies, not merely redact identities. Partner, clues,
        # visible counts, and every other public field remain fixed.
        slot = game._hands[0][0]
        draw = next(i for i, c in enumerate(game._deck) if c != slot.card)
        self.assertIsInstance(game._deck[draw], Card)
        slot.card, game._deck[draw] = game._deck[draw], slot.card
        game._deck.reverse()
        self.assertEqual(original_view, game.view(0))
        self.assertEqual(original_move, choose(game.view(0), "group"))


if __name__ == "__main__":
    unittest.main()

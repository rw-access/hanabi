import unittest
from unittest.mock import patch

from hanabi.bots import choose
from hanabi.clue_probe import branch, hint, probe, snapshot
from hanabi.game import IllegalMove


class ClueProbeTests(unittest.TestCase):
    def test_replay_pair_has_one_history_change_and_same_world(self):
        plain, marked = snapshot(False), snapshot(True)
        # Evaluator-only access: both branches face exactly the same hidden deal.
        self.assertEqual(plain._deck, marked._deck)
        self.assertEqual([[s.card for s in h] for h in plain._hands],
                         [[s.card for s in h] for h in marked._hands])
        a, b = plain.view(0), marked.view(0)
        self.assertEqual([i + 1 for i, pair in enumerate(zip(a["history"], b["history"]))
                          if pair[0] != pair[1]], [11])
        for key in ("turn", "current", "clues", "lives", "remaining", "fireworks", "discard"):
            self.assertEqual(a[key], b[key])
        self.assertEqual((a["turn"], a["current"], a["clues"], a["lives"], a["remaining"]),
                         (12, 0, 1, 3, 36))
        self.assertEqual((a["fireworks"]["red"], a["fireworks"]["green"]), (1, 2))
        self.assertEqual(a["discard"], [{"color": "red", "rank": 3}])
        self.assertEqual([(c["color"], c["rank"]) for c in a["partner"][:2]],
                         [("red", 3), ("green", 3)])
        self.assertGreater(len(a["partner"][0]["knowledge"]["colors"]), 1)
        self.assertEqual(b["partner"][0]["knowledge"]["colors"], ["red"])

    def test_branches_and_natural_baseline(self):
        for case in probe()["cases"]:
            marked = case["prior_red"]
            view = snapshot(marked).view(0)
            natural = case["unforced_action"]
            self.assertEqual(natural, {"type": "discard", "index": 2})
            self.assertEqual(natural, choose(view, "lantern"))
            rows = case["branches"]
            hints = {b["intervention"]["value"]: b for b in rows if b["intervention"]["type"] == "hint"}
            self.assertEqual(set(hints), {a["value"] for a in view["legal"] if a["type"] == "hint"})
            self.assertEqual(len(rows), len(hints) + 1)
            self.assertEqual(rows[-1]["intervention"]["type"], "discard")
            self.assertEqual(hints[3]["intervention"]["matched"], [0, 1])
            self.assertEqual(hints["green"]["intervention"]["matched"], [1, 3])
            self.assertTrue(all(b["horizon_plies"] == 3 and b["end"] == "deck" for b in rows))
            expected = ((2, "in_hand", "played", 15), (2, "in_hand", "played", 15),
                        (2, "in_hand", "played", 18)) if marked else (
                            (2, "in_hand", "played", 20), (0, "discarded", "discarded", 14),
                            (2, "discarded", "discarded", 15))
            for clue, check in zip((3, "green", 2), expected):
                row = hints[clue]
                event = row["next_receiver_event"]
                self.assertEqual((event["player"], event["turn"], event["type"]), (1, 14, "discard"))
                self.assertEqual((event["index"], row["target_at_horizon"], row["target_at_end"],
                                  row["strict_score"]), check)

    def test_legal_intervention_and_branch_isolation(self):
        start = snapshot(False)
        before = (start.view(0), start.view(1), tuple(start._deck))
        three, green = hint("rank", 3), hint("color", "green")
        result = branch(start, three)
        branch(start, green)
        self.assertEqual(branch(start, three), result)
        with self.assertRaises(IllegalMove):
            branch(start, hint("color", "white"))
        self.assertEqual((start.view(0), start.view(1), tuple(start._deck)), before)

    def test_target_identity_after_slot_shift_and_fresh_policy_views(self):
        # Under green the receiver discards slot 0 then plays the ORIGINAL
        # green 3 from slot 0. Never mistake its new position for a new card.
        seen = []

        def policy(view, name):
            seen.append((view["seat"], view["turn"]))
            self.assertEqual(view["seat"], view["current"])
            self.assertTrue(all(set(card) == {"ranks", "colors"} for card in view["hand"]))
            return choose(view, name)

        with patch("hanabi.clue_probe.choose", side_effect=policy):
            row = branch(snapshot(False), hint("color", "green"), target_index=1)
        self.assertEqual(seen, [(turn % 2, turn) for turn in range(13, 13 + len(seen))])
        self.assertEqual((row["target_at_horizon"], row["target_at_end"]), ("played", "played"))
